"""Knowledge base pages: list + search, details modal, create / edit / delete, AD sign-in.

Access: anyone can list, search and read records. Adding, editing and deleting need a sign-in
against the AD login API (app/kb_auth.py). A sign-in lasts KB_SESSION_MINUTES (default 30) from the
moment of login; after that the server-side session is dropped and the user is signed out.
"""
import json
import time
from functools import wraps

from flask import (Blueprint, abort, current_app, g, make_response, redirect, render_template, request,
                   session, url_for)

from . import kb, kb_auth
from .extensions import limiter
from .registry import all_tools

bp = Blueprint("kb", __name__, url_prefix="/kb")
MAX_USER, MAX_PASSWORD = 128, 256


# ------------------------------------------------------------------ session
# The sign-in is a server-side session: the cookie carries only a random 256-bit token; the database holds
# its SHA-256 with the user name and expiry. Nothing in the cookie can be forged or edited (even with the
# SECRET_KEY), signing out revokes the token immediately, and the user name is never sent to the browser.
def _cookie_name() -> str:
    return current_app.config["KB_SESSION_COOKIE"]


def _session() -> tuple[str, float] | None:
    if "kb_sess" not in g:
        g.kb_sess = kb.session_lookup(request.cookies.get(_cookie_name(), ""), time.time())
    return g.kb_sess


def current_user() -> str | None:
    """Signed-in user name, or None (no cookie, unknown / revoked token, or past its expiry)."""
    sess = _session()
    return sess[0] if sess else None


@bp.app_context_processor
def _inject_user():
    sess = _session() if request.blueprint == "kb" else None
    return {"kb_user": sess[0] if sess else None, "kb_exp": int(sess[1]) if sess else 0}


def _set_session_cookie(resp, token: str, max_age: int):
    resp.set_cookie(_cookie_name(), token, max_age=max_age, path="/", httponly=True,
                    secure=current_app.config["SESSION_COOKIE_SECURE"], samesite="Strict")


def _clear_session_cookie(resp):
    resp.delete_cookie(_cookie_name(), path="/", httponly=True,
                       secure=current_app.config["SESSION_COOKIE_SECURE"], samesite="Strict")


def _page(template, **ctx):
    return render_template(template, tools=all_tools(), active="kb", kb=kb, **ctx)


def _hx_target() -> str:
    if request.headers.get("HX-History-Restore-Request"):
        return ""                                  # history restore wants the full page
    return request.headers.get("HX-Target", "") if request.headers.get("HX-Request") else ""


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user():
            if request.headers.get("HX-Request"):  # HTMX fragment: send the whole page to sign-in
                resp = make_response("", 401)
                resp.headers["HX-Redirect"] = url_for("kb.login")
                return resp
            return redirect(url_for("kb.login"))
        return view(*args, **kwargs)
    return wrapper


def _changed(resp, close=False):
    events = {"kb-changed": True}
    if close:
        events["kb-close"] = True
    resp.headers["HX-Trigger"] = json.dumps(events)
    return resp


def _page_num() -> int:
    try:
        return max(1, min(int(request.args.get("page", "1")), 10_000))
    except ValueError:
        return 1


# ------------------------------------------------------------------ sign-in / sign-out
@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if not kb_auth.configured():
        return _page("kb_login.html", disabled=True), 503
    nxt = request.values.get("next", "")
    if not nxt.startswith("/kb") or nxt.startswith("//"):   # only local knowledge-base paths (no open redirect)
        nxt = url_for("kb.index")
    error, status, user = None, 200, ""
    if request.method == "POST":
        user = " ".join(request.form.get("username", "").split())
        password = request.form.get("password", "")
        if not user or not password:
            error, status = "Enter your user name and password.", 400
        elif len(user) > MAX_USER or len(password) > MAX_PASSWORD:
            error, status = "User name or password is too long.", 400
        else:
            try:
                ok = kb_auth.authenticate(user, password)
            except kb_auth.AuthUnavailable as e:
                current_app.logger.error("kb sign-in service unavailable: %s", e)
                ok, error, status = False, "The sign-in service is not reachable. Please try again later.", 503
            if ok:
                lifetime = current_app.config["KB_SESSION_MINUTES"] * 60
                old = request.cookies.get(_cookie_name(), "")
                kb.session_delete(old)                     # a previous token never survives a new sign-in
                token, _expires = kb.session_create(user, time.time(), lifetime)
                session.pop("csrf_token", None)            # new CSRF token after the privilege change
                current_app.logger.info("kb sign-in ok")
                resp = redirect(nxt)
                _set_session_cookie(resp, token, lifetime)
                return resp
            if not error:
                current_app.logger.warning("kb sign-in rejected")
                error, status = "Invalid user name or password.", 401
    return _page("kb_login.html", error=error, next=nxt, username=user), status


@bp.post("/logout")
def logout():
    kb.session_delete(request.cookies.get(_cookie_name(), ""))   # revoke on the server, not just in the browser
    g.pop("kb_sess", None)
    session.pop("csrf_token", None)
    resp = redirect(url_for("kb.index"))
    _clear_session_cookie(resp)
    return resp


# ------------------------------------------------------------------ list + search (public)
@bp.get("")
def index():
    params = request.args.to_dict()
    results = kb.search(params, _page_num())
    if _hx_target() == "kb-results":
        return render_template("_kb_list.html", kb=kb, results=results, params=params)
    return _page("kb.html", results=results, params=params, stats=kb.stats(), techs=kb.technologies())


# ------------------------------------------------------------------ details (public)
@bp.get("/<int:record_id>")
def detail(record_id: int):
    rec = kb.get(record_id)
    if rec is None:
        abort(404)
    ctx = {"rec": rec, "related": kb.related(rec)}
    if _hx_target() == "kb-modal-body":
        return render_template("_kb_detail.html", kb=kb, **ctx)
    params = {}                                     # direct link: full page with the modal open
    return _page("kb.html", results=kb.search(params), params=params, stats=kb.stats(),
                 techs=kb.technologies(), modal=render_template("_kb_detail.html", kb=kb, **ctx))


# ------------------------------------------------------------------ create / edit / delete (signed in)
@bp.get("/new")
@login_required
def new():
    return render_template("_kb_form.html", kb=kb, rec=None, form={"reported_by": current_user()},
                           techs=kb.technologies())


@bp.get("/<int:record_id>/edit")
@login_required
def edit(record_id: int):
    rec = kb.get(record_id)
    if rec is None:
        abort(404)
    return render_template("_kb_form.html", kb=kb, rec=rec, form=dict(rec), techs=kb.technologies())


@bp.post("/save")
@login_required
def save():
    raw = request.form.get("id", "")
    record_id = int(raw) if raw.isdigit() else None
    rec = kb.get(record_id) if record_id else None
    if record_id and rec is None:
        abort(404)
    try:
        data = kb.validate(request.form)
    except kb.KBError as e:
        return render_template("_kb_form.html", kb=kb, rec=rec, form=request.form, error=str(e),
                               techs=kb.technologies())
    user = current_user()
    if record_id:
        kb.update(record_id, data, user)
    else:
        record_id = kb.create(data, user)
    saved = kb.get(record_id)
    resp = make_response(render_template("_kb_detail.html", kb=kb, rec=saved, related=kb.related(saved),
                                         notice="Saved."))
    return _changed(resp)


@bp.post("/<int:record_id>/delete")
@login_required
def remove(record_id: int):
    if not kb.delete(record_id):
        abort(404)
    return _changed(make_response(""), close=True)
