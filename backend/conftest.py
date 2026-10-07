import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def client(tmp_path, monkeypatch):
    # db_path() reads DATA_DIR lazily per connection, so setting it before the
    # client enters its lifespan is enough to seed a fresh database.
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.main import app
    with TestClient(app) as c:
        yield c
