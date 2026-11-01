"""Security knowledge base: public reading, AD sign-in + 30 min session, CRUD, search, escaping."""
import html
import io
import json
import re
import sqlite3
import time
import urllib.error

import pytest

from app import kb, kb_auth, kb_routes

USER, PW = "rabbi", "s3cret!"


def token(client, path="/kb"):
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


@pytest.fixture()
def ad(monkeypatch):
    """Fake AD login API: accepts USER / PW only. Records every call."""
    calls = []

    def fake(user, password):
        calls.append((user, password))
        return user == USER and password == PW
    monkeypatch.setattr(kb_auth, "authenticate", fake)
    return calls


def sign_in(client, user=USER, password=PW, **extra):
    return client.post("/kb/login", data={"username": user, "password": password,
                                          "csrf_token": token(client, "/kb/login"), **extra})


@pytest.fixture()
def kbc(client, ad):
    """Signed-in client."""
    assert sign_in(client).status_code == 302
    return client


def save(c, **fields):
    data = {"kind": "incident", "severity": "high", "status": "resolved", "csrf_token": token(c)}
    data.update(fields)
    return c.post("/kb/save", data=data, headers={"HX-Request": "true", "HX-Target": "kb-modal-body"})


def listing(c, **params):
    return c.get("/kb", query_string=params,
                 headers={"HX-Request": "true", "HX-Target": "kb-results"}).get_data(as_text=True)


def detail(c, rid):
    return c.get(f"/kb/{rid}", headers={"HX-Request": "true", "HX-Target": "kb-modal-body"}).get_data(as_text=True)


def titles(page):
    return [html.unescape(re.sub(r"<[^>]+>", "", t)).strip()
            for t in re.findall(r'<div class="kb-title">(.*?)(?:<span class="kb-ref">|</div>)', page, re.S)]


@pytest.fixture()
def seeded(kbc):
    save(kbc, title="Log4Shell RCE on payment API", reference="CVE-2021-44228", kind="va", severity="critical",
         technology="Java, Log4j", tags="rce", description="JNDI lookup in log messages",
         solution="Upgrade log4j-core to 2.17.1 and set log4j2.formatMsgNoLookups=true")
    save(kbc, title="SQL injection in login form", kind="pt", severity="high", technology="PHP, MySQL",
         description="The username parameter is concatenated into the query",
         solution="Use prepared statements (PDO) with bound parameters")
    save(kbc, title="Site-to-site VPN tunnel dropping", kind="incident", severity="medium", status="open",
         technology="Network, VPN", description="IPsec phase 2 renegotiation fails every 8 hours")
    save(kbc, title="Django debug page exposed", kind="va", severity="medium", technology="Python, Django",
         solution="Set DEBUG = False in production settings")
    return kbc


# ------------------------------------------------------------------ public reading
def test_records_are_public_but_read_only(seeded, app):
    anon = app.test_client()
    page = anon.get("/kb").get_data(as_text=True)
    assert "Sign in to add records" in page and "+ New record" not in page and "Sign out" not in page
    assert len(titles(listing(anon, q="log4"))) == 1                       # search works without sign-in
    d = detail(anon, 1)
    assert "Log4Shell" in d and "log4j-core to 2.17.1" in d
    assert ">Edit<" not in d and ">Delete<" not in d and "by rabbi" in d   # author shown, no edit controls


def test_writes_require_sign_in(client, ad):
    assert client.get("/kb").status_code == 200
    assert client.get("/kb/new").headers["Location"] == "/kb/login"
    r = client.get("/kb/new", headers={"HX-Request": "true"})
    assert r.status_code == 401 and r.headers["HX-Redirect"] == "/kb/login"
    t = token(client, "/kb/login")
    assert client.post("/kb/save", data={"title": "x", "csrf_token": t}).status_code == 302
    assert client.post("/kb/1/delete", data={"csrf_token": t}).status_code == 302
    assert client.get("/kb/1/edit").status_code == 302


# ------------------------------------------------------------------ sign-in
def test_sign_in_shows_user_and_sign_out(client, ad):
    r = sign_in(client)
    assert r.status_code == 302 and r.headers["Location"] == "/kb"
    assert ad == [(USER, PW)]
    page = client.get("/kb").get_data(as_text=True)
    assert "Signed in as <strong>rabbi</strong>" in page and "Sign out" in page and "+ New record" in page
    assert 'data-expires="' in page
    r = client.post("/kb/logout", data={"csrf_token": token(client)})
    assert r.headers["Location"] == "/kb"
    assert "Sign in to add records" in client.get("/kb").get_data(as_text=True)


def test_wrong_password_and_open_redirect(client, ad):
    r = sign_in(client, password="nope")
    body = r.get_data(as_text=True)
    assert r.status_code == 401 and "Invalid user name or password" in body
    assert 'value="rabbi"' in body and "nope" not in body                 # user name kept, password never echoed
    assert sign_in(client, password="").status_code == 400
    assert sign_in(client, next="//evil.example").headers["Location"] == "/kb"


def test_auth_service_down(client, monkeypatch):
    def down(user, password):
        raise kb_auth.AuthUnavailable("URLError")
    monkeypatch.setattr(kb_auth, "authenticate", down)
    r = sign_in(client)
    assert r.status_code == 503 and "not reachable" in r.get_data(as_text=True)


def test_session_expires_after_30_minutes(kbc, monkeypatch):
    assert kbc.get("/kb/new").status_code == 200
    real = time.time
    monkeypatch.setattr(kb_routes.time, "time", lambda: real() + 29 * 60)
    assert kbc.get("/kb/new").status_code == 200
    monkeypatch.setattr(kb_routes.time, "time", lambda: real() + 30 * 60 + 1)
    assert kbc.get("/kb/new").status_code == 302                          # signed out
    assert "Sign in to add records" in kbc.get("/kb").get_data(as_text=True)
    monkeypatch.setattr(kb_routes.time, "time", real)
    assert kbc.get("/kb/new").status_code == 302                          # stays signed out


def test_sign_in_disabled_without_api_url(app, client):
    app.config["KB_AUTH_URL"] = ""
    assert client.get("/kb").status_code == 200                           # reading still works
    r = client.get("/kb/login")
    assert r.status_code == 503 and "KB_AUTH_URL" in r.get_data(as_text=True)


# ------------------------------------------------------------------ the AD API client
class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


@pytest.fixture()
def api(app, monkeypatch):
    sent = {}

    def install(reply=None, exc=None):
        def fake_open(req, timeout):
            sent.update(body=json.loads(req.data), headers=dict(req.header_items()), timeout=timeout)
            if exc:
                raise exc
            return _Resp(reply if isinstance(reply, bytes) else json.dumps(reply).encode())
        monkeypatch.setattr(kb_auth._opener, "open", fake_open)
        return sent
    with app.app_context():
        yield install


def test_api_success_and_request_shape(api):
    sent = api({"responseCode": 100, "isValidUser": 1})
    assert kb_auth.authenticate("rabbi", "pw") is True
    assert sent["body"] == {"userId": "rabbi", "password": "pw", "channelCode": "USERCODE"}
    assert sent["headers"]["Content-type"] == "application/json" and sent["timeout"] == kb_auth.TIMEOUT_S
    api({"responseCode": "100", "isValidUser": "1"})                      # string values are fine too
    assert kb_auth.authenticate("rabbi", "pw") is True


@pytest.mark.parametrize("reply", [{"responseCode": 100, "isValidUser": 0}, {"responseCode": 101, "isValidUser": 1},
                                   {"isValidUser": 1}, {}])
def test_api_rejections(api, reply):
    api(reply)
    assert kb_auth.authenticate("rabbi", "pw") is False


def test_api_failures_are_reported_as_unavailable(api):
    for kwargs in ({"reply": b"<html>gateway error</html>"}, {"reply": [1, 2]},
                   {"exc": urllib.error.URLError("down")}, {"exc": TimeoutError()},
                   {"exc": urllib.error.HTTPError("u", 502, "Bad Gateway", {}, io.BytesIO(b"{}"))}):
        api(**kwargs)
        with pytest.raises(kb_auth.AuthUnavailable):
            kb_auth.authenticate("rabbi", "pw")
    api(exc=urllib.error.HTTPError("u", 401, "Unauthorized", {},
                                   io.BytesIO(b'{"responseCode": 101, "isValidUser": 0}')))
    assert kb_auth.authenticate("rabbi", "pw") is False                   # 4xx with a JSON verdict


def test_api_requires_https(api, app):
    api({"responseCode": 100, "isValidUser": 1})
    app.config["KB_AUTH_URL"] = "http://login.example/user"
    with pytest.raises(kb_auth.AuthUnavailable):
        kb_auth.authenticate("rabbi", "pw")


def test_api_does_not_follow_redirects():
    assert kb_auth._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://elsewhere") is None


# ------------------------------------------------------------------ audit + migration
def test_created_and_updated_by(kbc):
    save(kbc, title="Open SMB share")
    d = detail(kbc, 1)
    assert "by rabbi" in d and "Last edited" not in d


def test_old_database_gets_new_columns(app, tmp_path):
    path = tmp_path / "old.sqlite3"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, "
                "reference TEXT NOT NULL DEFAULT '', severity TEXT NOT NULL, status TEXT NOT NULL, "
                "technology TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '', asset TEXT NOT NULL DEFAULT '', "
                "description TEXT NOT NULL DEFAULT '', root_cause TEXT NOT NULL DEFAULT '', "
                "solution TEXT NOT NULL DEFAULT '', links TEXT NOT NULL DEFAULT '', "
                "reported_by TEXT NOT NULL DEFAULT '', occurred_on TEXT, created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL)")
    con.close()
    app.config["KB_DB_PATH"] = str(path)
    with app.test_request_context():
        cols = {r[1] for r in kb.get_db().execute("PRAGMA table_info(records)")}
    assert {"created_by", "updated_by"} <= cols


def test_csrf_required(kbc):
    r = kbc.post("/kb/save", data={"title": "x", "kind": "va", "severity": "low", "status": "open"})
    assert r.status_code == 400


# ------------------------------------------------------------------ CRUD
def test_create_view_edit_delete(kbc):
    r = save(kbc, title="Weak TLS ciphers", technology="nginx,  TLS/SSL, Nginx", tags="tls",
             solution="ssl_ciphers HIGH:!aNULL")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "Saved." in body and "kb-changed" in r.headers["HX-Trigger"]
    rid = int(re.search(r"#(\d+)</span>", body).group(1))
    d = detail(kbc, rid)
    assert "Weak TLS ciphers" in d and "ssl_ciphers HIGH:!aNULL" in d and "<html" not in d
    assert ">nginx<" in d and ">TLS/SSL<" in d and ">Nginx<" not in d          # de-duplicated list
    full = kbc.get(f"/kb/{rid}").get_data(as_text=True)                        # direct link: page + open modal
    assert "data-autoopen" in full and "Weak TLS ciphers" in full

    body = save(kbc, id=str(rid), title="Weak TLS ciphers on edge", status="mitigated").get_data(as_text=True)
    assert "Weak TLS ciphers on edge" in body and "Mitigated" in body

    r = kbc.post(f"/kb/{rid}/delete", data={"csrf_token": token(kbc)}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "kb-close" in r.headers["HX-Trigger"]
    assert kbc.get(f"/kb/{rid}").status_code == 404


def test_validation_errors(kbc):
    assert "Title is required" in save(kbc, title="  ").get_data(as_text=True)
    assert "valid severity" in save(kbc, title="x", severity="extreme").get_data(as_text=True)
    assert "valid date" in save(kbc, title="x", occurred_on="2026-13-40").get_data(as_text=True)
    assert "too long" in save(kbc, title="x" * 301).get_data(as_text=True)
    assert "keep me" in save(kbc, title="", solution="keep me").get_data(as_text=True)   # input is kept


# ------------------------------------------------------------------ search
def test_search_ranking_and_prefix(seeded):
    assert titles(listing(seeded, q="log4"))[0] == "Log4Shell RCE on payment API"          # prefix while typing
    assert titles(listing(seeded, q="CVE-2021-44228")) == ["Log4Shell RCE on payment API"]  # reference / CVE
    assert titles(listing(seeded, q="prepared statements")) == ["SQL injection in login form"]  # solution text
    assert titles(listing(seeded, q="injected"))[0] == "SQL injection in login form"        # stemming
    assert "<mark>IPsec</mark>" in listing(seeded, q="ipsec")                               # highlighted snippet


def test_search_falls_back_to_any_word(seeded):
    page = listing(seeded, q="django kubernetes")
    assert titles(page) == ["Django debug page exposed"] and "match <em>some</em>" in page
    assert "Nothing found" in listing(seeded, q="zzzqqq")


def test_filters_and_sort(seeded):
    assert titles(listing(seeded, tech="java")) == ["Log4Shell RCE on payment API"]
    assert titles(listing(seeded, tech="network")) == ["Site-to-site VPN tunnel dropping"]
    assert set(titles(listing(seeded, kind="va"))) == {"Log4Shell RCE on payment API", "Django debug page exposed"}
    assert titles(listing(seeded, status="unresolved")) == ["Site-to-site VPN tunnel dropping"]
    assert "Site-to-site VPN tunnel dropping" not in titles(listing(seeded, has_solution="on"))
    assert titles(listing(seeded, sort="severity"))[0] == "Log4Shell RCE on payment API"


@pytest.mark.parametrize("q", ['"', "NEAR(", "a OR", "* -x", "title:foo", "^x",
                               "'; DROP TABLE records; --", "(((", "\\"])
def test_search_syntax_cannot_break_or_inject(seeded, q):
    r = seeded.get("/kb", query_string={"q": q}, headers={"HX-Request": "true", "HX-Target": "kb-results"})
    assert r.status_code == 200
    assert len(titles(listing(seeded))) == 4                                                # table intact


def test_build_match_quotes_every_term():
    assert kb.build_match('log4j "sql injection" C#') == '"log4j"* AND "sql injection" AND "C"*'
    assert kb.build_match("") is None and kb.build_match("-- ;") is None


def test_related_records(seeded):
    save(seeded, title="Log4j vulnerable version in reporting service", technology="Java")
    rid = re.search(r'href="/kb/(\d+)"', listing(seeded, q="reporting")).group(1)
    d = detail(seeded, rid)
    assert "Related records" in d and "Log4Shell RCE on payment API" in d


# ------------------------------------------------------------------ output safety
def test_stored_text_is_escaped(kbc):
    evil = "<script>alert(1)</script><img src=x onerror=alert(2)>"
    save(kbc, title=evil, description=evil, solution=evil, technology=evil,
         links="javascript:alert(1)\nhttps://ok.example/a")
    for body in (kbc.get("/kb").get_data(as_text=True), detail(kbc, 1), listing(kbc, q="script alert")):
        assert "<script>alert" not in body and "<img src=x" not in body
    d = detail(kbc, 1)
    assert 'href="javascript:' not in d and 'href="https://ok.example/a"' in d


def test_mark_never_leaves_unbalanced_tags():
    assert str(kb.mark("a\x02b")) == "ab"
    assert str(kb.mark("<b>\x02x\x03")) == "&lt;b&gt;<mark>x</mark>"


def test_pages_have_no_inline_script_or_style(kbc):
    save(kbc, title="x", solution="y")
    for path in ("/kb", "/kb/1", "/kb/new", "/kb/1/edit", "/kb/login"):
        body = kbc.get(path).get_data(as_text=True)
        assert not re.search(r"\sstyle\s*=", body), path
        assert not re.search(r"\son[a-z]+\s*=", body), path
        assert "<script>" not in body, path
