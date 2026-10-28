"""Structural JSON diff with coloured output."""
import json
from collections import Counter

from markupsafe import Markup

from ..highlight import diff_table_html, json_html
from ..registry import Action, Field, Result, Tool, ToolError, register

MAX_DIFFS = 2000
Change = tuple  # (kind, path, old, new)  kind: added | removed | changed


def _load(text: str, label: str):
    if not text.strip():
        raise ToolError(f"{label} is empty.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ToolError(f"{label} is invalid JSON: {e.msg} (line {e.lineno}, column {e.colno})")


def _short(v) -> str:
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 120 else s[:117] + "..."


def diff(a, b, path="$", ignore_order=False, out=None) -> list[Change]:
    out = [] if out is None else out
    if len(out) >= MAX_DIFFS:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a.keys() - b.keys():
            out.append(("removed", f"{path}.{k}", a[k], None))
        for k in b.keys() - a.keys():
            out.append(("added", f"{path}.{k}", None, b[k]))
        for k in a.keys() & b.keys():
            diff(a[k], b[k], f"{path}.{k}", ignore_order, out)
    elif isinstance(a, list) and isinstance(b, list):
        if ignore_order:
            key = lambda x: json.dumps(x, sort_keys=True)
            a, b = sorted(a, key=key), sorted(b, key=key)
        for i in range(max(len(a), len(b))):
            p = f"{path}[{i}]"
            if i >= len(a):
                out.append(("added", p, None, b[i]))
            elif i >= len(b):
                out.append(("removed", p, a[i], None))
            else:
                diff(a[i], b[i], p, ignore_order, out)
    elif a != b or type(a) is not type(b):
        out.append(("changed", path, a, b))
    return out


def _text(c: Change) -> str:
    kind, path, old, new = c
    if kind == "added":
        return f"+ added    {path} = {_short(new)}"
    if kind == "removed":
        return f"- removed  {path} = {_short(old)}"
    return f"~ changed  {path}: {_short(old)}  →  {_short(new)}"


def _row(c: Change):
    kind, path, old, new = c
    return (kind, path,
            json_html(_short(old)) if kind != "added" else None,
            json_html(_short(new)) if kind != "removed" else None)


def handler(action, form, files):
    a, b = _load(form["left"], "Left JSON"), _load(form["right"], "Right JSON")
    changes = diff(a, b, ignore_order=form.get("ignore_order") == "on")
    if not changes:
        msg = "Documents are identical ✔"
        return Result(msg, block=Markup('<div class="ok">{}</div>').format(msg))
    changes.sort(key=lambda c: c[1])
    counts = Counter(c[0] for c in changes)
    notes = ["Showing the first %d differences only." % MAX_DIFFS] if len(changes) >= MAX_DIFFS else []
    return Result("\n".join(_text(c) for c in changes), notes=notes,
                  block=diff_table_html([_row(c) for c in changes], counts))


register(Tool(
    slug="json-compare", title="JSON Compare",
    description="Coloured added / removed / changed values between two JSON documents.",
    fields=(
        Field("left", "Left (original)", required=True),
        Field("right", "Right (modified)", required=True),
        Field("ignore_order", "Ignore array order", "checkbox"),
    ),
    actions=(Action("compare", "Compare", True),),
    handler=handler, group="Formatters", layout="compare", ext="txt",
    sample={"left": '{"name": "DevTools", "version": 1, "tags": ["json", "xml"], "active": true}',
            "right": '{"name": "DevTools Web", "version": 2, "tags": ["json", "sql"], "owner": null}'},
))
