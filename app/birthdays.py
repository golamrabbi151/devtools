"""Birthday celebrations for the home page.

Data source
- BIRTHDAY_API_URL set   -> GET that URL (JSON), cached for BIRTHDAY_CACHE_SECONDS.
- BIRTHDAY_API_URL empty -> built-in dummy users (also served at GET /api/birthdays, so the expected
                            JSON shape is visible and the real API can mirror it).

Expected JSON (field names are matched loosely, see _FIELD_NAMES):
  {"users": [{"id": 1, "name": "Ayesha Rahman", "designation": "Engineer", "department": "IT",
              "birthday": "1992-11-03", "image": "https://hr.example.com/photos/1.jpg"}]}
A bare list, or {"data": [...]}, also works. Birthdays may be "YYYY-MM-DD", "DD-MM-YYYY", "DD/MM/YYYY"
or "MM-DD". Only the day and month are shown: never the year or age.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from flask import current_app
from markupsafe import escape

WINDOW_DAYS = 7
TIMEOUT_S = 5
MAX_REPLY = 2 * 1024 * 1024
MAX_PEOPLE = 5000

WINDOWS = (("past", "Past 7 days"), ("yesterday", "Yesterday"), ("today", "Today"), ("next", "Next 7 days"))

WISHES = (
    "Wishing you a year full of wins, laughter and zero production incidents! 🎉",
    "May your code compile on the first try and your coffee never go cold. ☕",
    "Happy birthday! Here's to another year of brilliant ideas and great teamwork. 🚀",
    "Cheers to you! May this year bring health, happiness and big achievements. 🥂",
    "Another trip around the sun — may it be your best one yet! ☀️",
    "Happy birthday! Thanks for making every day at work a little brighter. 🌟",
    "May all your bugs be minor and all your releases smooth. Have a fantastic day! 🐞✨",
    "Sending you the warmest wishes and the biggest slice of cake. 🎂",
    "Happy birthday! May your year be as awesome as your best commit. 💚",
    "Here's to new adventures, new goals and lots of celebration. Enjoy your day! 🎈",
    "Wishing you joy that lasts all year and success in everything you do. 🌈",
    "Happy birthday to a true team player — have a wonderful celebration! 🙌",
    "May your day be filled with smiles, surprises and your favourite things. 🎁",
    "Level up! Wishing you an amazing new year of life. 🎮",
    "Happy birthday! May every dream on your roadmap ship this year. 🗺️",
    "Wishing you peace, happiness and lots of reasons to smile. 😊",
)


@dataclass(frozen=True)
class Celebrant:
    id: str
    name: str
    designation: str
    department: str
    month: int
    day: int
    image: str
    offset: int            # days from today: -7..+7 (0 = today)
    occurs_on: date
    wish: str
    theme: int             # 0..5, picks the card colour scheme

    @property
    def window(self) -> str:
        if self.offset == 0:
            return "today"
        if self.offset == -1:
            return "yesterday"
        return "past" if self.offset < 0 else "next"

    @property
    def windows(self) -> str:
        """Space-separated tabs this card belongs to (yesterday is also part of the past 7 days)."""
        return "yesterday past" if self.offset == -1 else self.window

    @property
    def when(self) -> str:
        return {0: "Today", -1: "Yesterday", 1: "Tomorrow"}.get(
            self.offset, f"In {self.offset} days" if self.offset > 0 else f"{-self.offset} days ago")

    @property
    def initials(self) -> str:
        parts = [p for p in self.name.split() if p[:1].isalnum()]
        return "".join(p[0] for p in (parts[:1] + parts[-1:] if len(parts) > 1 else parts)).upper() or "?"


# ------------------------------------------------------------------ dummy data
_DUMMY = (
    ("Ayesha Rahman", "Senior Software Engineer", "Engineering", 0),
    ("Tanvir Hasan", "Network Administrator", "IT Infrastructure", 0),
    ("Nusrat Jahan", "Security Analyst", "Information Security", 0),
    ("Rafiq Ahmed", "DevOps Engineer", "Platform", -1),
    ("Farzana Akter", "QA Lead", "Quality Assurance", -3),
    ("Imran Chowdhury", "Database Administrator", "Data", -5),
    ("Sadia Islam", "UI/UX Designer", "Product", -7),
    ("Mahmudul Karim", "Java Developer", "Engineering", 1),
    ("Priya Das", "Project Manager", "PMO", 2),
    ("Arif Hossain", "PHP Developer", "Engineering", 4),
    ("Lamia Sultana", "HR Business Partner", "Human Resources", 6),
    ("Kamrul Islam", "System Administrator", "IT Infrastructure", 7),
    ("Sharmin Nahar", "Python Developer", "Data", 12),       # outside every window: must not appear
)


def dummy_users(today: date | None = None) -> list[dict]:
    """Fake HR records whose birthdays fall around `today`, so every tab has something to show."""
    today = today or date.today()
    users = []
    for i, (name, designation, dept, offset) in enumerate(_DUMMY, start=1):
        d = today + timedelta(days=offset)
        year = 1985 + (i * 3) % 15
        try:
            bday = d.replace(year=year)
        except ValueError:                       # 29 Feb in a non-leap year
            bday = date(year, 2, 28)
        users.append({"id": i, "name": name, "designation": designation, "department": dept,
                      "birthday": bday.isoformat(), "image": f"/api/birthdays/avatar/{i}.svg"})
    return users


_PALETTE = (("#12685d", "#3fb68b"), ("#7a3fc4", "#c86dd7"), ("#c0245a", "#f27a54"),
            ("#0b5c8f", "#38b6e0"), ("#b24a00", "#f2b134"), ("#3b4cca", "#7f8cf7"))


def avatar_svg(user_id: int) -> str | None:
    """Initials avatar for the dummy users (our own SVG: escaped text, no scripts)."""
    if not 1 <= user_id <= len(_DUMMY):
        return None
    name = _DUMMY[user_id - 1][0]
    initials = "".join(p[0] for p in name.split()[:2]).upper()
    a, b = _PALETTE[user_id % len(_PALETTE)]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" width="120" height="120">'
            f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{a}"/><stop offset="1" stop-color="{b}"/></linearGradient></defs>'
            f'<rect width="120" height="120" fill="url(#g)"/>'
            f'<circle cx="96" cy="22" r="30" fill="#ffffff" fill-opacity=".12"/>'
            f'<text x="60" y="74" text-anchor="middle" font-family="Segoe UI, Roboto, sans-serif" '
            f'font-size="44" font-weight="700" fill="#ffffff">{escape(initials)}</text></svg>')


# ------------------------------------------------------------------ real API
_cache: dict = {"at": 0.0, "url": "", "users": None}
_cache_lock = threading.Lock()


class BirthdayUnavailable(Exception):
    pass


def _fetch(url: str) -> list:
    if not url.lower().startswith(("https://", "http://")):
        raise BirthdayUnavailable("BIRTHDAY_API_URL must be an http(s) URL")
    headers = {"Accept": "application/json"}
    token = current_app.config.get("BIRTHDAY_API_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:   # nosec B310 - scheme checked above
            raw = resp.read(MAX_REPLY + 1)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise BirthdayUnavailable(type(e).__name__)
    if len(raw) > MAX_REPLY:
        raise BirthdayUnavailable("reply too large")
    try:
        data = json.loads(raw)
    except ValueError:
        raise BirthdayUnavailable("reply is not JSON")
    if isinstance(data, dict):
        data = next((data[k] for k in ("users", "data", "employees", "items", "result") if isinstance(data.get(k), list)), None)
    if not isinstance(data, list):
        raise BirthdayUnavailable("unexpected JSON shape")
    return data[:MAX_PEOPLE]


def load_users() -> list:
    url = current_app.config.get("BIRTHDAY_API_URL", "")
    if not url:
        return dummy_users()
    ttl = current_app.config.get("BIRTHDAY_CACHE_SECONDS", 600)
    with _cache_lock:
        if _cache["users"] is not None and _cache["url"] == url and time.monotonic() - _cache["at"] < ttl:
            return _cache["users"]
    users = _fetch(url)
    with _cache_lock:
        _cache.update(at=time.monotonic(), url=url, users=users)
    return users


# ------------------------------------------------------------------ normalising
_FIELD_NAMES = {
    "id": ("id", "userId", "user_id", "employeeId", "employee_id", "empId", "staffId"),
    "name": ("name", "fullName", "full_name", "displayName", "employeeName", "userName", "username"),
    "designation": ("designation", "title", "jobTitle", "job_title", "position", "role"),
    "department": ("department", "dept", "division", "team", "unit"),
    "birthday": ("birthday", "birthDate", "birth_date", "dateOfBirth", "date_of_birth", "dob"),
    "image": ("image", "imageUrl", "image_url", "photo", "photoUrl", "photo_url", "avatar", "picture"),
}


def _pick(rec: dict, field: str) -> str:
    for key in _FIELD_NAMES[field]:
        val = rec.get(key)
        if val not in (None, ""):
            return str(val).strip()
    return ""


def parse_birthday(text: str) -> tuple[int, int] | None:
    """-> (month, day) or None. Accepts ISO dates/datetimes, DD-MM-YYYY, DD/MM/YYYY, MM-DD."""
    text = (text or "").strip()[:40]
    if re.match(r"\d{4}-\d{2}-\d{2}[T ]", text):          # ISO datetime: keep the date part
        text = text[:10]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y", "%d %b %Y", "%d %B %Y"):
        try:
            d = datetime.strptime(text, fmt)
            return d.month, d.day
        except ValueError:
            continue
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", text)          # MM-DD (no year)
    if m:
        month, day = int(m[1]), int(m[2])
        try:
            date(2000, month, day)                            # leap year, so 02-29 is valid
            return month, day
        except ValueError:
            return None
    return None


def _safe_image(url: str) -> str:
    """Only same-site paths or http(s) URLs (never javascript:/data: from the API)."""
    if url.startswith("/") and not url.startswith("//"):
        return url
    if url.lower().startswith(("https://", "http://")):
        return url
    return ""


def _occurrence(month: int, day: int, today: date) -> tuple[int, date] | None:
    """Nearest occurrence within +-WINDOW_DAYS of today (29 Feb is celebrated on 28 Feb in other years)."""
    for offset in range(-WINDOW_DAYS, WINDOW_DAYS + 1):
        d = today + timedelta(days=offset)
        m, dd = (month, day)
        if (m, dd) == (2, 29) and not _is_leap(d.year):
            dd = 28
        if (d.month, d.day) == (m, dd):
            return offset, d
    return None


def _is_leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def _wish(key: str, today: date) -> tuple[str, int]:
    """Random-looking but stable for the whole day (the card doesn't change on every refresh)."""
    seed = int(hashlib.sha256(f"{key}|{today.isoformat()}".encode()).hexdigest()[:12], 16)
    rng = random.Random(seed)  # nosec B311 - picking a greeting, not security-sensitive
    return rng.choice(WISHES), rng.randrange(len(_PALETTE))


def celebrants(users: list, today: date | None = None) -> list[Celebrant]:
    today = today or date.today()
    out = []
    for rec in users:
        if not isinstance(rec, dict):
            continue
        name = " ".join(_pick(rec, "name").split())[:80]
        md = parse_birthday(_pick(rec, "birthday"))
        if not name or not md:
            continue
        hit = _occurrence(*md, today)
        if hit is None:
            continue
        offset, occurs = hit
        uid = _pick(rec, "id")[:64] or name
        wish, theme = _wish(uid, today)
        out.append(Celebrant(id=uid, name=name, designation=_pick(rec, "designation")[:80],
                             department=_pick(rec, "department")[:80], month=md[0], day=md[1],
                             image=_safe_image(_pick(rec, "image"))[:500], offset=offset, occurs_on=occurs,
                             wish=wish, theme=theme))
    # today first, then upcoming (soonest first), then past (most recent first)
    return sorted(out, key=lambda c: (0 if c.offset == 0 else 1 if c.offset > 0 else 2, abs(c.offset), c.name))


def for_home() -> dict:
    """Everything the home page needs; never raises (the page must render even if the API is down)."""
    try:
        people = celebrants(load_users())
        error = None
    except BirthdayUnavailable as e:
        current_app.logger.warning("birthday API unavailable: %s", e)
        people, error = [], "Birthdays are not available right now."
    counts = {key: sum(1 for p in people if key in p.windows.split()) for key, _ in WINDOWS}
    default = "today" if counts["today"] else "next" if counts["next"] else "past" if counts["past"] else "today"
    return {"people": people, "counts": counts, "default": default, "error": error,
            "dummy": not current_app.config.get("BIRTHDAY_API_URL")}
