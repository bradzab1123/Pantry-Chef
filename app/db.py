"""Database engine, sessions and a tiny versioned-migration runner.

How migrations work:
- Each file in app/migrations/ is named NNNN_description.sql (e.g. 0001_initial.sql).
- The `schema_version` table remembers which numbers have already run.
- On startup, migrate() runs any newer files in order, each in its own transaction.
- To change the schema later, add a new file with the next number. Never edit
  a file that has already shipped, because existing databases won't re-run it.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

from app import config

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_engine: Engine | None = None


def get_engine() -> Engine:
    """Create the engine on first use (lazily, so tests can set DB_PATH first)."""
    global _engine
    if _engine is None:
        path = config.db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: FastAPI may use a session from a different
        # thread than the one that opened the connection. Each request still
        # gets its own session, so this is safe.
        _engine = create_engine(
            f"sqlite:///{path}", connect_args={"check_same_thread": False}
        )
        event.listen(_engine, "connect", _on_connect)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine (used by tests that switch databases)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def _on_connect(dbapi_conn, _record) -> None:
    # SQLite ignores foreign keys unless you turn them on for every connection.
    dbapi_conn.execute("PRAGMA foreign_keys = ON")


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    with Session(get_engine()) as session:
        yield session


def _migration_files() -> list[tuple[int, Path]]:
    files = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        number = int(path.name.split("_", 1)[0])
        files.append((number, path))
    return files


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        " version INTEGER PRIMARY KEY,"
        " applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] or 0


def migrate() -> int:
    """Apply any pending migrations. Returns the schema version afterwards."""
    path = config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Use the plain sqlite3 module here: executescript() can run a whole .sql
    # file at once, which SQLAlchemy doesn't offer directly.
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        version = current_version(conn)
        for number, file in _migration_files():
            if number <= version:
                continue
            sql = file.read_text(encoding="utf-8")
            # BEGIN/COMMIT around the file + the version insert, so a failed
            # migration leaves nothing half-applied.
            conn.executescript(
                "BEGIN;\n"
                + sql
                + f"\nINSERT INTO schema_version (version) VALUES ({number});\nCOMMIT;"
            )
            version = number
        return version
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
