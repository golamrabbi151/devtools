"""Server-side syntax highlighting.

Security model: every piece of user-derived text goes through markupsafe.escape()
before being wrapped in a <span class="...">. Class names come from a fixed set in
this file, never from input. The result is a Markup object, so templates render it
without `|safe` and without inline scripts/styles (colours live in app.css).
"""
import re

import sqlparse
from markupsafe import Markup, escape
from sqlparse import tokens as T

MAX_HIGHLIGHT = 300_000  # characters; bigger output falls back to plain text


def _span(cls: str, text: str) -> Markup:
    return Markup('<span class="{}">{}</span>').format(cls, text)


def _join(parts) -> Markup:
    return Markup("").join(parts)


# ---------------------------------------------------------------- JSON
_JSON_TOKEN = re.compile(
    r'(?P<str>"(?:\\.|[^"\\])*")(?P<colon>\s*:)?'
    r"|(?P<num>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"|(?P<lit>true|false|null)"
    r"|(?P<punct>[{}\[\],:])"
    r"|(?P<ws>\s+)"
    r"|(?P<other>.)",
    re.S,
)


def json_html(text: str) -> Markup | None:
    if len(text) > MAX_HIGHLIGHT:
        return None
    parts = []
    for m in _JSON_TOKEN.finditer(text):
        kind = m.lastgroup
        if m.group("str") is not None:
            if m.group("colon"):
                parts.append(_span("t-key", m.group("str")))
                parts.append(_span("t-punct", m.group("colon")))
            else:
                parts.append(_span("t-str", m.group("str")))
        elif kind == "num":
            parts.append(_span("t-num", m.group()))
        elif kind == "lit":
            parts.append(_span("t-lit", m.group()))
        elif kind == "punct":
            parts.append(_span("t-punct", m.group()))
        else:
            parts.append(escape(m.group()))
    return _join(parts)


# ---------------------------------------------------------------- XML
_XML_TOKEN = re.compile(
    r"(?P<cdata><!\[CDATA\[.*?\]\]>)"
    r"|(?P<comment><!--.*?-->)"
    r"|(?P<pi><\?.*?\?>)"
    r"|(?P<tag></?[^<>]+>)"
    r"|(?P<text>[^<]+)"
    r"|(?P<other>.)",
    re.S,
)
_XML_TAG = re.compile(r"^(</?)([^\s/>]+)(.*?)(/?>)$", re.S)
_XML_ATTR = re.compile(r"""([^\s=]+)(\s*=\s*)("[^"]*"|'[^']*')|(\s+)|(.)""", re.S)


def _xml_tag(tok: str) -> Markup:
    m = _XML_TAG.match(tok)
    if not m:
        return escape(tok)
    open_, name, attrs, close = m.groups()
    parts = [_span("t-punct", open_), _span("t-tag", name)]
    for a in _XML_ATTR.finditer(attrs):
        if a.group(1):
            parts += [_span("t-attr", a.group(1)), _span("t-punct", a.group(2)), _span("t-str", a.group(3))]
        else:
            parts.append(escape(a.group()))
    parts.append(_span("t-punct", close))
    return _join(parts)


def xml_html(text: str) -> Markup | None:
    if len(text) > MAX_HIGHLIGHT:
        return None
    parts = []
    for m in _XML_TOKEN.finditer(text):
        kind, tok = m.lastgroup, m.group()
        if kind == "tag":
            parts.append(_xml_tag(tok))
        elif kind in ("comment", "cdata", "pi"):
            parts.append(_span({"comment": "t-cmt", "cdata": "t-lit", "pi": "t-meta"}[kind], tok))
        else:
            parts.append(escape(tok))
    return _join(parts)


# ---------------------------------------------------------------- SQL
def _sql_class(ttype) -> str | None:
    if ttype in T.Comment:
        return "t-cmt"
    if ttype in T.String:
        return "t-str"
    if ttype in T.Number:
        return "t-num"
    if ttype in T.Keyword:
        return "t-kw"
    if ttype in T.Name.Builtin:
        return "t-lit"
    if ttype in T.Name:
        return "t-key"
    if ttype in T.Operator or ttype in T.Wildcard or ttype in T.Punctuation:
        return "t-punct"
    return None


def sql_html(text: str) -> Markup | None:
    if len(text) > MAX_HIGHLIGHT:
        return None
    parts = []
    for ttype, value in sqlparse.lexer.tokenize(text):
        cls = _sql_class(ttype)
        parts.append(_span(cls, value) if cls else escape(value))
    return _join(parts)


# ---------------------------------------------------------------- password
def password_html(text: str) -> Markup:
    """Colour digits and symbols so passwords are easier to read/transcribe."""
    parts = []
    for ch in text:
        if ch.isdigit():
            parts.append(_span("t-num", ch))
        elif ch.isalpha():
            parts.append(escape(ch))
        elif ch.isspace():
            parts.append(escape(ch))
        else:
            parts.append(_span("t-kw", ch))
    return _join(parts)


# ---------------------------------------------------------------- diff
_BADGES = {"added": ("d-add", "Added"), "removed": ("d-del", "Removed"), "changed": ("d-chg", "Changed")}
_DASH = Markup('<span class="muted">&mdash;</span>')


def diff_table_html(rows, counts: dict) -> Markup:
    """Side-by-side diff. rows: (kind, path, left_html|None, right_html|None); all text is escaped."""
    chips = _join(
        Markup('<span class="badge {}">{} {}</span>').format(_BADGES[k][0], counts.get(k, 0), _BADGES[k][1].lower())
        for k in ("added", "removed", "changed")
    )
    body = _join(
        Markup('<tr class="r-{}"><td><span class="badge {}">{}</span></td><td class="path">{}</td>'
               '<td class="val">{}</td><td class="val">{}</td></tr>').format(
            kind, _BADGES[kind][0], _BADGES[kind][1], path, left or _DASH, right or _DASH)
        for kind, path, left, right in rows
    )
    return Markup(
        '<div class="diff-summary">{}</div><div class="diff-wrap"><table class="diff"><thead><tr>'
        "<th>Type</th><th>Path</th><th>Left</th><th>Right</th></tr></thead><tbody>{}</tbody></table></div>"
    ).format(chips, body)
