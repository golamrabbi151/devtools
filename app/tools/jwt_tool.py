"""JWT decoder / HS* encoder + verifier. Pure stdlib; 'alg: none' is never accepted as valid."""
import base64
import binascii
import hashlib
import hmac
import json
from datetime import datetime, timezone

from ..registry import Action, Field, Result, Tool, ToolError, register

HS = {"HS256": hashlib.sha256, "HS384": hashlib.sha384, "HS512": hashlib.sha512}


def b64u_decode(s: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except (binascii.Error, ValueError):
        raise ToolError("Token is not valid base64url.")


def b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def split_token(token: str):
    parts = "".join(token.split()).split(".")
    if len(parts) != 3:
        raise ToolError("A JWT has three dot-separated parts (header.payload.signature).")
    try:
        header = json.loads(b64u_decode(parts[0]))
        payload = json.loads(b64u_decode(parts[1]))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ToolError("Header or payload is not valid JSON.")
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise ToolError("Header and payload must be JSON objects.")
    return parts, header, payload


def _sign(alg: str, secret: str, signing_input: str) -> str:
    return b64u_encode(hmac.new(secret.encode(), signing_input.encode("ascii"), HS[alg]).digest())


def _times(payload: dict) -> list[str]:
    out, now = [], datetime.now(timezone.utc).timestamp()
    for claim in ("iat", "nbf", "exp"):
        v = payload.get(claim)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            try:
                out.append(f"{claim}: {datetime.fromtimestamp(v, timezone.utc):%Y-%m-%d %H:%M:%S} UTC"
                           + (" — EXPIRED" if claim == "exp" and v < now else "")
                           + (" — NOT YET VALID" if claim == "nbf" and v > now else ""))
            except (OverflowError, OSError, ValueError):
                out.append(f"{claim}: out of range")
    return out


def decode(token: str, secret: str = "") -> Result:
    parts, header, payload = split_token(token)
    text = ("HEADER\n" + json.dumps(header, indent=2, ensure_ascii=False)
            + "\n\nPAYLOAD\n" + json.dumps(payload, indent=2, ensure_ascii=False))
    notes = _times(payload)
    alg = header.get("alg")
    if alg == "none":
        notes.append("⚠ alg=none: this token is unsigned and must never be trusted.")
    if secret:
        if alg in HS:
            ok = hmac.compare_digest(_sign(alg, secret, f"{parts[0]}.{parts[1]}"), parts[2])
            notes.append("Signature: VALID ✔" if ok else "Signature: INVALID ✘")
        else:
            notes.append(f"Signature check supports HS256/384/512 only (token uses {alg!r}).")
    else:
        notes.append("Signature not verified (provide the secret to verify HS* tokens).")
    return Result(text, notes=notes)


def encode(header_text: str, payload_text: str, secret: str, alg: str) -> Result:
    if alg not in HS:
        raise ToolError("Unsupported algorithm.")
    if not secret:
        raise ToolError("A secret is required to sign.")
    try:
        header, payload = json.loads(header_text or "{}"), json.loads(payload_text)
    except json.JSONDecodeError as e:
        raise ToolError(f"Invalid JSON: {e.msg}")
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise ToolError("Header and payload must be JSON objects.")
    header.update({"alg": alg, "typ": "JWT"})
    si = (b64u_encode(json.dumps(header, separators=(",", ":")).encode())
          + "." + b64u_encode(json.dumps(payload, separators=(",", ":")).encode()))
    return Result(f"{si}.{_sign(alg, secret, si)}", notes=["Do not use real production secrets in online tools."])


def handler(action, form, files):
    if action == "decode":
        return decode(form["token"], form.get("secret", ""))
    return encode(form.get("header", ""), form["payload"], form.get("secret", ""), form.get("alg", "HS256"))


register(Tool(
    slug="jwt", title="JWT Encode / Decode",
    description="Inspect JWTs, verify HS256/384/512 signatures, or create signed tokens.",
    fields=(
        Field("token", "Token (to decode)", placeholder="eyJhbGciOi..."),
        Field("header", "Header JSON (to encode)", default='{"alg":"HS256","typ":"JWT"}'),
        Field("payload", "Payload JSON (to encode)", default='{"sub":"1234567890","name":"John Doe"}'),
        Field("alg", "Algorithm (encode)", "select", tuple((a, a) for a in HS), "HS256"),
        Field("secret", "Secret (sign / verify)", "text", max_len=1000),
    ),
    actions=(Action("decode", "Decode", True), Action("encode", "Encode")),
    handler=handler, group="Encoders / Decoders",
))
