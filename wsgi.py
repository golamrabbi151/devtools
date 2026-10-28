import os

if __name__ == "__main__":
    # Local development only. Production: gunicorn (see Dockerfile).
    os.environ.setdefault("APP_ENV", "development")

from app import create_app  # noqa: E402

app = create_app(os.environ.get("APP_ENV"))

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
