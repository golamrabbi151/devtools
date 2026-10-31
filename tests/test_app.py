import base64
import io
import json

import segno

from tests.conftest import get_token


def out_text(resp):
    """Visible text of the result <pre> (tags stripped, entities decoded)."""
    import html
    import re
    body = resp.get_data(as_text=True).split('<pre id="out" class="hl">')[1].split("</pre>")[0]
    return html.unescape(re.sub(r"<[^>]+>", "", body))


# ---------- pages ----------
def test_all_pages_render(client):
    assert client.get("/").status_code == 200
    for slug in ("json-format", "json-compare", "sql-format", "xml-format", "cron",
                 "base64-text", "base64-image", "certificate", "qr-code", "jwt", "excel-json", "excel-sql",
                 "lorem-ipsum", "password", "uuid"):
        assert client.get(f"/tool/{slug}").status_code == 200, slug
    assert client.get("/tool/nope").status_code == 404


# ---------- functional ----------
def test_json_format_and_error(run):
    r = run("json-format", input='{"b":1,"a":[1,2]}', action="format", indent="2", sort="on")
    assert '"a": [' in out_text(r)
    r = run("json-format", input="{bad", action="format")
    assert "Invalid JSON" in r.get_data(as_text=True)


def test_json_compare(run):
    r = run("json-compare", left='{"a":1,"b":2}', right='{"a":3,"c":4}', action="compare")
    t = r.get_data(as_text=True)
    assert "changed" in t and "removed" in t and "added" in t


def test_sql_format(run):
    r = run("sql-format", input="select a,b from t where x=1", action="format")
    assert "SELECT" in r.get_data(as_text=True)


def test_xml_format(run):
    r = run("xml-format", input="<a><b>1</b></a>", action="format")
    assert "<b>1</b>" in out_text(r)


def test_cron(run):
    r = run("cron", input="*/15 9-17 * * 1-5", tz="Asia/Dhaka", count="5", action="parse")
    t = r.get_data(as_text=True)
    assert "Next runs" in t and "Meaning" in t
    assert "Invalid" in run("cron", input="61 * * * *", action="parse").get_data(as_text=True)


def test_base64_text_roundtrip(run):
    enc = run("base64-text", input="héllo wörld", action="encode").get_data(as_text=True)
    assert "aMOpbGxvIHfDtnJsZA==" in enc
    dec = run("base64-text", input="aMOpbGxvIHfDtnJsZA==", action="decode").get_data(as_text=True)
    assert "héllo wörld" in dec


def test_jwt_roundtrip_and_verify(run):
    r = run("jwt", header='{"alg":"HS256"}', payload='{"sub":"1"}', secret="s3cret", alg="HS256", action="encode")
    token = out_text(r).strip()
    ok = run("jwt", token=token, secret="s3cret", action="decode").get_data(as_text=True)
    assert "VALID ✔" in ok
    bad = run("jwt", token=token, secret="wrong", action="decode").get_data(as_text=True)
    assert "INVALID" in bad


def test_qr_roundtrip(run):
    r = run("qr-code", input="https://example.com/?a=1", ecc="m", scale="8", action="encode")
    uri = r.get_data(as_text=True).split('src="')[1].split('"')[0]
    png = base64.b64decode(uri.split(",", 1)[1])
    r2 = run("qr-code", action="decode", file=(io.BytesIO(png), "q.png"))
    assert "https://example.com/?a=1" in r2.get_data(as_text=True)


def test_base64_image_roundtrip(run):
    buf = io.BytesIO()
    segno.make("x").save(buf, kind="png", scale=3)
    r = run("base64-image", action="encode", file=(io.BytesIO(buf.getvalue()), "a.png"))
    assert "PNG" in r.get_data(as_text=True)
    b64 = base64.b64encode(buf.getvalue()).decode()
    r2 = run("base64-image", action="decode", input="data:image/png;base64," + b64)
    assert "<img" in r2.get_data(as_text=True)


def test_certificate(run):
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.example")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("test.example")]), False)
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    t = run("certificate", input=pem, action="decode").get_data(as_text=True)
    assert "CN=test.example" in t and "valid" in t and "EC secp256r1" in t


# ---------- security ----------
def test_security_headers(client):
    h = client.get("/").headers
    assert "script-src 'self'" in h["Content-Security-Policy"]
    assert "unsafe-inline" not in h["Content-Security-Policy"]
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert h["Cache-Control"] == "no-store"


def test_no_inline_scripts_or_handlers(client):
    html = client.get("/tool/json-format").get_data(as_text=True)
    assert "<script>" not in html and " onclick=" not in html
    assert "javascript:" not in html


def test_csrf_enforced(csrf_client):
    r = csrf_client.post("/tool/json-format/run", data={"input": "{}", "action": "format"})
    assert r.status_code == 400
    token = get_token(csrf_client)
    r = csrf_client.post("/tool/json-format/run",
                         data={"input": "{}", "action": "format", "csrf_token": token})
    assert r.status_code == 200


def test_output_is_escaped(run):
    r = run("json-format", input='{"x":"<script>alert(1)</script>"}', action="format")
    t = r.get_data(as_text=True)
    assert "<script>alert(1)" not in t and "&lt;script&gt;" in t


def test_xxe_and_entity_bomb_rejected(run):
    xxe = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
    t = run("xml-format", input=xxe, action="format").get_data(as_text=True)
    assert "root:" not in t and "not allowed" in t
    bomb = '<!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><l>&b;</l>'
    assert "not allowed" in run("xml-format", input=bomb, action="format").get_data(as_text=True)


def test_oversized_field_rejected(run):
    r = run("cron", input="*" * 300, action="parse")          # per-field max_len (200)
    assert "too long" in r.get_data(as_text=True)
    r = run("json-format", input="1" * 600_000, action="validate")  # server-wide form cap
    assert r.status_code == 413


def test_oversized_request_rejected(client):
    r = client.post("/tool/json-format/run", data=b"x" * (4 * 1024 * 1024),
                    content_type="application/octet-stream")
    assert r.status_code in (400, 413)


def test_non_image_upload_rejected(run):
    r = run("qr-code", action="decode", file=(io.BytesIO(b"<svg onload=alert(1)>"), "x.png"))
    assert "Rejected" in r.get_data(as_text=True)


def test_unknown_action_and_extra_fields_ignored(run):
    assert "Unknown action" in run("json-format", input="{}", action="rm -rf").get_data(as_text=True)


def test_jwt_alg_none_flagged(run):
    h = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    p = base64.urlsafe_b64encode(json.dumps({"sub": 1}).encode()).rstrip(b"=").decode()
    t = run("jwt", token=f"{h}.{p}.", secret="x", action="decode").get_data(as_text=True)
    assert "unsigned" in t and "VALID ✔" not in t


def test_deeply_nested_json_does_not_crash(run):
    r = run("json-format", input="[" * 100000 + "]" * 100000, action="format")
    assert r.status_code == 200


# ---------- highlighting ----------
def test_json_is_highlighted_and_escaped(run):
    r = run("json-format", input='{"k":"<img src=x onerror=alert(1)>","n":1,"t":true}', action="format")
    t = r.get_data(as_text=True)
    assert 'class="t-key"' in t and 'class="t-str"' in t and 'class="t-num"' in t and 'class="t-lit"' in t
    assert "<img" not in t and "&lt;img" in t


def test_xml_highlight_escapes_attrs_and_text(run):
    r = run("xml-format", input='<a x="1&quot;&lt;b"><b>&lt;script&gt;</b></a>', action="format")
    t = r.get_data(as_text=True)
    assert 'class="t-tag"' in t and 'class="t-attr"' in t
    assert "<script" not in t


def test_sql_highlight(run):
    r = run("sql-format", input="select 'a<b', 1 from t -- c<d", action="format")
    t = r.get_data(as_text=True)
    assert 'class="t-kw"' in t and 'class="t-str"' in t and 'class="t-num"' in t
    assert "<b" not in t.replace("<br", "").split("<pre")[1].replace("<span", "").replace("</span", "")


def test_compare_is_coloured(run):
    r = run("json-compare", left='{"a":1,"b":2}', right='{"a":3,"c":"<x>"}', action="compare")
    t = r.get_data(as_text=True)
    assert "d-add" in t and "d-del" in t and "d-chg" in t and "&lt;x&gt;" in t


def test_highlight_cap_falls_back_to_plain():
    from app.highlight import MAX_HIGHLIGHT, json_html
    assert json_html("1" * (MAX_HIGHLIGHT + 1)) is None


# ---------- generators ----------
def test_lorem(run):
    t = run("lorem-ipsum", kind="paragraphs", count="2", classic="on", action="generate").get_data(as_text=True)
    assert "Lorem ipsum dolor sit amet," in t and "\n\n" in t
    t = run("lorem-ipsum", kind="words", count="5", action="generate").get_data(as_text=True)
    assert len(out_text(run("lorem-ipsum", kind="words", count="5", action="generate")).split()) == 5
    assert "between 1 and" in run("lorem-ipsum", kind="words", count="99999", action="generate").get_data(as_text=True)


def _pwds(resp):
    return out_text(resp).split("\n")


def test_password_rules(run):
    r = run("password", length="24", count="5", lower="on", upper="on", digits="on", symbols="on", action="generate")
    pw = _pwds(r)
    assert len(pw) == 5 and len(set(pw)) == 5
    for p in pw:
        assert len(p) == 24
        assert any(c.islower() for c in p) and any(c.isupper() for c in p)
        assert any(c.isdigit() for c in p) and any(not c.isalnum() for c in p)
    assert "bits of entropy" in r.get_data(as_text=True)


def test_password_options_and_errors(run):
    pw = _pwds(run("password", length="64", count="1", digits="on", exclude="12345", action="generate"))[0]
    assert set(pw) <= set("06789") and len(pw) == 64
    assert "between 4 and 128" in run("password", length="200", count="1", lower="on", action="generate").get_data(as_text=True)
    assert "No characters left" in run("password", length="12", count="1", action="generate").get_data(as_text=True)
    amb = _pwds(run("password", length="128", count="1", lower="on", upper="on", digits="on", ambiguous="on", action="generate"))[0]
    assert not set(amb) & set("Il1O0o")


def test_uuid(run):
    import uuid as u
    out = _pwds(run("uuid", version="4", count="10", hyphens="on", action="generate"))
    assert len(out) == 10 and all(u.UUID(x).version == 4 for x in out)
    out = _pwds(run("uuid", version="7", count="5", hyphens="on", action="generate"))
    assert all(u.UUID(x).version == 7 for x in out) and out == sorted(out)
    assert _pwds(run("uuid", version="5", namespace="dns", name="python.org", hyphens="on", action="generate"))[0] \
        == str(u.uuid5(u.NAMESPACE_DNS, "python.org"))
    t = _pwds(run("uuid", version="4", count="1", upper="on", braces="on", action="generate"))[0]
    assert t.startswith("{") and t.endswith("}") and t[1:-1].isupper() or t[1:-1].upper() == t[1:-1]
    info = run("uuid", uuid=out[0], action="inspect").get_data(as_text=True)
    assert "Version  : 7" in info and "Timestamp" in info
    assert "Not a valid UUID" in run("uuid", uuid="nope", action="inspect").get_data(as_text=True)


# ---------- design / layout ----------
def test_palette_is_applied():
    css = open("app/static/css/app.css", encoding="utf-8").read().lower()
    for colour in ("#12685d", "#f5f7f2", "#183b35", "#e3eddb"):
        assert colour in css, colour


def test_sidebar_toggle_present_and_accessible(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="nav-toggle"' in html and 'aria-controls="sidebar"' in html and 'aria-expanded=' in html
    assert 'id="sidebar"' in html


def test_no_inline_styles_or_handlers_on_any_page(client):
    import re
    for slug in ("", "tool/json-format", "tool/json-compare", "tool/xml-format", "tool/sql-format",
                 "tool/cron", "tool/jwt", "tool/uuid"):
        html = client.get("/" + slug).get_data(as_text=True)
        assert not re.search(r"\sstyle\s*=", html), slug          # CSP style-src 'self' would block these
        assert not re.search(r"\son[a-z]+\s*=", html), slug
        assert "<script>" not in html and "javascript:" not in html, slug


def test_split_layout_has_input_and_output_panes(client):
    html = client.get("/tool/json-format").get_data(as_text=True)
    assert "data-editor" in html and 'id="result"' in html and "data-sample=" in html
    assert 'class="tool-form lay-split"' in html


def test_compare_layout_has_two_editors_and_button_between(client):
    html = client.get("/tool/json-compare").get_data(as_text=True)
    assert html.count("data-editor") == 2 and "compare-mid" in html
    assert html.index('id="f-left"') < html.index("compare-mid") < html.index('id="f-right"')


def test_cron_layout_and_live_trigger(client):
    html = client.get("/tool/cron").get_data(as_text=True)
    assert "cron-input" in html and "data-fill=" in html and "hx-trigger=" in html


def test_sample_attribute_is_safely_encoded(client):
    html = client.get("/tool/xml-format").get_data(as_text=True)
    assert "data-sample='" in html and "<catalog>" not in html   # tojson escapes < > & '


def test_compare_table_side_by_side(run):
    t = run("json-compare", left='{"a":1,"b":[1,2]}', right='{"a":2,"b":[1,2,3]}', action="compare").get_data(as_text=True)
    assert 'class="diff"' in t and "<th>Left</th>" in t and "<th>Right</th>" in t
    assert "1 added" in t and "1 changed" in t
    same = run("json-compare", left="[1]", right="[1]", action="compare").get_data(as_text=True)
    assert "identical" in same


def test_compare_escapes_hostile_values(run):
    t = run("json-compare", left='{"k":"<img src=x onerror=alert(1)>"}', right='{"k":"</td><script>alert(1)</script>"}',
            action="compare").get_data(as_text=True)
    assert "<img" not in t and "<script" not in t


def test_cron_breakdown_cards_and_runs(run):
    t = run("cron", input="*/15 9-17 * * 1-5", tz="UTC", count="5", action="parse").get_data(as_text=True)
    assert 'class="cron-fields"' in t and t.count('class="cron-field"') == 5
    assert "Day of week" in t and 'class="runs"' in t and "in " in t


def test_cron_escapes_and_rejects_hostile_input(run):
    t = run("cron", input="<img src=x onerror=alert(1)> * * * *", action="parse").get_data(as_text=True)
    assert "<img" not in t
    t = run("cron", input="* * * * *", tz="<script>x</script>", action="parse").get_data(as_text=True)
    assert "<script" not in t


def test_download_and_copy_buttons_on_output(run):
    t = run("json-format", input="{}", action="format").get_data(as_text=True)
    assert "data-download=" in t and 'data-filename="result.json"' in t and "data-copy=" in t
    assert 'class="gutter"' in t


def test_field_options_only_on_select():
    """Regression: defaults passed positionally landed in `options` and were silently lost."""
    from app.registry import all_tools
    for t in all_tools():
        for f in t.fields:
            assert f.kind == "select" or not f.options, (t.slug, f.name)


def test_defaults_are_rendered_in_forms(client):
    html = client.get("/tool/password").get_data(as_text=True)
    assert 'name="length" value="20"' in html and html.count(" checked>") >= 4
    assert 'name="count" value="3"' in client.get("/tool/lorem-ipsum").get_data(as_text=True)
    assert 'name="tz" value="UTC"' in client.get("/tool/cron").get_data(as_text=True)
    assert 'value="*/15 9-17 * * 1-5"' in client.get("/tool/cron").get_data(as_text=True)


def test_cron_runs_with_defaults_like_the_browser_does(client):
    import re
    html = client.get("/tool/cron").get_data(as_text=True)
    tok = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    r = client.post("/tool/cron/run", data={"csrf_token": tok, "input": "*/15 9-17 * * 1-5", "tz": "UTC", "count": "10"})
    assert 'class="cron-fields"' in r.get_data(as_text=True)
