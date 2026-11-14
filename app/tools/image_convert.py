"""Image converter: PNG / JPEG / WebP / AVIF / GIF / BMP / TIFF / ICO, with optional resize and quality.

Safety: the upload has passed app.uploads.validate_image (real image, nothing appended). Pillow decodes it
with the decompression-bomb guard (MAX_IMAGE_PIXELS), the output size is capped, and the result is always
a fresh re-encode: metadata (EXIF incl. GPS location, comments, ICC extras) is never copied across.
"""
import base64
import io
import os
import re
import threading

from markupsafe import Markup
from PIL import Image, ImageOps, UnidentifiedImageError

from ..registry import Action, Field, Result, Tool, ToolError, clamp_int, register

INPUTS = {"PNG", "JPEG", "GIF", "WEBP", "BMP", "AVIF"}
# key -> (Pillow format, extension, MIME type, keeps transparency, browser can preview it)
OUTPUTS = {
    "jpg":  ("JPEG", "jpg", "image/jpeg", False, True),
    "png":  ("PNG", "png", "image/png", True, True),
    "webp": ("WEBP", "webp", "image/webp", True, True),
    "avif": ("AVIF", "avif", "image/avif", True, True),
    "gif":  ("GIF", "gif", "image/gif", True, True),
    "bmp":  ("BMP", "bmp", "image/bmp", False, True),
    "tiff": ("TIFF", "tiff", "image/tiff", True, False),
    "ico":  ("ICO", "ico", "image/x-icon", True, True),
}
MAX_SIDE = 8000
MAX_OUT_PIXELS = 40_000_000
MAX_OUT_BYTES = 25 * 1024 * 1024
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
# AVIF / big WebP encodes are CPU-heavy: allow only a few at once per process.
_SLOTS = threading.BoundedSemaphore(max(1, int(os.environ.get("IMAGE_MAX_PARALLEL", "2"))))


def _open(data: bytes) -> tuple[Image.Image, str, int]:
    try:
        img = Image.open(io.BytesIO(data))
        fmt, frames = img.format, getattr(img, "n_frames", 1)
        img.load()                                  # full decode (bomb guard applies here)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        raise ToolError("The file is not a valid or supported image.")
    if fmt not in INPUTS:
        raise ToolError("Supported input formats: PNG, JPEG, GIF, WebP, BMP, AVIF.")
    img = ImageOps.exif_transpose(img)              # phone photos: apply the rotation before EXIF is dropped
    return img, fmt, frames


def _background(text: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", (text or "").strip())
    if not m:
        raise ToolError("Background must be a hex colour such as #ffffff.")
    h = m.group(1)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _resize(img: Image.Image, width: str, height: str) -> Image.Image:
    w = clamp_int(width, 1, MAX_SIDE, "Width") if width.strip() else None
    h = clamp_int(height, 1, MAX_SIDE, "Height") if height.strip() else None
    if not w and not h:
        return img
    if w and h:
        size = (w, h)                               # exact size (may change the aspect ratio)
    elif w:
        size = (w, max(1, round(img.height * w / img.width)))
    else:
        size = (max(1, round(img.width * h / img.height)), h)
    if size[0] * size[1] > MAX_OUT_PIXELS:
        raise ToolError(f"The output would be too large (max {MAX_OUT_PIXELS // 1_000_000} megapixels).")
    return img.resize(size, Image.Resampling.LANCZOS)


def _prepare(img: Image.Image, keeps_alpha: bool, bg) -> tuple[Image.Image, bool]:
    """Pick a pixel mode the target format can store. Returns (image, transparency was flattened)."""
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    if has_alpha and not keeps_alpha:
        rgba = img.convert("RGBA")
        flat = Image.new("RGB", rgba.size, bg)
        flat.paste(rgba, mask=rgba.getchannel("A"))
        return flat, True
    if img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
        img = img.convert("RGBA" if has_alpha else "RGB")
    return img, False


def convert(data: bytes, target: str, quality: int, width: str = "", height: str = "", bg_text: str = "#ffffff",
            lossless: bool = False):
    if target not in OUTPUTS:
        raise ToolError("Choose an output format.")
    pil_fmt, ext, mime, keeps_alpha, previewable = OUTPUTS[target]
    img, src_fmt, frames = _open(data)
    src_size = img.size
    img = _resize(img, width, height)
    img, flattened = _prepare(img, keeps_alpha, _background(bg_text))

    opts = {}
    if pil_fmt == "JPEG":
        opts = {"quality": quality, "optimize": True, "progressive": True}
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
    if pil_fmt in ("WEBP", "AVIF") and img.mode not in ("RGB", "RGBA"):
        alpha = img.mode in ("LA", "PA") or (img.mode == "P" and "transparency" in img.info)
        img = img.convert("RGBA" if alpha else "RGB")
    if pil_fmt == "WEBP":
        opts ={"lossless": True, "quality": 100, "method": 6} if lossless else {"quality": quality, "method": 4}
    elif pil_fmt == "AVIF":
        opts = {"quality": 100 if lossless else quality, "speed": 6}
    elif pil_fmt == "PNG":
        opts = {"optimize": True}
    elif pil_fmt == "GIF":
        if img.mode not in ("P", "L"):
            img = img.convert("RGBA" if img.mode in ("RGBA", "LA") else "RGB").quantize(256)
        opts = {"optimize": True}
    elif pil_fmt == "BMP" and img.mode not in ("RGB", "L", "P"):
        img = img.convert("RGB")
    elif pil_fmt == "TIFF":
        opts = {"compression": "tiff_lzw"}
    elif pil_fmt == "ICO":
        side = min(max(img.size), 256)
        sizes = [(s, s) for s in ICO_SIZES if s <= side] or [(16, 16)]
        opts = {"sizes": sizes}
        img = img.convert("RGBA")

    buf = io.BytesIO()
    try:
        img.save(buf, pil_fmt, **opts)              # no exif= / icc_profile= passed: metadata is dropped
    except (OSError, ValueError) as e:
        raise ToolError(f"Could not encode the image as {ext.upper()}: {type(e).__name__}.")
    out = buf.getvalue()
    if len(out) > MAX_OUT_BYTES:
        raise ToolError("The converted image is too large. Try a smaller size or lower quality.")
    info = {"src_fmt": src_fmt, "src_size": src_size, "src_bytes": len(data), "frames": frames,
            "out_size": img.size, "out_bytes": len(out), "ext": ext, "mime": mime,
            "previewable": previewable, "flattened": flattened}
    return out, info


def _fmt_bytes(n: int) -> str:
    for unit in ("bytes", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:,} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024


def _preview_uri(out: bytes, info: dict) -> str:
    if info["previewable"]:
        return f"data:{info['mime']};base64," + base64.b64encode(out).decode("ascii")
    img = Image.open(io.BytesIO(out))               # e.g. TIFF: browsers cannot show it, preview as PNG
    img.thumbnail((1200, 1200))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def handler(action, form, files):
    data = files.get("file")
    if not data:
        raise ToolError("Choose an image to convert.")
    quality = clamp_int(form.get("quality", "85") or "85", 1, 100, "Quality")
    if not _SLOTS.acquire(blocking=False):
        raise ToolError("The converter is busy. Please try again in a few seconds.")
    try:
        out, info = convert(data, form.get("format", "webp"), quality, form.get("width", ""), form.get("height", ""),
                            form.get("background", "#ffffff"), form.get("lossless") == "on")
    finally:
        _SLOTS.release()

    sw, sh = info["src_size"]
    ow, oh = info["out_size"]
    change = (info["out_bytes"] - info["src_bytes"]) / info["src_bytes"] * 100
    notes = [f"{info['src_fmt']} {sw}×{sh}, {_fmt_bytes(info['src_bytes'])} → {info['ext'].upper()} {ow}×{oh}, "
             f"{_fmt_bytes(info['out_bytes'])} ({change:+.0f}%)."]
    if info["frames"] > 1:
        notes.append(f"Animated image: only the first of {info['frames']} frames was converted.")
    if info["flattened"]:
        notes.append(f"{info['ext'].upper()} has no transparency: transparent areas were filled with the background colour.")
    notes.append("Metadata (EXIF, GPS location, camera details) was removed.")

    href = f"data:{info['mime']};base64," + base64.b64encode(out).decode("ascii")
    block = Markup('<div class="img-out"><a class="btn primary" href="{}" download="converted.{}">'
                   '⬇ Download {} ({})</a></div>').format(href, info["ext"], info["ext"].upper(),
                                                         _fmt_bytes(info["out_bytes"]))
    return Result("", image=_preview_uri(out, info), notes=notes, block=block)


register(Tool(
    slug="image-convert", title="Image Converter",
    description="Convert images between PNG, JPG, WebP, AVIF, GIF, BMP, TIFF and ICO, with optional resize. "
                "Metadata such as GPS location is removed.",
    fields=(
        Field("file", "Image (PNG, JPEG, GIF, WebP, BMP, AVIF)", "file",
              accept="image/png,image/jpeg,image/gif,image/webp,image/bmp,image/avif,.avif", upload="image"),
        Field("format", "Convert to", "select",
              (("webp", "WebP"), ("jpg", "JPG / JPEG"), ("png", "PNG"), ("avif", "AVIF"), ("gif", "GIF"),
               ("bmp", "BMP"), ("tiff", "TIFF"), ("ico", "ICO (favicon)")), "webp"),
        Field("quality", "Quality (1-100, for JPG / WebP / AVIF)", "number", default="85", lo=1, hi=100),
        Field("width", "Width in px (optional; blank = keep)", "number", lo=1, hi=MAX_SIDE),
        Field("height", "Height in px (optional; one value keeps the aspect ratio)", "number", lo=1, hi=MAX_SIDE),
        Field("background", "Background for transparent areas (JPG / BMP)", "text", default="#ffffff", max_len=7),
        Field("lossless", "Lossless (WebP / AVIF)", "checkbox"),
    ),
    actions=(Action("convert", "Convert image", True),),
    handler=handler, group="Converters", rate="10 per minute",
))
