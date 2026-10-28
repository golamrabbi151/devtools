"""Generic routes: one page + one HTMX endpoint per registered tool."""
from flask import Blueprint, abort, current_app, render_template, request

from .extensions import limiter
from . import uploads
from .registry import Result, ToolError, all_tools, get_tool

bp = Blueprint("main", __name__)


@bp.get("/healthz")
@limiter.exempt
def healthz():
    """Liveness probe for load balancers / orchestrators. No tool code, no secrets, no state."""
    return "ok", 200, {"Content-Type": "text/plain; charset=utf-8"}


@bp.get("/")
def index():
    return render_template("index.html", tools=all_tools(), active=None)


@bp.get("/tool/<slug>")
def tool_page(slug: str):
    tool = get_tool(slug)
    if tool is None:
        abort(404)
    return render_template("tool.html", tool=tool, tools=all_tools(), active=slug,
                           upload_limit=uploads.limit_for(tool, current_app.config))


@bp.post("/tool/<slug>/run")
@limiter.limit(lambda: _rate_for(request.view_args.get("slug", "")))
def tool_run(slug: str):
    tool = get_tool(slug)
    if tool is None:
        abort(404)

    cfg = current_app.config
    action = request.form.get("action") or tool.actions[0].name
    if action not in {a.name for a in tool.actions}:
        return _render(tool, error="Unknown action.")

    # Only read declared fields (ignore anything else the client sends) and cap sizes.
    form, files, notes = {}, {}, []
    try:
        for f in tool.fields:
            if f.kind == "file":
                up = request.files.get(f.name)
                if up and up.filename:
                    limit = uploads.limit_for(tool, cfg)
                    # Read at most limit+1 bytes: enough to know it is too big, never more.
                    data = up.stream.read(limit + 1)
                    if len(data) > limit:
                        raise ToolError(f"File '{f.label}' is larger than the {uploads.fmt_limit(limit)} limit "
                                        f"for this tool.")
                    if f.upload:                       # magic bytes / structure / malware-style checks
                        notes.extend(uploads.screen(f.upload, data))
                    files[f.name] = data
            elif f.kind == "checkbox":
                # Unchecked boxes are absent from the POST; don't fall back to the default.
                form[f.name] = "on" if request.form.get(f.name) else ""
            else:
                val = request.form.get(f.name, f.default)
                limit = f.max_len or cfg["MAX_TEXT_FIELD"]
                if len(val) > limit:
                    raise ToolError(f"'{f.label}' is too long (max {limit:,} characters).")
                form[f.name] = val
        result = tool.handler(action, form, files)
        if not isinstance(result, Result):
            raise TypeError("handler must return Result")
        result.notes = notes + result.notes          # "what we checked" first, then tool notes
        return _render(tool, result=result)
    except ToolError as e:
        return _render(tool, error=str(e))
    except RecursionError:
        return _render(tool, error="Input is nested too deeply.")
    except Exception as e:  # never leak internals or echo user input into logs
        current_app.logger.error("tool %s failed: %s", slug, type(e).__name__)
        return _render(tool, error="Could not process the input.")


def _rate_for(slug: str) -> str:
    tool = get_tool(slug)
    return tool.rate if tool else "30 per minute"


def _render(tool, result: Result | None = None, error: str | None = None):
    return render_template("_result.html", tool=tool, result=result, error=error)
