"""Shared test fixtures.

Every test gets its own fresh SQLite file in a temp folder, so tests never
touch data/pantry.db and can't affect each other.
"""

import pytest
from fastapi.testclient import TestClient

from app import db


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    """Point the app at a temp database and return its path."""
    path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(path))
    db.reset_engine()
    yield path
    db.reset_engine()


@pytest.fixture
def client(db_file):
    """A TestClient. Using it as a context manager runs the app's startup,
    which applies migrations to the temp database."""
    from app.main import app

    with TestClient(app) as c:
        yield c
