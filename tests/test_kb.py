"""Security knowledge base: access control, CRUD, search quality and output escaping."""
import html
import re

import pytest

from app import kb

PW = "test-kb-password"


def token(client, path="/kb"):
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


@pytest.fixture()
def kbc(client):
    """Signed-in client."""
    r = client.post("/kb/login", data={"password": PW, "csrf_token": token(client, "/kb/login")})
    assert r.status_code == 302
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


# ------------------------------------------------------------------ access control
def test_requires_sign_in(client):
    assert client.get("/kb").status_code == 302
    r = client.get("/kb", headers={"HX-Request": "true"})
    assert r.status_code == 401 and r.headers["HX-Redirect"] == "/kb/login"
    t = token(client, "/kb/login")
    assert client.post("/kb/save", data={"title": "x", "csrf_token": t}).status_code == 302


def test_wrong_password_and_open_redirect(client):
    t = token(client, "/kb/login")
    assert client.post("/kb/login", data={"password": "nope", "csrf_token": t}).status_code == 401
    r = client.post("/kb/login", data={"password": PW, "next": "//evil.example", "csrf_token": t})
    assert r.headers["Location"] == "/kb"


def test_disabled_without_password(app, client):
    app.config["KB_PASSWORD"] = ""
    assert client.get("/kb").status_code == 503
    assert "KB_PASSWORD" in client.get("/kb/login").get_data(as_text=True)


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
