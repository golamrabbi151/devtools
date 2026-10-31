"""Tool modules. Importing a module registers its tool."""
import importlib

_MODULES = (
    "json_format", "json_compare", "sql_format", "xml_format", "cron_parser",
    "base64_text", "base64_image", "cert_decode", "qr_code", "jwt_tool",
    "excel_json", "excel_sql", "lorem", "password_gen", "uuid_gen",
)


def load_all() -> None:
    for name in _MODULES:
        importlib.import_module(f"{__name__}.{name}")
