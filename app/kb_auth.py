"""Knowledge base sign-in against the company AD login API.

POST {"userId", "password", "channelCode"} as JSON to KB_AUTH_URL. The user is valid when the reply has
responseCode == 100 and isValidUser == 1 (numbers or strings are both accepted).
The password is sent only to that API (HTTPS required), never stored and never logged.
"""
import json
import ssl
import urllib.error
import urllib.request

from flask import current_app

TIMEOUT_S = 10
MAX_REPLY = 64 * 1024


class AuthUnavailable(Exception):
    """The login API could not be reached or answered with something unexpected."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):     # never re-send credentials to another URL
        return None


_opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirect)


def configured() -> bool:
    return bool(current_app.config.get("KB_AUTH_URL"))


def authenticate(user: str, password: str) -> bool:
    url = current_app.config["KB_AUTH_URL"]
    if not url.lower().startswith("https://"):
        raise AuthUnavailable("KB_AUTH_URL must be an https:// URL")
    body = json.dumps({"userId": user, "password": password,
                       "channelCode": current_app.config["KB_AUTH_CHANNEL"]}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with _opener.open(req, timeout=TIMEOUT_S) as resp:     # nosec B310 - scheme checked above (https only)
            raw = resp.read(MAX_REPLY + 1)
    except urllib.error.HTTPError as e:                         # 4xx/5xx may still carry the JSON verdict
        raw = e.read(MAX_REPLY + 1) if e.code < 500 else b""
        if not raw:
            raise AuthUnavailable(f"HTTP {e.code}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise AuthUnavailable(type(e).__name__)
    if len(raw) > MAX_REPLY:
        raise AuthUnavailable("reply too large")
    try:
        reply = json.loads(raw)
    except ValueError:
        raise AuthUnavailable("reply is not JSON")
    if not isinstance(reply, dict):
        raise AuthUnavailable("unexpected reply")
    return str(reply.get("responseCode")) == "100" and str(reply.get("isValidUser")) == "1"
