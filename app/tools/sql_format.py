"""SQL formatter / minifier."""
import re

import sqlparse

from ..highlight import sql_html
from ..registry import Action, Field, Result, Tool, ToolError, register

MAX_SQL = 200_000  # sqlparse is pure Python; keep worst-case CPU bounded


def handler(action, form, files):
    sql = form["input"]
    if not sql.strip():
        raise ToolError("Paste some SQL first.")
    if len(sql) > MAX_SQL:
        raise ToolError(f"SQL is too long (max {MAX_SQL:,} characters).")
    kw = form.get("keyword_case", "upper")
    if action == "format":
        out = sqlparse.format(
            sql, reindent=True, keyword_case=kw if kw != "keep" else None,
            indent_width=2 if form.get("indent", "2") == "2" else 4,
        ).strip()
    else:
        stripped = sqlparse.format(sql, strip_comments=True)
        out = re.sub(r"\s+", " ", stripped).strip()
    return Result(out, html=sql_html(out))


register(Tool(
    slug="sql-format", title="SQL Formatter",
    description="Pretty-print or minify SQL (dialect-agnostic). The SQL is never executed.",
    fields=(
        Field("input", "SQL input", placeholder="select * from users where id=1", required=True, max_len=200_000),
        Field("keyword_case", "Keywords", "select", (("upper", "UPPER"), ("lower", "lower"), ("keep", "Keep")), "upper"),
        Field("indent", "Indentation", "select", (("2", "2 spaces"), ("4", "4 spaces")), "2"),
    ),
    actions=(Action("format", "Format", True), Action("minify", "Minify")),
    handler=handler, group="Formatters", layout="split", ext="sql",
    sample={"input": 'select u.id,u.name,count(o.id) as orders from users u left join orders o on o.user_id=u.id where u.active=1 group by u.id order by orders desc limit 10'},
))
