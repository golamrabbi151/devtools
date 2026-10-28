"""Cron expression parser: human description, per-field breakdown, next run times."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cron_descriptor import get_description
from cronsim import CronSim, CronSimError
from markupsafe import Markup

from ..registry import Action, Field, Result, Tool, ToolError, register

FIELDS = (("Minute", "0–59"), ("Hour", "0–23"), ("Day of month", "1–31"),
          ("Month", "1–12 · JAN–DEC"), ("Day of week", "0–6 · SUN–SAT"))

PRESETS = (
    ("Every minute", "* * * * *"),
    ("Every 5 minutes", "*/5 * * * *"),
    ("Hourly", "0 * * * *"),
    ("Daily at midnight", "0 0 * * *"),
    ("Weekdays at 9:00", "0 9 * * 1-5"),
    ("Every Monday 8:30", "30 8 * * 1"),
    ("1st of month", "0 0 1 * *"),
    ("Yearly", "0 0 1 1 *"),
)


def describe(expr: str) -> str:
    try:
        return get_description(expr)
    except Exception:
        raise ToolError("Could not describe this cron expression.")


def next_runs(expr: str, tz, count: int = 10) -> list[datetime]:
    try:
        it = CronSim(expr, datetime.now(tz))
    except CronSimError as e:
        raise ToolError(f"Invalid cron expression: {e}")
    runs = []
    try:
        for _ in range(count):
            runs.append(next(it))
    except StopIteration:
        pass
    except CronSimError as e:
        raise ToolError(f"Invalid cron expression: {e}")
    return runs


def relative(delta: timedelta) -> str:
    s = max(int(delta.total_seconds()), 0)
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if s >= size:
            n = s // size
            return f"in {n} {unit}{'s' if n != 1 else ''}"
    return "in under a minute"


def _block(expr: str, meaning: str, tz_name: str, runs: list[datetime], now: datetime) -> Markup:
    cards = Markup("").join(
        Markup('<div class="cron-field"><code>{}</code><strong>{}</strong><span>{}</span></div>').format(tok, label, rng)
        for tok, (label, rng) in zip(expr.split(), FIELDS)
    )
    rows = Markup("").join(
        Markup("<tr><td class=\"n\">{}</td><td>{}</td><td><strong>{}</strong></td><td class=\"muted\">{}</td></tr>").format(
            i + 1, f"{r:%a, %d %b %Y}", f"{r:%H:%M:%S}", relative(r - now))
        for i, r in enumerate(runs)
    )
    table = (Markup('<table class="runs"><tbody>{}</tbody></table>').format(rows) if runs
             else Markup('<p class="muted">No upcoming runs.</p>'))
    return Markup(
        '<p class="cron-desc">{}</p><div class="cron-fields">{}</div>'
        '<h3 class="cron-h">Next {} runs <small>({})</small></h3>{}'
    ).format(meaning, cards, len(runs), tz_name, table)


def handler(action, form, files):
    expr = " ".join(form["input"].split())
    if not expr:
        raise ToolError("Enter a cron expression (5 fields), e.g. */15 9-17 * * 1-5")
    if len(expr.split()) != 5:
        raise ToolError("Standard cron has exactly 5 fields: minute hour day-of-month month day-of-week.")
    tz_name = (form.get("tz") or "UTC").strip()
    try:
        tz = ZoneInfo(tz_name) if tz_name.upper() != "UTC" else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        raise ToolError("Unknown timezone. Use an IANA name such as UTC or Asia/Dhaka.")
    try:
        count = max(1, min(int(form.get("count", "10")), 50))
    except ValueError:
        count = 10

    runs = next_runs(expr, tz, count)  # validates the expression first
    meaning = describe(expr)
    now = datetime.now(tz)
    text = [f"Meaning : {meaning}", f"Timezone: {tz_name}", "", "Next runs:"]
    text += [f"  {i + 1:>2}. {r:%Y-%m-%d %H:%M:%S %Z (%a)}" for i, r in enumerate(runs)]
    return Result("\n".join(text), block=_block(expr, meaning, tz_name, runs, now))


register(Tool(
    slug="cron", title="Cron Expression Parser",
    description="Explain a 5-field cron expression and list upcoming run times.",
    fields=(
        Field("input", "Cron expression", "text", default="*/15 9-17 * * 1-5", placeholder="*/15 9-17 * * 1-5", required=True, max_len=200),
        Field("tz", "Timezone", "text", default="UTC", placeholder="UTC or Asia/Dhaka", max_len=64),
        Field("count", "Runs to show", "select", tuple((str(n), str(n)) for n in (5, 10, 20, 50)), "10"),
    ),
    actions=(Action("parse", "Parse", True),),
    handler=handler, group="Converters", layout="cron", presets=PRESETS, live="input",
))
