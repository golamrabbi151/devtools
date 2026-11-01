"""HTTP security headers applied to every response."""
import re

from flask import Flask, request

CSP = "; ".join([
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "form-action 'self'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
    "object-src 'none'",
])


def build_csp(img_origins: str = "") -> str:
    """CSP, optionally allowing images from extra origins (e.g. the HR photo server for birthday cards).
    Only plain https://host[:port] origins are accepted; anything else is ignored."""
    extra = [o for o in img_origins.split() if re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d{1,5})?", o)]
    if not extra:
        return CSP
    return CSP.replace("img-src 'self' data:", "img-src 'self' data: " + " ".join(extra))


def init_security(app: Flask) -> None:
    csp = build_csp(app.config.get("BIRTHDAY_IMAGE_ORIGINS", ""))

    @app.after_request
    def set_headers(resp):
        resp.headers["Content-Security-Policy"] = csp
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        if request.is_secure or request.headers.get("X-Forwarded-Proto") == "https":
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # Tool input/output may be sensitive (tokens, keys): never cache dynamic pages.
        if not request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp
