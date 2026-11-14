"""Image converter: formats, resize, transparency, metadata stripping, hostile files, limits."""
import base64
import io
import re

import pytest
from PIL import Image

from app.uploads import validate_image
from tests.test_uploads import text, up

MB = 1024 * 1024


def make(fmt="PNG", size=(64, 40), mode="RGBA", color=(200, 30, 30, 128), **save):
    img = Image.new(mode, size, color if mode != "RGB" else color[:3])
    buf = io.BytesIO()
    img.save(buf, fmt, **save)
    return buf.getvalue()


def convert(run, data, name="in.png", **fields):
    fields.setdefault("format", "webp")
    return up(run, "image-convert", data, name, **fields)


def output(resp):
    """(mime, filename, decoded image) from the Download link."""
    html = resp.get_data(as_text=True)
    m = re.search(r'href="data:([\w/+.-]+);base64,([A-Za-z0-9+/=]+)" download="(converted\.\w+)"', html)
    assert m, text(resp)
    raw = base64.b64decode(m.group(2))
    return m.group(1), m.group(3), Image.open(io.BytesIO(raw)), raw


@pytest.mark.parametrize("target,pil_fmt,mime", [
    ("jpg", "JPEG", "image/jpeg"), ("png", "PNG", "image/png"), ("webp", "WEBP", "image/webp"),
    ("avif", "AVIF", "image/avif"), ("gif", "GIF", "image/gif"), ("bmp", "BMP", "image/bmp"),
    ("tiff", "TIFF", "image/tiff"), ("ico", "ICO", "image/x-icon")])
def test_every_output_format(run, target, pil_fmt, mime):
    r = convert(run, make(), format=target)
    got_mime, name, img, _ = output(r)
    assert (got_mime, name, img.format) == (mime, f"converted.{target}", pil_fmt)
    assert "Security check passed" in text(r) and '<img class="preview' in r.get_data(as_text=True)


def test_png_to_jpg_fills_transparency_with_background(run):
    r = convert(run, make(color=(0, 0, 0, 0)), format="jpg", background="#00ff00")
    _, _, img, _ = output(r)
    assert img.mode == "RGB" and img.getpixel((5, 5))[1] > 240 and img.getpixel((5, 5))[0] < 15
    assert "transparent areas were filled" in text(r)
    assert "Background must be a hex colour" in text(convert(run, make(), format="jpg", background="red"))


def test_transparency_kept_for_webp_and_png(run):
    for target in ("webp", "png", "avif"):
        _, _, img, _ = output(convert(run, make(color=(0, 0, 255, 0)), format=target))
        assert img.convert("RGBA").getpixel((3, 3))[3] < 10, target


def test_resize_keeps_aspect_ratio_or_uses_exact_size(run):
    assert output(convert(run, make(size=(400, 200)), width="100"))[2].size == (100, 50)
    assert output(convert(run, make(size=(400, 200)), height="50"))[2].size == (100, 50)
    assert output(convert(run, make(size=(400, 200)), width="30", height="30"))[2].size == (30, 30)
    assert "Width must be between" in text(convert(run, make(), width="99999"))
    assert "too large" in text(convert(run, make(), width="8000", height="8000"))


def test_quality_changes_size_and_is_validated(run):
    photo = Image.effect_noise((300, 300), 60).convert("RGB")
    buf = io.BytesIO(); photo.save(buf, "PNG")
    small = output(convert(run, buf.getvalue(), format="jpg", quality="20"))[3]
    big = output(convert(run, buf.getvalue(), format="jpg", quality="95"))[3]
    assert len(small) < len(big)
    assert "Quality must be between" in text(convert(run, buf.getvalue(), format="jpg", quality="0"))


def test_lossless_webp_is_pixel_exact(run):
    src = Image.effect_noise((50, 50), 80).convert("RGB")
    buf = io.BytesIO(); src.save(buf, "PNG")
    _, _, img, _ = output(convert(run, buf.getvalue(), format="webp", lossless="on"))
    assert img.convert("RGB").tobytes() == src.tobytes()


def test_exif_and_gps_are_removed_and_rotation_applied(run):
    img = Image.new("RGB", (80, 40), "red")
    exif = Image.Exif()
    exif[0x0112] = 6                                           # orientation: rotate 90 when shown
    exif[0x010F] = "SecretCam"                                  # camera maker
    exif[0x8825] = {1: "N", 2: (23.0, 48.0, 0.0)}               # GPS info
    buf = io.BytesIO(); img.save(buf, "JPEG", exif=exif.tobytes())
    r = convert(run, buf.getvalue(), "photo.jpg", format="jpg")
    _, _, out, raw = output(r)
    assert out.size == (40, 80)                                 # rotation applied before EXIF dropped
    assert not out.getexif() and b"SecretCam" not in raw and b"Exif" not in raw
    assert "Metadata (EXIF, GPS location" in text(r)


def test_animated_gif_converts_first_frame_with_note(run):
    frames = [Image.new("RGB", (20, 20), c) for c in ("red", "green", "blue")]
    buf = io.BytesIO(); frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100)
    r = convert(run, buf.getvalue(), "a.gif", format="png")
    assert output(r)[2].convert("RGB").getpixel((1, 1))[0] > 200 and "first of 3 frames" in text(r)


def test_ico_contains_standard_sizes(run):
    _, _, ico, _ = output(convert(run, make(size=(300, 300)), format="ico"))
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= set(ico.info["sizes"])


def test_bmp_and_avif_inputs(run):
    for fmt, name in (("BMP", "x.bmp"), ("AVIF", "x.avif")):
        data = make(fmt, mode="RGB")
        assert validate_image(data) == fmt
        assert output(convert(run, data, name, format="png"))[2].format == "PNG"


def test_hostile_or_fake_images_rejected(run):
    payload = b"<?php system($_GET['c']); ?>"
    for fmt, name in (("PNG", "a.png"), ("BMP", "a.bmp"), ("AVIF", "a.avif"), ("WEBP", "a.webp")):
        r = convert(run, make(fmt, mode="RGB") + payload, name)
        assert "Rejected" in text(r), fmt                       # data appended after the image
    assert "Rejected" in text(convert(run, b"MZ\x90\x00" + b"\0" * 100, "evil.png"))
    assert "Rejected" in text(convert(run, b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', "x.png"))
    noisy = io.BytesIO(); Image.effect_noise((200, 200), 80).save(noisy, "PNG")
    cut = noisy.getvalue()[: len(noisy.getvalue()) // 2] + b"\x00\x00\x00\x00IEND\xaeB`\x82"   # truncated pixels
    assert "not a valid" in text(convert(run, cut, "cut.png"))


def test_upload_limit_is_10mb(app, client):
    assert app.config["UPLOAD_LIMITS"]["image-convert"] == 10 * MB
    assert "Max file size: 10 MB" in client.get("/tool/image-convert").get_data(as_text=True)


def test_page_has_no_inline_script_or_style(client):
    html = client.get("/tool/image-convert").get_data(as_text=True)
    assert not re.search(r"\sstyle\s*=", html) and not re.search(r"\son[a-z]+\s*=", html)
