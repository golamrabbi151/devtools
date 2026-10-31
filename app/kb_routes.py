"""Knowledge base pages: list + search, details modal, create / edit / delete, password sign-in.

Access: every route needs a signed-in session. The shared password comes from the KB_PASSWORD
env var; without it the knowledge base is disabled (findings are sensitive, so no open default).
"""
import hmac
import json
from functools import wraps

from flask import (Blueprint, abort, current_app, make_response, redirect, render_template, request,
                   session, url_for)

from . import kb
from .extensions import limiter
from .registry import all_tools

bp = Blueprint("kb", __name__, url_prefix="/kb")


def _page(template, **ctx):
    return render_template(template, tools=all_tools(), active="kb", kb=kb, **ctx)


def _hx_target() -> str:
    if request.headers.get("HX-History-Restore-Request"):
        return ""                                  # history restore wants the full page
    return request.headers.get("HX-Target", "") if request.headers.get("HX-Request") else ""


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_app.config.get("KB_PASSWORD"):
            return _page("kb_login.html", disabled=True), 503
        if not session.get("kb_auth"):
            if request.headers.get("HX-Request"):  # HTMX fragment: send the whole page to sign-in
                resp = make_response("", 401)
                resp.headers["HX-Redirect"] = url_for("kb.login")
                return resp
            return redirect(url_for("kb.login", next=request.full_path if request.method == "GET" else None))
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


# ------------------------------------------------------------------ auth
@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if not current_app.config.get("KB_PASSWORD"):
        return _page("kb_login.html", disabled=True), 503
    nxt = request.values.get("next", "")
    if not nxt.startswith("/kb") or nxt.startswith("//"):   # only local knowledge-base paths (no open redirect)
        nxt = url_for("kb.index")
    error = None
    if request.method == "POST":
        given = request.form.get("password", "").encode()
        if hmac.compare_digest(given, current_app.config["KB_PASSWORD"].encode()):
            session.clear()
            session["kb_auth"] = True
            return redirect(nxt)
        current_app.logger.warning("kb sign-in failed")
        error = "Wrong password."
    return _page("kb_login.html", error=error, next=nxt), (401 if error else 200)


@bp.post("/logout")
def logout():
    session.pop("kb_auth", None)
    return redirect(url_for("kb.login"))


# ------------------------------------------------------------------ list + search
@bp.get("")
@login_required
def index():
    params = request.args.to_dict()
    results = kb.search(params, _page_num())
    if _hx_target() == "kb-results":
        return render_template("_kb_list.html", kb=kb, results=results, params=params)
    return _page("kb.html", results=results, params=params, stats=kb.stats(), techs=kb.technologies())


# ------------------------------------------------------------------ details
@bp.get("/<int:record_id>")
@login_required
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


# ------------------------------------------------------------------ create / edit / delete
@bp.get("/new")
@login_required
def new():
    return render_template("_kb_form.html", kb=kb, rec=None, form={}, techs=kb.technologies())


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
    if record_id:
        kb.update(record_id, data)
    else:
        record_id = kb.create(data)
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
