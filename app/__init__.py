"""Application factory."""
import io

from flask import Flask, Request, abort, current_app, render_template, request
from PIL import Image
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import get_config
from .extensions import csrf, limiter
from .registry import all_tools
from .security import init_security


class SafeRequest(Request):
    """Keep uploaded files in memory only (bounded by the size limits), never in temp files on disk."""

    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        return io.BytesIO()


def _limit_request_size() -> None:
    """Runs BEFORE CSRF/form parsing: apply the tool's own body limit (e.g. 1 MB workbook + slack),
    so an oversized upload is refused early instead of being read and parsed."""
    if request.endpoint != "main.tool_run":
        return
    from . import uploads
    from .registry import get_tool
    tool = get_tool((request.view_args or {}).get("slug", ""))
    if tool is None:
        return
    limit = uploads.request_limit_for(tool, current_app.config)
    request.max_content_length = limit
    if request.content_length is not None and request.content_length > limit:
        abort(413)


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__)
    app.request_class = SafeRequest
    app.config.from_object(get_config(config_name))
    Image.MAX_IMAGE_PIXELS = app.config["MAX_IMAGE_PIXELS"]  # raises on decompression bombs
    hops = app.config["PROXY_HOPS"]
    if hops:   # trust exactly `hops` proxies' X-Forwarded-* headers (never more: they are client-spoofable)
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)

    app.before_request(_limit_request_size)     # must be registered before CSRF (which parses the form)
    csrf.init_app(app)
    limiter.init_app(app)
    init_security(app)

    from .tools import load_all
    load_all()

    from .routes import bp
    app.register_blueprint(bp)

    _register_errors(app)
    return app


def _register_errors(app: Flask) -> None:
    def make(code, msg):
        def handler(_e):
            if request.headers.get("HX-Request"):  # HTMX: return only the fragment
                return render_template("_result.html", result=None, error=msg), code
            return render_template("error.html", code=code, message=msg, tools=all_tools(), active=None), code
        return handler

    for code, msg in {400: "Bad request (or expired session — reload the page).",
                      404: "Page not found.",
                      405: "Method not allowed.",
                      413: "The upload is larger than this tool allows.",
                      429: "Too many requests — slow down.",
                      500: "Something went wrong."}.items():
        app.register_error_handler(code, make(code, msg))
