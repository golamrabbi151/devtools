"""Excel (.xlsx) to SQL: CREATE TABLE + INSERT statements.

Reads the workbook with excel_json.convert (same upload screening, streaming read-only parsing,
cached values only, row / cell / time budgets and parallelism slots). Column types are inferred
from the values; every value is emitted as an escaped literal for the chosen dialect.
The SQL is generated as text only and never executed.
"""
import math
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal

from ..highlight import sql_html
from ..registry import Action, Field, Result, Tool, ToolError, register
from .excel_json import _SLOTS, convert

MAX_OUTPUT = 10_000_000          # characters; a few long strings repeated in many cells can blow up
_INT32 = 2 ** 31

DIALECTS = (("mysql", "MySQL / MariaDB"), ("postgres", "PostgreSQL"), ("sqlserver", "SQL Server"), ("sqlite", "SQLite"))
_TYPES = {
    "mysql":     {"bool": "BOOLEAN", "int": "INT", "bigint": "BIGINT", "float": "DOUBLE",
                  "date": "DATE", "datetime": "DATETIME", "time": "TIME", "text": "VARCHAR(255)", "long": "TEXT"},
    "postgres":  {"bool": "BOOLEAN", "int": "INTEGER", "bigint": "BIGINT", "float": "DOUBLE PRECISION",
                  "date": "DATE", "datetime": "TIMESTAMP", "time": "TIME", "text": "TEXT", "long": "TEXT"},
    "sqlserver": {"bool": "BIT", "int": "INT", "bigint": "BIGINT", "float": "FLOAT",
                  "date": "DATE", "datetime": "DATETIME2", "time": "TIME", "text": "NVARCHAR(255)", "long": "NVARCHAR(MAX)"},
    "sqlite":    {"bool": "INTEGER", "int": "INTEGER", "bigint": "INTEGER", "float": "REAL",
                  "date": "TEXT", "datetime": "TEXT", "time": "TEXT", "text": "TEXT", "long": "TEXT"},
}


def _raw(v):
    """Keep native Python types (for type inference); drop values SQL cannot hold."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, timedelta):
        return v.total_seconds()
    if v is None or isinstance(v, (bool, int, float, Decimal, str, datetime, date, dtime)):
        return v
    return str(v)


# ------------------------------------------------------------------ identifiers
def _ident(name: str, dialect: str) -> str:
    name = "".join("_" if ord(c) < 32 or ord(c) == 127 else c for c in str(name)).strip() or "unnamed"
    if dialect == "mysql":
        return "`" + name.replace("`", "``") + "`"
    if dialect == "sqlserver":
        return "[" + name.replace("]", "]]") + "]"
    return '"' + name.replace('"', '""') + '"'


# ------------------------------------------------------------------ type inference
def _kind(values) -> str:
    vals = [v for v in values if v is not None and v != ""]
    if not vals:
        return "text"
    if all(isinstance(v, bool) for v in vals):
        return "bool"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
        return "bigint" if any(not -_INT32 <= v < _INT32 for v in vals) else "int"
    if all(isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) for v in vals):
        return "float"
    if all(isinstance(v, (datetime, date)) for v in vals):
        # openpyxl returns datetime for date-formatted cells; midnight everywhere means a plain date.
        if all(not isinstance(v, datetime) or v.time() == dtime() for v in vals):
            return "date"
        return "datetime"
    if all(isinstance(v, dtime) for v in vals):
        return "time"
    return "long" if any(len(_text(v)) > 255 for v in vals) else "text"


# ------------------------------------------------------------------ literals
def _text(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S") + (f".{v.microsecond:06d}" if v.microsecond else "")
    if isinstance(v, (date, dtime)):
        return v.isoformat()
    return str(v)


def _string(s: str, dialect: str) -> str:
    s = s.replace("'", "''")
    if dialect == "mysql":                 # MySQL treats backslash as an escape inside '...'
        s = s.replace("\\", "\\\\")
    return ("N'" if dialect == "sqlserver" else "'") + s + "'"


def _literal(v, kind: str, dialect: str) -> str:
    if v is None or (v == "" and kind != "text" and kind != "long"):
        return "NULL"
    if kind == "bool":
        return ("1" if v else "0") if dialect in ("sqlserver", "sqlite") else ("TRUE" if v else "FALSE")
    if kind in ("int", "bigint", "float"):
        return _text(v)
    if kind == "date" and isinstance(v, datetime):
        v = v.date()
    return _string(_text(v), dialect)


# ------------------------------------------------------------------ SQL generation
def _table_sql(table: str, columns: list, rows: list, dialect: str, create: bool, drop: bool, batch: int) -> list[str]:
    kinds = [_kind([r[i] for r in rows]) for i in range(len(columns))]
    t = _ident(table, dialect)
    cols = ", ".join(_ident(c, dialect) for c in columns)
    out = []
    if drop:
        out.append(f"DROP TABLE IF EXISTS {t};")
    if create:
        types = _TYPES[dialect]
        body = ",\n".join(f"  {_ident(c, dialect)} {types[k]}" for c, k in zip(columns, kinds))
        out.append(f"CREATE TABLE {t} (\n{body}\n);")
    for start in range(0, len(rows), batch):
        chunk = rows[start:start + batch]
        values = [
            "(" + ", ".join(_literal(v, k, dialect) for v, k in zip(r, kinds)) + ")" for r in chunk
        ]
        if batch == 1:
            out.append(f"INSERT INTO {t} ({cols}) VALUES {values[0]};")  # nosec B608 - generated text, never executed
        else:
            out.append(f"INSERT INTO {t} ({cols}) VALUES\n  " + ",\n  ".join(values) + ";")  # nosec B608 - generated text
    return out


def _columns_and_rows(rows: list, header: bool):
    """Normalise convert() output to (column names, list of equal-length value lists)."""
    if header:
        columns = list(dict.fromkeys(k for r in rows for k in r))   # union, first-seen order
        return columns, [[r.get(c) for c in columns] for r in rows]
    width = max(len(r) for r in rows)
    return [f"column_{i + 1}" for i in range(width)], [list(r) + [None] * (width - len(r)) for r in rows]


def handler(action, form, files):
    data = files.get("file")
    if not data:
        raise ToolError("Choose an .xlsx file to convert.")
    sheet = form.get("sheet", "").strip()
    table = form.get("table", "").strip()
    dialect = form.get("dialect", "mysql")
    if dialect not in _TYPES:
        raise ToolError("Unknown SQL dialect.")
    batch = {"1": 1, "100": 100, "500": 500, "1000": 1000}.get(form.get("batch", "100"), 100)
    header = form.get("header") == "on"

    if not _SLOTS.acquire(blocking=False):
        raise ToolError("The converter is busy with other files. Please try again in a few seconds.")
    try:
        sheets, stats = convert(data, sheet, header, form.get("hidden") == "on", cell=_raw)
    finally:
        _SLOTS.release()
    if table and len(sheets) > 1:
        raise ToolError("A table name applies to one sheet. Choose a sheet, or leave the table name empty "
                        "to use each sheet's name.")

    parts, notes, size = [], [], 0
    for name, rows in sheets.items():
        if not rows:
            notes.append(f"Sheet '{name}' has no data rows; skipped.")
            continue
        columns, values = _columns_and_rows(rows, header)
        stmts = _table_sql(table or name, columns, values, dialect,
                           form.get("create") == "on", form.get("drop") == "on", batch)
        block = "\n\n".join(stmts)
        size += len(block)
        if size > MAX_OUTPUT:
            raise ToolError(f"The generated SQL is larger than {MAX_OUTPUT:,} characters. Try a smaller sheet.")
        parts.append(block)
    if not parts:
        raise ToolError("No data rows found to convert.")
    out = "\n\n".join(parts) + "\n"
    notes.append(f"{len(parts)} table(s), {stats['rows']:,} row(s), {stats['cells']:,} cell(s) converted. "
                 "Column types are inferred from the values; review them before running.")
    return Result(out, html=sql_html(out), notes=notes)


register(Tool(
    slug="excel-sql", title="Excel to SQL",
    description="Turn an .xlsx workbook into CREATE TABLE and INSERT statements. The SQL is never executed.",
    fields=(
        Field("file", "Excel file (.xlsx)", "file", accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              upload="xlsx"),
        Field("dialect", "SQL dialect", "select", DIALECTS, "mysql"),
        Field("table", "Table name (empty = sheet name)", "text", max_len=128),
        Field("sheet", "Sheet (name or number; empty = all sheets)", "text", max_len=64),
        Field("batch", "Rows per INSERT", "select",
              (("1", "1 (one INSERT per row)"), ("100", "100"), ("500", "500"), ("1000", "1000")), "100"),
        Field("header", "First row contains column names", "checkbox", default="on"),
        Field("create", "Include CREATE TABLE", "checkbox", default="on"),
        Field("drop", "Include DROP TABLE IF EXISTS", "checkbox"),
        Field("hidden", "Include hidden sheets", "checkbox"),
    ),
    actions=(Action("convert", "Convert to SQL", True),),
    handler=handler, group="Converters", ext="sql", rate="10 per minute",
))
