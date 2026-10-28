"""XML formatter / minifier / validator. Parsing uses defusedxml (no XXE, no entity bombs)."""
import re

from defusedxml import DefusedXmlException
from defusedxml.minidom import parseString

from ..highlight import xml_html
from ..registry import Action, Field, Result, Tool, ToolError, register


def _parse(text: str):
    if not text.strip():
        raise ToolError("Paste some XML first.")
    try:
        # forbid_dtd=True rejects DOCTYPE entirely (blocks XXE / billion laughs).
        return parseString(text.encode("utf-8"), forbid_dtd=True, forbid_entities=True,
                           forbid_external=True)
    except DefusedXmlException:
        raise ToolError("DOCTYPE / entity declarations are not allowed for security reasons.")
    except Exception as e:
        raise ToolError(f"Invalid XML: {e}")


def format_xml(text: str, indent: int = 2) -> str:
    dom = _parse(text)
    pretty = dom.toprettyxml(indent=" " * indent)
    lines = [ln for ln in pretty.splitlines() if ln.strip()]
    return "\n".join(lines)


def minify_xml(text: str) -> str:
    dom = _parse(text)
    xml = dom.toxml()
    return re.sub(r">\s+<", "><", xml)


def handler(action, form, files):
    text = form["input"]
    if action == "format":
        out = format_xml(text, 2 if form.get("indent", "2") == "2" else 4)
        return Result(out, html=xml_html(out))
    if action == "minify":
        out = minify_xml(text)
        return Result(out, html=xml_html(out))
    _parse(text)
    return Result("Well-formed XML ✔")


register(Tool(
    slug="xml-format", title="XML Formatter",
    description="Format, minify and validate XML. DTDs and entities are rejected.",
    fields=(
        Field("input", "XML input", placeholder="<root><a>1</a></root>", required=True),
        Field("indent", "Indentation", "select", (("2", "2 spaces"), ("4", "4 spaces")), "2"),
    ),
    actions=(Action("format", "Format", True), Action("minify", "Minify"), Action("validate", "Validate")),
    handler=handler, group="Formatters", layout="split", ext="xml",
    sample={"input": '<?xml version="1.0"?><catalog><book id="1"><title>Flask &amp; HTMX</title><price>29.99</price></book></catalog>'},
))
