"""Base64 <-> image. Images are verified and re-encoded; original bytes are never echoed back."""
import base64
import io

from flask import current_app
from PIL import Image, UnidentifiedImageError

from ..registry import Action, Field, Result, Tool, ToolError, register
from .base64_text import decode_bytes

ALLOWED = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}


def open_image(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        fmt = img.format
        img.load()  # full decode: catches truncated/malicious files; bomb guard via MAX_IMAGE_PIXELS
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        raise ToolError("File is not a valid or supported image.")
    if fmt not in ALLOWED:
        raise ToolError("Supported formats: PNG, JPEG, GIF, WebP.")
    img.info["_fmt"] = fmt
    return img


def png_data_uri(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.convert("RGBA" if img.mode in ("P", "LA", "RGBA") else "RGB").save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def handler(action, form, files):
    if action == "encode":
        data = files.get("file")
        if not data:
            raise ToolError("Choose an image file to encode.")
        img = open_image(data)
        fmt = img.info["_fmt"]
        b64 = base64.b64encode(data).decode("ascii")
        out = f"data:{ALLOWED[fmt]};base64,{b64}" if form.get("as_uri") == "on" else b64
        return Result(out, png_data_uri(img), [f"{fmt}, {img.width}×{img.height}px, {len(data):,} bytes"])
    raw = decode_bytes(form["input"])
    if len(raw) > current_app.config["UPLOAD_LIMITS"]["base64-image"]:
        raise ToolError("Decoded image is too large.")
    from ..uploads import validate_image
    validate_image(raw)                       # same checks as an upload: real image, no trailing payload
    img = open_image(raw)
    return Result("", png_data_uri(img), [f"{img.info['_fmt']}, {img.width}×{img.height}px, {len(raw):,} bytes"])


register(Tool(
    slug="base64-image", title="Base64 Image",
    description="Convert an image to Base64 / data URI, or preview an image from Base64.",
    fields=(
        Field("file", "Image to encode (PNG, JPEG, GIF, WebP)", "file", accept="image/png,image/jpeg,image/gif,image/webp", upload="image"),
        Field("as_uri", "Output as data: URI", "checkbox"),
        Field("input", "Base64 to decode (raw or data: URI)"),
    ),
    actions=(Action("encode", "Encode image", True), Action("decode", "Decode to image")),
    handler=handler, group="Encoders / Decoders",
))
