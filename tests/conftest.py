import re

import pytest

from app import create_app


@pytest.fixture()
def app(tmp_path):
    app = create_app("testing")
    app.config["KB_DB_PATH"] = str(tmp_path / "kb.sqlite3")     # fresh knowledge base per test
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def csrf_client(client):
    return client


def get_token(client, slug="json-format"):
    html = client.get(f"/tool/{slug}").get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


@pytest.fixture()
def run(client):
    """POST to a tool using the real CSRF flow (token fetched from the tool page)."""
    def _run(slug, **data):
        data.setdefault("csrf_token", get_token(client, slug))
        return client.post(f"/tool/{slug}/run", data=data, content_type="multipart/form-data")
    return _run
