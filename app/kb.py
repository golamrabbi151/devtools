"""Knowledge base of VA / PT findings and incidents with their solutions.

Storage is a single SQLite file (WAL mode, safe for several gunicorn workers on one host).
Search uses an FTS5 index kept in sync by triggers:
- ranked with bm25, title / reference (CVE, ticket id) weighted highest, then technology / tags,
  solution, asset, description, root cause;
- porter stemming ("injection" finds "injected") + prefix matching while typing ("log4" -> "log4j");
- "quoted phrases" match exactly; every user term is quoted, so FTS syntax can never be injected;
- if all-terms (AND) finds nothing, it falls back to any-term (OR) and says so.
All stored text is plain text and is escaped when rendered.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import date, datetime, timezone

from flask import current_app, g
from markupsafe import Markup, escape

KINDS = (("incident", "Incident"), ("va", "Vulnerability Assessment"), ("pt", "Penetration Test"))
SEVERITIES = (("critical", "Critical"), ("high", "High"), ("medium", "Medium"), ("low", "Low"), ("info", "Info"))
STATUSES = (("open", "Open"), ("in_progress", "In progress"), ("resolved", "Resolved"),
            ("mitigated", "Mitigated"), ("accepted", "Risk accepted"))
TECH_SUGGESTIONS = ("Java", "Spring", "Python", "Django", "PHP", "Laravel", "Node.js", ".NET", "Go", "JavaScript",
                    "React", "Angular", "Android", "iOS", "Linux", "Windows", "Active Directory", "Network", "Firewall",
                    "VPN", "DNS", "TLS/SSL", "Web server", "Nginx", "Apache", "IIS", "Tomcat", "Database", "MySQL",
                    "PostgreSQL", "Oracle", "SQL Server", "MongoDB", "Redis", "Docker", "Kubernetes", "AWS", "Azure",
                    "GCP", "Email", "Endpoint", "Backup", "IAM")
SORTS = (("relevance", "Best match"), ("newest", "Recently updated"), ("severity", "Severity"), ("occurred", "Date occurred"))

# field -> max length (plain text)
LIMITS = {"title": 300, "reference": 200, "technology": 400, "tags": 400, "asset": 400, "reported_by": 120,
          "description": 50_000, "root_cause": 50_000, "solution": 50_000, "links": 5_000}
TEXT_FIELDS = tuple(LIMITS)
PAGE_SIZE = 20
_MAX_TERMS = 12

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,
  title       TEXT NOT NULL,
  reference   TEXT NOT NULL DEFAULT '',
  severity    TEXT NOT NULL,
  status      TEXT NOT NULL,
  technology  TEXT NOT NULL DEFAULT '',
  tags        TEXT NOT NULL DEFAULT '',
  asset       TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  root_cause  TEXT NOT NULL DEFAULT '',
  solution    TEXT NOT NULL DEFAULT '',
  links       TEXT NOT NULL DEFAULT '',
  reported_by TEXT NOT NULL DEFAULT '',
  occurred_on TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS records_updated ON records(updated_at);
CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
  title, reference, technology, tags, asset, description, root_cause, solution,
  content='records', content_rowid='id', tokenize='porter unicode61', prefix='2 3'
);
CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
  INSERT INTO records_fts(rowid, title, reference, technology, tags, asset, description, root_cause, solution)
  VALUES (new.id, new.title, new.reference, new.technology, new.tags, new.asset, new.description, new.root_cause, new.solution);
END;
CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
  INSERT INTO records_fts(records_fts, rowid, title, reference, technology, tags, asset, description, root_cause, solution)
  VALUES ('delete', old.id, old.title, old.reference, old.technology, old.tags, old.asset, old.description, old.root_cause, old.solution);
END;
CREATE TRIGGER IF NOT EXISTS records_au AFTER UPDATE ON records BEGIN
  INSERT INTO records_fts(records_fts, rowid, title, reference, technology, tags, asset, description, root_cause, solution)
  VALUES ('delete', old.id, old.title, old.reference, old.technology, old.tags, old.asset, old.description, old.root_cause, old.solution);
  INSERT INTO records_fts(rowid, title, reference, technology, tags, asset, description, root_cause, solution)
  VALUES (new.id, new.title, new.reference, new.technology, new.tags, new.asset, new.description, new.root_cause, new.solution);
END;
"""
# bm25 weights, in FTS column order: title, reference, technology, tags, asset, description, root_cause, solution
_RANK = "bm25(records_fts, 10.0, 8.0, 5.0, 5.0, 2.0, 1.5, 2.0, 3.0)"
_SEV_ORDER = "CASE r.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END"
# Snippet markers: control characters cannot be typed into the form fields, so they are unambiguous.
_HL_OPEN, _HL_CLOSE = "\x02", "\x03"

_initialised: set[str] = set()
_init_lock = threading.Lock()


class KBError(Exception):
    """User-facing validation error."""


# ------------------------------------------------------------------ connection
def db_path() -> str:
    return current_app.config.get("KB_DB_PATH") or os.path.join(current_app.instance_path, "kb.sqlite3")


def get_db() -> sqlite3.Connection:
    if "kb_db" not in g:
        path = db_path()
        if path not in _initialised:
            with _init_lock:
                if path not in _initialised:
                    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                    con = sqlite3.connect(path)
                    con.execute("PRAGMA journal_mode=WAL")
                    con.executescript(_SCHEMA)
                    con.close()
                    _initialised.add(path)
        con = sqlite3.connect(path, timeout=5)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=5000")
        g.kb_db = con
    return g.kb_db


def close_db(_exc=None) -> None:
    con = g.pop("kb_db", None)
    if con is not None:
        con.close()


# ------------------------------------------------------------------ validation
def _clean(text: str, limit: int, label: str) -> str:
    # strip control chars except tab/newline (also keeps the snippet markers unambiguous)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", (text or "").replace("\r\n", "\n")).strip()
    if len(text) > limit:
        raise KBError(f"{label} is too long (max {limit:,} characters).")
    return text


def _list(text: str) -> str:
    """'java,  Spring ,java' -> 'java, Spring' (comma list, de-duplicated case-insensitively)."""
    seen, out = set(), []
    for item in text.split(","):
        item = " ".join(item.split())
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return ", ".join(out)


def validate(form) -> dict:
    labels = {"title": "Title", "reference": "Reference", "technology": "Technology", "tags": "Tags",
              "asset": "Affected asset", "reported_by": "Reported by", "description": "Description",
              "root_cause": "Root cause", "solution": "Solution", "links": "References / links"}
    rec = {f: _clean(form.get(f, ""), LIMITS[f], labels[f]) for f in TEXT_FIELDS}
    if not rec["title"]:
        raise KBError("Title is required.")
    for f in ("title", "reference", "asset", "reported_by"):
        rec[f] = " ".join(rec[f].split())
    rec["technology"] = _list(rec["technology"])
    rec["tags"] = _list(rec["tags"])
    for name, choices, label in (("kind", KINDS, "type"), ("severity", SEVERITIES, "severity"), ("status", STATUSES, "status")):
        val = form.get(name, "")
        if val not in dict(choices):
            raise KBError(f"Choose a valid {label}.")
        rec[name] = val
    occurred = (form.get("occurred_on") or "").strip()
    if occurred:
        try:
            occurred = date.fromisoformat(occurred).isoformat()
        except ValueError:
            raise KBError("Date occurred must be a valid date (YYYY-MM-DD).")
    rec["occurred_on"] = occurred or None
    return rec


# ------------------------------------------------------------------ CRUD
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


_COLUMNS = frozenset(TEXT_FIELDS) | {"kind", "severity", "status", "occurred_on"}


def _check_columns(rec: dict) -> None:
    if not set(rec) <= _COLUMNS:          # column names are interpolated below: allow-list only
        raise ValueError("unknown column")


def create(rec: dict) -> int:
    _check_columns(rec)
    con = get_db()
    now = _now()
    cols = list(rec) + ["created_at", "updated_at"]
    with con:
        cur = con.execute(f"INSERT INTO records ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",  # nosec B608 - allow-listed columns, bound values
                          [*rec.values(), now, now])
    return cur.lastrowid


def update(record_id: int, rec: dict) -> bool:
    _check_columns(rec)
    con = get_db()
    with con:
        cur = con.execute(f"UPDATE records SET {', '.join(f'{c} = ?' for c in rec)}, updated_at = ? WHERE id = ?",  # nosec B608 - allow-listed columns
                          [*rec.values(), _now(), record_id])
    return cur.rowcount == 1


def delete(record_id: int) -> bool:
    con = get_db()
    with con:
        return con.execute("DELETE FROM records WHERE id = ?", (record_id,)).rowcount == 1


def get(record_id: int) -> sqlite3.Row | None:
    return get_db().execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()


def technologies() -> list[str]:
    """Technologies in use (most used first), followed by the built-in suggestions."""
    counts: dict[str, list] = {}
    for (techs,) in get_db().execute("SELECT technology FROM records WHERE technology != ''"):
        for t in techs.split(", "):
            counts.setdefault(t.lower(), [t, 0])[1] += 1
    used = [name for name, _n in sorted(counts.values(), key=lambda x: (-x[1], x[0].lower()))]
    return used + [t for t in TECH_SUGGESTIONS if t.lower() not in counts]


def stats() -> dict:
    row = get_db().execute(
        "SELECT count(*) AS total, "
        "sum(status IN ('open', 'in_progress')) AS open, "
        "sum(severity = 'critical' AND status IN ('open', 'in_progress')) AS critical_open, "
        "sum(solution != '') AS solved FROM records").fetchone()
    return {k: row[k] or 0 for k in row.keys()}


# ------------------------------------------------------------------ search
_TERM = re.compile(r'"([^"]+)"|([^\s"]+)')


def build_match(query: str, mode: str = "AND") -> str | None:
    """User text -> safe FTS5 MATCH expression. Every term is a quoted string (no operators can
    be injected); bare words get prefix matching, "quoted phrases" match exactly."""
    terms = []
    for phrase, word in _TERM.findall(query or ""):
        if phrase:
            words = re.findall(r"\w+", phrase)
            if words:
                terms.append('"' + " ".join(words) + '"')
        else:
            words = re.findall(r"\w+", word)           # CVE-2021-44228 -> phrase "CVE 2021 44228"
            if words:
                terms.append('"' + " ".join(words) + '"*')
        if len(terms) >= _MAX_TERMS:
            break
    if not terms:
        return None
    return f" {mode} ".join(terms)


def _filters(params: dict) -> tuple[list[str], list]:
    where, args = [], []
    for name, choices in (("kind", KINDS), ("severity", SEVERITIES), ("status", STATUSES)):
        val = params.get(name, "")
        if val in dict(choices):
            where.append(f"r.{name} = ?")
            args.append(val)
        elif name == "status" and val == "unresolved":
            where.append("r.status IN ('open', 'in_progress')")
    tech = (params.get("tech") or "").strip().lower()
    if tech:
        where.append("instr(',' || replace(lower(r.technology), ', ', ',') || ',', ?) > 0")
        args.append("," + tech + ",")
    if params.get("has_solution") == "on":
        where.append("r.solution != ''")
    return where, args


def search(params: dict, page: int = 1) -> dict:
    """Returns {"rows", "total", "page", "pages", "fallback", "query"}; rows carry a highlighted snippet."""
    con = get_db()
    query = (params.get("q") or "").strip()[:300]
    sort = params.get("sort") or "relevance"
    where, args = _filters(params)
    page = max(1, page)
    fallback = False

    def run(match):
        w, a = list(where), list(args)
        if match:
            base = "FROM records_fts JOIN records r ON r.id = records_fts.rowid"
            w.insert(0, "records_fts MATCH ?")
            a.insert(0, match)
            snippet = f"snippet(records_fts, -1, '{_HL_OPEN}', '{_HL_CLOSE}', ' … ', 28)"
            title = f"highlight(records_fts, 0, '{_HL_OPEN}', '{_HL_CLOSE}')"
            rank = _RANK
        else:
            base = "FROM records r"
            snippet, title, rank = "substr(coalesce(nullif(r.description, ''), r.solution), 1, 220)", "r.title", "0"
        clause = (" WHERE " + " AND ".join(w)) if w else ""
        order = {
            "newest": "r.updated_at DESC",
            "severity": f"{_SEV_ORDER}, r.updated_at DESC",
            "occurred": "r.occurred_on IS NULL, r.occurred_on DESC, r.updated_at DESC",
        }.get(sort, f"{rank}, r.updated_at DESC" if match else "r.updated_at DESC")
        total = con.execute(f"SELECT count(*) {base}{clause}", a).fetchone()[0]
        rows = con.execute(
            f"SELECT r.id, r.kind, r.severity, r.status, r.technology, r.tags, r.reference, r.asset, "
            f"r.occurred_on, r.updated_at, r.solution != '' AS has_solution, "
            f"{title} AS title_hl, {snippet} AS snippet {base}{clause} ORDER BY {order} LIMIT ? OFFSET ?",
            a + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
        return total, rows

    match = build_match(query)
    try:
        total, rows = run(match)
        if match and total == 0 and " AND " in match:
            total, rows = run(build_match(query, "OR"))
            fallback = total > 0
    except sqlite3.OperationalError:            # defensive: should not happen with quoted terms
        current_app.logger.warning("kb search failed")
        total, rows = 0, []
    pages = max(1, -(-total // PAGE_SIZE))
    return {"rows": rows, "total": total, "page": min(page, pages), "pages": pages,
            "fallback": fallback, "query": query, "searched": bool(match)}


def related(rec: sqlite3.Row, limit: int = 5) -> list:
    """Records most similar to `rec` (title + technology + tags, any-term, best bm25 first)."""
    text = " ".join([rec["title"], rec["technology"].replace(",", " "), rec["tags"].replace(",", " ")])
    words = [w for w in dict.fromkeys(re.findall(r"\w{3,}", text.lower())) if w not in _STOP][:_MAX_TERMS]
    if not words:
        return []
    match = " OR ".join(f'"{w}"' for w in words)
    return get_db().execute(
        f"SELECT r.id, r.title, r.kind, r.severity, r.status, r.solution != '' AS has_solution "  # nosec B608 - constants only
        f"FROM records_fts JOIN records r ON r.id = records_fts.rowid "
        f"WHERE records_fts MATCH ? AND r.id != ? ORDER BY {_RANK} LIMIT ?",
        (match, rec["id"], limit)).fetchall()


_STOP = {"the", "and", "for", "with", "from", "this", "that", "into", "via", "are", "was", "not", "has", "have",
         "issue", "error", "server", "using", "when", "after", "before", "found"}


# ------------------------------------------------------------------ rendering helpers
def mark(text: str | None) -> Markup:
    """Escape text, then turn the snippet markers into <mark> tags."""
    safe = str(escape(text or ""))
    safe = safe.replace(_HL_OPEN, "<mark>").replace(_HL_CLOSE, "</mark>")
    # an unbalanced marker (snippet cut mid-term) must not leave an open tag
    if safe.count("<mark>") != safe.count("</mark>"):
        safe = safe.replace("<mark>", "").replace("</mark>", "")
    return Markup(safe)  # nosec B704 - built from escape() output plus our own <mark> tags


def label(choices, value: str) -> str:
    return dict(choices).get(value, value)
