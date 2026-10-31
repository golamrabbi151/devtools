"""Application configuration. Secrets come from the environment only."""
import os


MB = 1024 * 1024


def _limit_mb(env_name: str, default_mb: float) -> int:
    """Upload limit in bytes. Override with an env var (in MB); hard ceiling of 20 MB."""
    try:
        value = float(os.environ.get(env_name, default_mb))
    except ValueError:
        value = default_mb
    return int(min(max(value, 0.01), 20) * MB)


class Config:
    # ---- Upload limits, per tool (slug -> bytes). Change the defaults here or via env vars. ----
    UPLOAD_LIMITS = {
        "excel-json":   _limit_mb("UPLOAD_MB_EXCEL_JSON", 1),     # Excel to JSON            1 MB
        "excel-sql":    _limit_mb("UPLOAD_MB_EXCEL_SQL", 1),      # Excel to SQL             1 MB
        "base64-image": _limit_mb("UPLOAD_MB_BASE64_IMAGE", 5),   # Base64 image             5 MB
        "certificate":  _limit_mb("UPLOAD_MB_CERTIFICATE", 2),    # Certificate decoder      2 MB
        "qr-code":      _limit_mb("UPLOAD_MB_QR_CODE", 2),        # QR code reader           2 MB
    }
    MAX_UPLOAD_BYTES = _limit_mb("UPLOAD_MB_DEFAULT", 2)          # any other file field
    REQUEST_SLACK = 1 * MB                      # allowance for form fields / multipart overhead
    MAX_TEXT_REQUEST = 3 * MB                   # body limit for tools without file uploads
    # Absolute ceiling for any request body (per-tool limits are tighter, see app/uploads.py).
    MAX_CONTENT_LENGTH = max(UPLOAD_LIMITS.values()) + REQUEST_SLACK

    # Number of reverse proxies in front of the app (nginx / load balancer). 0 = none (direct).
    # Needed so rate limiting sees the real client IP instead of the proxy's IP.
    PROXY_HOPS = max(0, min(int(os.environ.get("PROXY_HOPS", "0") or 0), 5))

    MAX_TEXT_FIELD = 500_000                    # characters per text field
    MAX_IMAGE_PIXELS = 20_000_000               # decompression-bomb guard

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Strict"
    SESSION_COOKIE_SECURE = True
    WTF_CSRF_TIME_LIMIT = 4 * 3600
    WTF_CSRF_SSL_STRICT = True

    RATELIMIT_DEFAULT = "120 per minute"
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True

    JSON_SORT_KEYS = False
    TESTING = False

    # ---- Knowledge base (VA / PT / incidents). Disabled unless KB_PASSWORD is set. ----
    KB_PASSWORD = os.environ.get("KB_PASSWORD", "")
    KB_DB_PATH = os.environ.get("KB_DB_PATH", "")   # default: <instance folder>/kb.sqlite3


class DevelopmentConfig(Config):
    DEBUG = False  # keep the Werkzeug debugger off even locally (it is an RCE vector)
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-key")
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_SSL_STRICT = False


class ProductionConfig(Config):
    SECRET_KEY = os.environ.get("SECRET_KEY")

    @classmethod
    def validate(cls):
        if not cls.SECRET_KEY or len(cls.SECRET_KEY) < 32:
            raise RuntimeError("SECRET_KEY must be set (>= 32 chars) in production.")


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "test-key"
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_SSL_STRICT = False
    RATELIMIT_ENABLED = False
    KB_PASSWORD = "test-kb-password"


def get_config(name: str | None):
    name = (name or os.environ.get("APP_ENV", "production")).lower()
    cfg = {"development": DevelopmentConfig, "testing": TestingConfig,
           "production": ProductionConfig}.get(name, ProductionConfig)
    if cfg is ProductionConfig:
        ProductionConfig.SECRET_KEY = os.environ.get("SECRET_KEY")
        ProductionConfig.validate()
    return cfg
