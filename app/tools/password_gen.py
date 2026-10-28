"""Password generator using the OS CSPRNG (`secrets`)."""
import math
import secrets

from markupsafe import Markup

from ..highlight import password_html
from ..registry import Action, Field, Result, Tool, ToolError, clamp_int, register

LOWER = "abcdefghijklmnopqrstuvwxyz"
UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
DIGITS = "0123456789"
SYMBOLS = "!@#$%^&*()-_=+[]{};:,.<>?/~"
AMBIGUOUS = set("Il1O0o|`'\"")
_RNG = secrets.SystemRandom()


def build_pools(lower: bool, upper: bool, digits: bool, symbols: bool,
                exclude_ambiguous: bool, exclude_chars: str = "") -> list[str]:
    banned = set(exclude_chars) | (AMBIGUOUS if exclude_ambiguous else set())
    pools = []
    for enabled, chars in ((lower, LOWER), (upper, UPPER), (digits, DIGITS), (symbols, SYMBOLS)):
        if enabled:
            pool = "".join(c for c in chars if c not in banned)
            if pool:
                pools.append(pool)
    if not pools:
        raise ToolError("No characters left to choose from — enable a character set or relax the exclusions.")
    return pools


def generate_password(length: int, pools: list[str]) -> str:
    """Guarantee at least one character from every enabled pool, then shuffle."""
    if length < len(pools):
        raise ToolError(f"Length must be at least {len(pools)} to include every selected character set.")
    allchars = "".join(pools)
    chars = [secrets.choice(p) for p in pools] + [secrets.choice(allchars) for _ in range(length - len(pools))]
    _RNG.shuffle(chars)
    return "".join(chars)


def entropy_bits(length: int, pools: list[str]) -> float:
    return length * math.log2(len("".join(pools)))


def _strength(bits: float) -> str:
    return ("very weak" if bits < 40 else "weak" if bits < 60 else "fair" if bits < 80
            else "strong" if bits < 100 else "very strong")


def handler(action, form, files):
    length = clamp_int(form.get("length", "20"), 4, 128, "Length")
    count = clamp_int(form.get("count", "1"), 1, 20, "Count")
    pools = build_pools(form.get("lower") == "on", form.get("upper") == "on", form.get("digits") == "on",
                        form.get("symbols") == "on", form.get("ambiguous") == "on", form.get("exclude", ""))
    pwds = [generate_password(length, pools) for _ in range(count)]
    bits = entropy_bits(length, pools)
    text = "\n".join(pwds)
    return Result(text, html=Markup("\n").join(password_html(p) for p in pwds),
                  notes=[f"≈ {bits:.0f} bits of entropy — {_strength(bits)}. "
                         "Generated with the server's CSPRNG over your HTTPS connection; never stored or logged."])


register(Tool(
    slug="password", title="Password Generator",
    description="Random passwords from the operating system's secure random source.",
    fields=(
        Field("length", "Length", "number", default="20", lo=4, hi=128),
        Field("count", "How many", "number", default="1", lo=1, hi=20),
        Field("lower", "Lowercase (a-z)", "checkbox", default="on"),
        Field("upper", "Uppercase (A-Z)", "checkbox", default="on"),
        Field("digits", "Digits (0-9)", "checkbox", default="on"),
        Field("symbols", "Symbols (!@#$…)", "checkbox", default="on"),
        Field("ambiguous", "Exclude look-alikes (I l 1 O 0 o)", "checkbox"),
        Field("exclude", "Also exclude these characters", "text", max_len=50),
    ),
    actions=(Action("generate", "Generate", True),),
    handler=handler, group="Generators",
))
