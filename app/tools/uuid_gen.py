"""UUID generator (v4, v7, v5, v3, nil, max) and inspector.

v1 is intentionally omitted: it embeds the host's MAC address.
"""
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

from ..registry import Action, Field, Result, Tool, ToolError, clamp_int, register

NAMESPACES = {"dns": uuid.NAMESPACE_DNS, "url": uuid.NAMESPACE_URL,
              "oid": uuid.NAMESPACE_OID, "x500": uuid.NAMESPACE_X500}


def uuid7() -> uuid.UUID:
    """RFC 9562 UUIDv7: 48-bit unix ms timestamp + 74 random bits."""
    ms = time.time_ns() // 1_000_000
    rand_a, rand_b = secrets.randbits(12), secrets.randbits(62)
    value = ((ms & (2**48 - 1)) << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


def _fmt(u: uuid.UUID, upper: bool, hyphens: bool, braces: bool) -> str:
    s = str(u) if hyphens else u.hex
    s = s.upper() if upper else s
    return "{" + s + "}" if braces else s


def _namespace(form) -> uuid.UUID:
    ns = form.get("namespace", "dns")
    if ns in NAMESPACES:
        return NAMESPACES[ns]
    try:
        return uuid.UUID(form.get("custom_ns", "").strip())
    except ValueError:
        raise ToolError("Custom namespace must be a valid UUID.")


def inspect(text: str) -> str:
    try:
        u = uuid.UUID(text.strip().strip("{}"))
    except ValueError:
        raise ToolError("Not a valid UUID.")
    variant = {uuid.RFC_4122: "RFC 4122 / 9562", uuid.RESERVED_NCS: "NCS (reserved)",
               uuid.RESERVED_MICROSOFT: "Microsoft (reserved)", uuid.RESERVED_FUTURE: "Future (reserved)"}.get(u.variant, "?")
    lines = [f"UUID     : {u}", f"Hex      : {u.hex}", f"Integer  : {u.int}", f"Version  : {u.version}", f"Variant  : {variant}"]
    if u.int == 0:
        lines.append("Note     : nil UUID")
    if u.version == 7:
        ts = datetime.fromtimestamp((u.int >> 80) / 1000, timezone.utc)
        lines.append(f"Timestamp: {ts:%Y-%m-%d %H:%M:%S.%f} UTC")
    elif u.version == 1:
        ts = datetime(1582, 10, 15, tzinfo=timezone.utc) + timedelta(microseconds=u.time // 10)
        lines.append(f"Timestamp: {ts:%Y-%m-%d %H:%M:%S} UTC")
    return "\n".join(lines)


def handler(action, form, files):
    if action == "inspect":
        return Result(inspect(form.get("uuid", "")))
    version = form.get("version", "4")
    upper, hyphens, braces = form.get("upper") == "on", form.get("hyphens") == "on", form.get("braces") == "on"
    if version in ("3", "5"):
        name = form.get("name", "")
        if not name:
            raise ToolError("Enter a name to hash.")
        fn = uuid.uuid3 if version == "3" else uuid.uuid5
        return Result(_fmt(fn(_namespace(form), name), upper, hyphens, braces),
                      notes=["Name-based UUIDs are deterministic: same namespace + name → same UUID."])
    count = clamp_int(form.get("count", "5"), 1, 100, "Count")
    if version == "nil":
        out = [uuid.UUID(int=0)] * count
    elif version == "max":
        out = [uuid.UUID(int=2**128 - 1)] * count
    elif version == "7":
        out = sorted(uuid7() for _ in range(count))
    elif version == "4":
        out = [uuid.UUID(bytes=secrets.token_bytes(16), version=4) for _ in range(count)]
    else:
        raise ToolError("Unknown UUID version.")
    return Result("\n".join(_fmt(u, upper, hyphens, braces) for u in out))


register(Tool(
    slug="uuid", title="UUID Generator",
    description="Generate v4 / v7 / v5 / v3 UUIDs, or inspect an existing one.",
    fields=(
        Field("version", "Version", "select",
              (("4", "v4 — random"), ("7", "v7 — time-ordered"), ("5", "v5 — name (SHA-1)"),
               ("3", "v3 — name (MD5)"), ("nil", "Nil (all zeros)"), ("max", "Max (all ones)")), "4"),
        Field("count", "How many (random versions)", "number", default="5", lo=1, hi=100),
        Field("hyphens", "Hyphens", "checkbox", default="on"),
        Field("upper", "Uppercase", "checkbox"),
        Field("braces", "Wrap in { }", "checkbox"),
        Field("namespace", "Namespace (v3/v5)", "select",
              (("dns", "DNS"), ("url", "URL"), ("oid", "OID"), ("x500", "X.500"), ("custom", "Custom UUID…")), "dns"),
        Field("custom_ns", "Custom namespace UUID", "text", max_len=64),
        Field("name", "Name (v3/v5)", "text", max_len=500),
        Field("uuid", "UUID to inspect", "text", max_len=64),
    ),
    actions=(Action("generate", "Generate", True), Action("inspect", "Inspect")),
    handler=handler, group="Generators",
))
