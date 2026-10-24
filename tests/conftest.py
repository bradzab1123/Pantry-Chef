"""Shared test fixtures.

Every test gets its own fresh SQLite file in a temp folder, so tests never
touch data/pantry.db and can't affect each other.
"""

import sqlite3

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


# A new install starts with two unnamed people and no restrictions (see
# 0002_seed.sql). Most tests want a household that's set up, with restrictions
# that exercise the never filter, so this fills one in after migrating.
HOUSEHOLD_SQL = """
UPDATE person SET name = 'Alex' WHERE id = 1;
UPDATE person SET name = 'Jordan' WHERE id = 2;
INSERT INTO restriction (person_id, term, aliases, exceptions, level, notes) VALUES
    (1, 'shrimp',
        '["prawn", "prawns", "shrimp paste", "dried shrimp", "shrimp stock", "XO sauce", "bagoong", "belacan"]',
        '[]', 'never', NULL),
    (1, 'fish', '[]', '[]', 'avoid', NULL),
    (2, 'broccoli', '["broccolini"]', '[]', 'never', 'Harder vegetable.'),
    (2, 'corn', '["popcorn"]', '["cornstarch", "corn starch"]', 'never', 'Harder vegetable.');
"""


@pytest.fixture
def household(db_file):
    """Migrate the temp database and fill in the test household."""
    db.migrate()
    conn = sqlite3.connect(db_file)
    try:
        conn.executescript(HOUSEHOLD_SQL)
    finally:
        conn.close()
    return db_file


@pytest.fixture
def client(household):
    """A TestClient for a set-up household. Using it as a context manager runs
    the app's startup (migrations, already applied here, so a no-op)."""
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def new_install_client(db_file):
    """A TestClient on a brand-new install: nobody named yet."""
    from app.main import app

    with TestClient(app) as c:
        yield c


# ---------- The Anthropic API: always faked in tests ----------


@pytest.fixture(autouse=True)
def no_real_anthropic(monkeypatch):
    """Make sure no test can ever reach the real Anthropic API.

    get_client() raises unless a test installs a fake (the fake_llm fixture),
    and the API key is blanked so nothing picks up a real one from the
    environment.
    """
    from app import config, llm

    def refuse():
        raise RuntimeError("Tests must not call the real Anthropic API. Use the fake_llm fixture.")

    monkeypatch.setattr(llm, "get_client", refuse)
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


class FakeLLM:
    """Stands in for anthropic.Anthropic(). Queue replies with .reply()/.fail(),
    then read what was sent from .calls (the kwargs of each messages.create)."""

    def __init__(self):
        self.replies = []
        self.calls = []
        self.messages = self  # so client.messages.create(...) lands on self.create

    def reply(self, recipes=None, *, text=None, stop_reason="end_turn",
              input_tokens=1000, output_tokens=2000):
        """Queue one response. `recipes` is a list of recipe dicts; `text`
        overrides the JSON body (e.g. to send something invalid)."""
        import json
        from types import SimpleNamespace

        body = text if text is not None else json.dumps({"recipes": recipes or []})
        self.replies.append(SimpleNamespace(
            stop_reason=stop_reason,
            # A thinking block first, like a real adaptive-thinking response.
            content=[SimpleNamespace(type="thinking", thinking=""),
                     SimpleNamespace(type="text", text=body)],
            usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        ))
        return self

    def fail(self, error):
        """Queue an exception to be raised by the next call."""
        self.replies.append(error)
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        assert self.replies, "FakeLLM got more calls than replies were queued"
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def prompt(self, n=-1) -> str:
        """The user message of call n (default: the last one)."""
        return self.calls[n]["messages"][0]["content"]


@pytest.fixture
def fake_llm(monkeypatch):
    from app import config, llm

    fake = FakeLLM()
    monkeypatch.setattr(llm, "get_client", lambda: fake)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key-not-real")
    return fake
