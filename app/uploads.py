"""Upload screening: every uploaded file is checked here BEFORE any tool parses it.

Principles
- Files are never executed, never written to disk (see SafeRequest in app/__init__.py) and never
  served back. The client's filename and Content-Type are ignored; only the bytes are trusted
  (and even those only after the checks below).
- Allow-list, not block-list: each profile says exactly what a valid file looks like; everything
  else is rejected with a clear reason. Known-dangerous types (executables, scripts, legacy Office
  with macros, archives) get a specific message.
- Archive formats (.xlsx is a ZIP) are inspected without trusting their headers: bounded reads
  defeat zip bombs, entry names are checked for path traversal, and macro / embedded-object /
  external-link parts are refused.
"""
from __future__ import annotations

import io
import posixpath
import zipfile

from .registry import ToolError

# ------------------------------------------------------------------ magic-byte sniffing
_DANGEROUS = (
    (b"MZ", "a Windows executable (.exe/.dll)"),
    (b"\x7fELF", "a Linux executable"),
    (b"\xcf\xfa\xed\xfe", "a macOS executable"),
    (b"\xfe\xed\xfa\xcf", "a macOS executable"),
    (b"\xca\xfe\xba\xbe", "a Java class / macOS universal binary"),
    (b"#!", "a script"),
    (b"%PDF", "a PDF document"),
    (b"Rar!", "a RAR archive"),
    (b"7z\xbc\xaf\x27\x1c", "a 7-Zip archive"),
    (b"\x1f\x8b", "a gzip archive"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "a legacy Office / OLE file (.xls, .doc; can carry macros) or an encrypted workbook"),
)
_TEXT_DANGEROUS = (b"<?php", b"<script", b"<html", b"<!doctype html", b"<svg", b"<?xml", b"<%", b"@echo")


def sniff_dangerous(data: bytes) -> str | None:
    head = data[:16]
    for magic, label in _DANGEROUS:
        if head.startswith(magic):
            return label
    text_head = data[:64].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if any(text_head.startswith(t) for t in _TEXT_DANGEROUS):
        return "a script or markup file"
    return None


def _reject_dangerous(data: bytes, expected: str) -> None:
    label = sniff_dangerous(data)
    if label:
        raise ToolError(f"Rejected: this looks like {label}, not {expected}.")


# ------------------------------------------------------------------ .xlsx
XLSX_MAX_ENTRIES = 1000
XLSX_MAX_ENTRY_BYTES = 25 * 1024 * 1024       # real (decompressed) bytes per part
XLSX_MAX_TOTAL_BYTES = 60 * 1024 * 1024       # real bytes for the whole package
XLSX_MAX_RATIO = 150                          # compression ratio ceiling (bomb indicator)

_XLSX_ALLOWED_EXT = {".xml", ".rels", ".png", ".jpg", ".jpeg", ".gif", ".emf", ".wmf", ".vml", ".bin"}
# Never legitimate in a plain data workbook; each is a known abuse vector.
_XLSX_BLOCKED_DIRS = (
    "xl/macrosheets/", "xl/dialogsheets/", "xl/activex/", "xl/embeddings/", "xl/externallinks/",
    "xl/ctrlprops/", "customui/", "xl/vbaproject", "xl/_rels/vbaproject",
)


def _xlsx_problem(name: str) -> str | None:
    n = name.replace("\\", "/")
    if "\\" in name or "\x00" in name or n.startswith("/") or ".." in posixpath.normpath(n).split("/"):
        return "contains an unsafe file path (path traversal)"
    low = n.lower()
    for blocked in _XLSX_BLOCKED_DIRS:
        if low.startswith(blocked):
            kind = {"xl/macrosheets/": "Excel 4 macro sheets", "xl/activex/": "ActiveX controls",
                    "xl/embeddings/": "embedded objects", "xl/externallinks/": "external workbook links",
                    "customui/": "custom UI / ribbon code"}.get(blocked, "a macro project or controls")
            return f"contains {kind}"
    if "vbaproject" in low or low.endswith(".vba"):
        return "contains a VBA macro project"
    # NB: posixpath.splitext("_rels/.rels") has no extension (dot-file), so handle .rels explicitly.
    ext = ".rels" if low.endswith(".rels") else posixpath.splitext(low)[1]
    if ext not in _XLSX_ALLOWED_EXT:
        return f"contains an unexpected file type ({ext or 'no extension'})"
    if ext == ".bin" and not low.startswith("xl/printersettings/"):
        return "contains an opaque binary part"
    return None


def validate_xlsx(data: bytes) -> dict:
    """Inspect an .xlsx package. Returns facts for display; raises ToolError if anything is suspicious."""
    if not data.startswith(b"PK\x03\x04"):
        _reject_dangerous(data, "an .xlsx workbook")
        raise ToolError("Rejected: not an .xlsx workbook (only the modern .xlsx format is accepted).")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ToolError("Rejected: the file is corrupted or not a valid .xlsx archive.")
    infos = zf.infolist()
    if not infos or len(infos) > XLSX_MAX_ENTRIES:
        raise ToolError(f"Rejected: unusual archive structure ({len(infos)} entries).")

    names = {i.filename for i in infos}
    if len(names) != len(infos):
        raise ToolError("Rejected: the archive contains duplicate entry names.")
    if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
        raise ToolError("Rejected: not an Excel workbook (required parts are missing).")

    real_total = 0
    for info in infos:
        why = _xlsx_problem(info.filename)
        if why:
            raise ToolError(f"Rejected: the workbook {why}. Remove it (or save as a plain .xlsx) and try again.")
        if info.flag_bits & 0x1:
            raise ToolError("Rejected: the archive is password-protected.")
        if info.is_dir():
            continue
        if info.file_size > XLSX_MAX_ENTRY_BYTES or (
                info.compress_size and info.file_size / info.compress_size > XLSX_MAX_RATIO and info.file_size > 1024 * 1024):
            raise ToolError("Rejected: the archive looks like a decompression bomb.")
        # Headers can lie; read with a hard cap to measure the REAL decompressed size.
        try:
            with zf.open(info) as fh:
                chunk = fh.read(XLSX_MAX_ENTRY_BYTES + 1)
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, OSError):
            raise ToolError("Rejected: the archive is corrupted or uses an unsupported feature.")
        if len(chunk) > XLSX_MAX_ENTRY_BYTES:
            raise ToolError("Rejected: the archive looks like a decompression bomb.")
        real_total += len(chunk)
        if real_total > XLSX_MAX_TOTAL_BYTES:
            raise ToolError("Rejected: the workbook expands to an unreasonable size.")
        low = info.filename.lower()
        if low.endswith((".xml", ".rels")):
            head = chunk.lower()
            if b"<!doctype" in head or b"<!entity" in head:
                raise ToolError("Rejected: an XML part declares a DTD/entity (XXE / entity-bomb technique).")
            if low == "[content_types].xml" and b"macroenabled" in head:
                raise ToolError("Rejected: macro-enabled workbook (.xlsm). Save it as a plain .xlsx.")
            if b'targetmode="external"' in head and low != "[content_types].xml":
                if b"oleobject" in head or b"externallink" in head or b"/package" in head:
                    raise ToolError("Rejected: the workbook links to external objects or files.")
    return {"entries": len(infos), "unpacked_bytes": real_total}


# ------------------------------------------------------------------ images
_PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"


def detect_image(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "GIF"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def validate_image(data: bytes) -> str:
    """Magic bytes must match a raster image and NOTHING may follow the image data
    (the classic way to hide a payload inside a picture)."""
    fmt = detect_image(data)
    if fmt is None:
        _reject_dangerous(data, "an image")
        raise ToolError("Rejected: not a PNG, JPEG, GIF or WebP image.")
    tail_ok = {
        "PNG": data.endswith(_PNG_END),
        "JPEG": data.rstrip(b"\x00").endswith(b"\xff\xd9"),
        "GIF": data.rstrip(b"\x00").endswith(b";"),
        "WEBP": len(data) in (int.from_bytes(data[4:8], "little") + 8, int.from_bytes(data[4:8], "little") + 9),
    }[fmt]
    if not tail_ok:
        raise ToolError("Rejected: the image contains extra data after its end (possible hidden payload) "
                        "or is truncated.")
    return fmt


# ------------------------------------------------------------------ certificates
def validate_cert(data: bytes) -> str:
    if b"PRIVATE KEY" in data:
        raise ToolError("Rejected: this file contains a private key. Never upload private keys; "
                        "upload only the public certificate.")
    head = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"-----BEGIN"):
        if b"\x00" in data:
            raise ToolError("Rejected: the PEM file contains binary data.")
        return "PEM"
    if data[:1] == b"\x30" and sniff_dangerous(data) is None:
        return "DER"
    _reject_dangerous(data, "a certificate")
    raise ToolError("Rejected: not a PEM or DER certificate.")


# ------------------------------------------------------------------ entry point
def screen(profile: str, data: bytes) -> list[str]:
    """Validate an upload for the given profile. Returns human-readable 'what we checked' notes."""
    if not data:
        raise ToolError("The uploaded file is empty.")
    if profile == "xlsx":
        info = validate_xlsx(data)
        return [f"Security check passed: valid .xlsx ({info['entries']} parts) with no macros, embedded objects, "
                "external links or DTDs. Formulas are never evaluated."]
    if profile == "image":
        fmt = validate_image(data)
        return [f"Security check passed: genuine {fmt} image with no trailing data."]
    if profile == "cert":
        return [f"Security check passed: {validate_cert(data)} certificate file, no private key."]
    raise ValueError(f"unknown upload profile {profile!r}")  # programmer error, never user-triggered


def limit_for(tool, cfg) -> int:
    """Largest file (bytes) a tool accepts."""
    return cfg["UPLOAD_LIMITS"].get(tool.slug, cfg["MAX_UPLOAD_BYTES"])


def request_limit_for(tool, cfg) -> int:
    """Largest request body (bytes) for a tool: biggest file + slack, or the text-only ceiling."""
    files = [f for f in tool.fields if f.kind == "file"]
    if not files:
        return cfg["MAX_TEXT_REQUEST"]
    return limit_for(tool, cfg) + cfg["REQUEST_SLACK"]


def fmt_limit(n: int) -> str:
    mb = n / (1024 * 1024)
    return f"{mb:g} MB"
