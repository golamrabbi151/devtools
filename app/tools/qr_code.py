"""QR code generator (segno, pure Python) and reader (OpenCV)."""
import io

import cv2
import numpy as np
import segno
from PIL import Image

from ..registry import Action, Field, Result, Tool, ToolError, register
from .base64_image import open_image, png_data_uri

MAX_QR_TEXT = 2000  # characters; keeps image size bounded
MAX_SIDE = 4000


def encode_qr(text: str, ecc: str = "m", scale: int = 8, border: int = 2) -> str:
    if not text:
        raise ToolError("Enter the text or URL to encode.")
    if len(text) > MAX_QR_TEXT:
        raise ToolError(f"Text too long for a QR code (max {MAX_QR_TEXT} characters).")
    try:
        qr = segno.make(text, error=ecc if ecc in "lmqh" else "m")
    except Exception:
        raise ToolError("Data does not fit in a QR code.")
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=max(2, min(scale, 20)), border=max(0, min(border, 8)))
    img = Image.open(io.BytesIO(buf.getvalue()))
    return png_data_uri(img)


def decode_qr(data: bytes) -> str:
    img = open_image(data).convert("RGB")
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE))
    arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    detector = cv2.QRCodeDetector()
    text, _, _ = detector.detectAndDecode(arr)
    if not text:
        # retry on greyscale with padding, which helps tight crops
        grey = cv2.copyMakeBorder(cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY), 40, 40, 40, 40,
                                  cv2.BORDER_CONSTANT, value=255)
        text, _, _ = detector.detectAndDecode(grey)
    if not text:
        raise ToolError("No QR code found in the image.")
    return text


def handler(action, form, files):
    if action == "encode":
        uri = encode_qr(form["input"], form.get("ecc", "m"), int(form.get("scale", "8")))
        return Result("", uri, ["Right-click the image to save it."])
    data = files.get("file")
    if not data:
        raise ToolError("Upload an image containing a QR code.")
    return Result(decode_qr(data), notes=["Decoded text is untrusted — do not open links blindly."])


register(Tool(
    slug="qr-code", title="QR Code",
    description="Generate a QR code from text, or read one from an image.",
    fields=(
        Field("input", "Text or URL to encode", max_len=MAX_QR_TEXT),
        Field("ecc", "Error correction", "select", (("l", "Low (7%)"), ("m", "Medium (15%)"), ("q", "Quartile (25%)"), ("h", "High (30%)")), "m"),
        Field("scale", "Pixel size", "select", tuple((str(n), f"{n}x") for n in (4, 8, 12, 16)), "8"),
        Field("file", "Image to decode (PNG, JPEG, GIF, WebP)", "file", accept="image/png,image/jpeg,image/gif,image/webp", upload="image"),
    ),
    actions=(Action("encode", "Generate", True), Action("decode", "Read from image")),
    handler=handler, group="Encoders / Decoders",
))
