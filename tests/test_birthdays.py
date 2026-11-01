"""Birthday cards on the home page: date windows, API parsing, dummy API, rendering, safety."""
import io
import json
import re
from datetime import date

import pytest

from app import birthdays as bd


def people(users, today):
    return {p.name: p for p in bd.celebrants(users, today)}


# ------------------------------------------------------------------ date windows
def test_windows_around_today():
    today = date(2026, 6, 15)
    users = [{"name": f"P{o:+d}", "birthday": f"1990-06-{15 + o:02d}"} for o in (-8, -7, -2, -1, 0, 1, 7, 8)]
    got = people(users, today)
    assert set(got) == {"P-7", "P-2", "P-1", "P+0", "P+1", "P+7"}               # +-8 days are outside
    assert got["P+0"].window == "today" and got["P+0"].when == "Today"
    assert got["P-1"].windows == "yesterday past" and got["P-1"].when == "Yesterday"
    assert got["P-2"].window == "past" and got["P-2"].when == "2 days ago"
    assert got["P+1"].when == "Tomorrow" and got["P+7"].when == "In 7 days" and got["P+7"].window == "next"
    order = [p.name for p in bd.celebrants(users, today)]
    assert order == ["P+0", "P+1", "P+7", "P-1", "P-2", "P-7"]                   # today, upcoming, recent past


def test_year_boundary_and_leap_day():
    got = people([{"name": "NewYear", "birthday": "1990-01-02"}, {"name": "Eve", "birthday": "1990-12-28"}],
                 date(2026, 12, 30))
    assert got["NewYear"].offset == 3 and got["NewYear"].occurs_on == date(2027, 1, 2)
    assert got["Eve"].offset == -2
    leap = people([{"name": "Leap", "birthday": "1992-02-29"}], date(2027, 2, 28))   # non-leap year: 28 Feb
    assert leap["Leap"].offset == 0
    assert people([{"name": "Leap", "birthday": "1992-02-29"}], date(2028, 2, 29))["Leap"].offset == 0


@pytest.mark.parametrize("text,expected", [
    ("1990-11-03", (11, 3)), ("1990-11-03T00:00:00Z", (11, 3)), ("03-11-1990", (11, 3)), ("03/11/1990", (11, 3)),
    ("1990/11/03", (11, 3)), ("3 Nov 1990", (11, 3)), ("11-03", (11, 3)), ("02-29", (2, 29)),
    ("", None), ("not a date", None), ("13-45", None), ("1990-02-30", None)])
def test_parse_birthday(text, expected):
    assert bd.parse_birthday(text) == expected


def test_loose_field_names_and_bad_rows():
    today = date(2026, 3, 10)
    users = [{"employeeId": 7, "fullName": "  Nadia   Karim ", "dob": "10/03/1991", "jobTitle": "Engineer",
              "dept": "IT", "photoUrl": "https://hr.example.com/7.jpg"},
             {"name": "No date"}, {"birthday": "1990-03-10"}, "junk", None, {"name": "Bad", "birthday": "x"}]
    (p,) = bd.celebrants(users, today)
    assert (p.id, p.name, p.designation, p.department, p.image) == ("7", "Nadia Karim", "Engineer", "IT",
                                                                     "https://hr.example.com/7.jpg")
    assert p.initials == "NK"


@pytest.mark.parametrize("url,ok", [("https://x.example/a.png", True), ("/api/birthdays/avatar/1.svg", True),
                                    ("javascript:alert(1)", False), ("data:image/svg+xml,<svg onload=1>", False),
                                    ("//evil.example/x.png", False), ("ftp://x/y", False)])
def test_image_urls_are_restricted(url, ok):
    assert (bd._safe_image(url) == url) is ok


def test_wish_is_stable_for_the_day_and_varies():
    u = [{"id": i, "name": f"N{i}", "birthday": "1990-05-05"} for i in range(30)]
    a, b = bd.celebrants(u, date(2026, 5, 5)), bd.celebrants(u, date(2026, 5, 5))
    assert [p.wish for p in a] == [p.wish for p in b]
    assert len({p.wish for p in a}) > 5 and all(p.wish in bd.WISHES for p in a)


# ------------------------------------------------------------------ dummy API + real API client
def test_dummy_api_and_avatar(client):
    r = client.get("/api/birthdays")
    users = r.get_json()["users"]
    assert r.status_code == 200 and len(users) == len(bd._DUMMY)
    assert {"id", "name", "designation", "department", "birthday", "image"} <= set(users[0])
    found = bd.celebrants(users)
    assert {p.window for p in found} == {"past", "yesterday", "today", "next"}
    assert "Sharmin Nahar" not in {p.name for p in found}                         # 12 days away
    svg = client.get("/api/birthdays/avatar/1.svg")
    assert svg.status_code == 200 and svg.mimetype == "image/svg+xml" and b">AR</text>" in svg.data
    assert b"<script" not in svg.data
    assert client.get("/api/birthdays/avatar/999.svg").status_code == 404


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_real_api_is_used_cached_and_failure_tolerant(app, client, monkeypatch):
    today = date.today().isoformat()
    calls = []

    def fake_urlopen(req, timeout):
        calls.append((req.full_url, req.get_header("Authorization")))
        return _Resp(json.dumps({"data": [{"id": 1, "name": "Real Person", "birthday": today}]}).encode())
    monkeypatch.setattr(bd.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setitem(bd._cache, "users", None)
    app.config.update(BIRTHDAY_API_URL="https://hr.example.com/birthdays", BIRTHDAY_API_TOKEN="tok")
    page = client.get("/").get_data(as_text=True)
    assert "Real Person" in page and "Ayesha Rahman" not in page and "sample data" not in page
    client.get("/")
    assert calls == [("https://hr.example.com/birthdays", "Bearer tok")]           # second load from cache

    def down(req, timeout):
        raise bd.urllib.error.URLError("down")
    monkeypatch.setattr(bd.urllib.request, "urlopen", down)
    monkeypatch.setitem(bd._cache, "users", None)
    r = client.get("/")
    assert r.status_code == 200 and "Birthdays are not available right now" in r.get_data(as_text=True)


# ------------------------------------------------------------------ home page
def test_home_page_shows_cards_tabs_and_tools(client):
    html = client.get("/").get_data(as_text=True)
    assert "Developer tools" in html and "Birthday celebrations" in html
    for label in ("Past 7 days", "Yesterday", "Today", "Next 7 days"):
        assert label in html
    assert html.count('class="bd-card') == 12 and html.count("is-today") == 3
    assert re.search(r'data-bd-tab="today"\s+aria-selected="true"', html)
    assert html.count('class="tool-card"') >= 15 and "Security Knowledge Base" in html
    assert "sample data" in html
    assert not re.search(r"\sstyle\s*=", html) and not re.search(r"\son[a-z]+\s*=", html)


def test_home_escapes_api_data(app, client, monkeypatch):
    evil = "<img src=x onerror=alert(1)>"
    monkeypatch.setattr(bd, "load_users", lambda: [{"name": evil, "designation": evil, "birthday": date.today().isoformat(),
                                                    "image": 'https://x.example/a.png" onerror="alert(1)'}])
    html = client.get("/").get_data(as_text=True)
    assert "<img src=x" not in html and 'onerror="alert' not in html and "&lt;img src=x" in html


def test_csp_can_allow_photo_origin():
    from app.security import build_csp
    assert "img-src 'self' data: https://hr.example.com;" in build_csp("https://hr.example.com javascript:x http://y")
    assert build_csp("") == build_csp("not-an-origin")
