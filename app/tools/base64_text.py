"""Base64 text encode / decode (standard and URL-safe)."""
import base64
import binascii

from ..registry import Action, Field, Result, Tool, ToolError, register


def encode(text: str, urlsafe: bool = False, pad: bool = True) -> str:
    enc = base64.urlsafe_b64encode if urlsafe else base64.b64encode
    out = enc(text.encode("utf-8")).decode("ascii")
    return out if pad else out.rstrip("=")


def decode_bytes(text: str, urlsafe: bool = False) -> bytes:
    cleaned = "".join(text.split())
    if "," in cleaned and cleaned.startswith("data:"):
        cleaned = cleaned.split(",", 1)[1]
    cleaned += "=" * (-len(cleaned) % 4)  # tolerate missing padding
    try:
        dec = base64.urlsafe_b64decode if urlsafe else base64.b64decode
        return dec(cleaned) if urlsafe else base64.b64decode(cleaned, validate=True)
    except (binascii.Error, ValueError):
        raise ToolError("Not valid Base64" + (" (URL-safe)." if urlsafe else "."))


def handler(action, form, files):
    text = form["input"]
    urlsafe = form.get("variant") == "urlsafe"
    if action == "encode":
        return Result(encode(text, urlsafe, form.get("pad") != "off"))
    data = decode_bytes(text, urlsafe)
    try:
        return Result(data.decode("utf-8"))
    except UnicodeDecodeError:
        preview = data[:256].hex(" ")
        return Result(f"(binary data, {len(data)} bytes — hex preview)\n{preview}"
                      + (" ..." if len(data) > 256 else ""))


register(Tool(
    slug="base64-text", title="Base64 Text",
    description="Encode text to Base64 or decode Base64 back to text.",
    fields=(
        Field("input", "Input", required=True),
        Field("variant", "Alphabet", "select", (("standard", "Standard (+/)"), ("urlsafe", "URL-safe (-_)")), "standard"),
        Field("pad", "Padding on encode", "select", (("on", "Keep '='"), ("off", "Strip '='")), "on"),
    ),
    actions=(Action("encode", "Encode", True), Action("decode", "Decode")),
    handler=handler, group="Encoders / Decoders",
))
