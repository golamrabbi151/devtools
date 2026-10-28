"""JSON formatter / minifier / validator."""
import json

from ..highlight import json_html
from ..registry import Action, Field, Result, Tool, ToolError, register


def _loads(text: str):
    if not text.strip():
        raise ToolError("Paste some JSON first.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ToolError(f"Invalid JSON: {e.msg} (line {e.lineno}, column {e.colno})")


def format_json(text: str, indent: int | str = 2, sort_keys: bool = False) -> str:
    return json.dumps(_loads(text), indent=indent, sort_keys=sort_keys, ensure_ascii=False)


def minify_json(text: str, sort_keys: bool = False) -> str:
    return json.dumps(_loads(text), separators=(",", ":"), sort_keys=sort_keys, ensure_ascii=False)


def _result(out: str) -> Result:
    return Result(out, html=json_html(out))


def handler(action, form, files):
    text = form["input"]
    sort = form.get("sort") == "on"
    if action == "format":
        indent = {"2": 2, "4": 4, "tab": "\t"}.get(form.get("indent", "2"), 2)
        return _result(format_json(text, indent, sort))
    if action == "minify":
        return _result(minify_json(text, sort))
    _loads(text)
    return Result("Valid JSON ✔")


register(Tool(
    slug="json-format", title="JSON Formatter",
    description="Format, minify and validate JSON with syntax colours.",
    fields=(
        Field("input", "JSON input", placeholder='{"hello": "world"}', required=True),
        Field("indent", "Indentation", "select", (("2", "2 spaces"), ("4", "4 spaces"), ("tab", "Tab")), "2"),
        Field("sort", "Sort keys", "checkbox"),
    ),
    actions=(Action("format", "Format", True), Action("minify", "Minify"), Action("validate", "Validate")),
    handler=handler, group="Formatters", layout="split", ext="json",
    sample={"input": '{"name":"DevTools","version":1.2,"tags":["json","xml"],"active":true,"owner":null}'},
))
