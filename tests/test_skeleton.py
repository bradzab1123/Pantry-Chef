"""Phase 1 tests: health, migrations, seed data, schema/model sync, pages, PWA files."""

import json
import sqlite3

import pytest
from sqlmodel import Session, SQLModel, select

from app import db, models
from app.main import TABS

# ---------- Health ----------


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------- Migrations ----------


def latest_migration_number() -> int:
    return max(int(p.name.split("_", 1)[0]) for p in db.MIGRATIONS_DIR.glob("*.sql"))


def test_migrate_reaches_latest_version(db_file):
    assert db.migrate() == latest_migration_number()


def test_migrate_is_idempotent(db_file):
    first = db.migrate()
    second = db.migrate()
    assert first == second

    conn = sqlite3.connect(db_file)
    try:
        # Seed rows were inserted exactly once.
        assert conn.execute("SELECT COUNT(*) FROM person").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM staple").fetchone()[0] == 10
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_version")]
        assert versions == sorted(set(versions))
    finally:
        conn.close()


def test_schema_matches_models(db_file):
    """The SQL migrations and app/models.py must describe the same columns."""
    db.migrate()
    conn = sqlite3.connect(db_file)
    try:
        for table in SQLModel.metadata.sorted_tables:
            rows = conn.execute(f"PRAGMA table_info({table.name})").fetchall()
            assert rows, f"table {table.name} missing from migrations"
            # PRAGMA row: (cid, name, type, notnull, default, pk)
            sql_columns = {r[1] for r in rows}
            model_columns = {c.name for c in table.columns}
            assert sql_columns == model_columns, f"column mismatch in {table.name}"
    finally:
        conn.close()


# ---------- Seed data (read through the ORM, as the app will) ----------


@pytest.fixture
def session(db_file):
    db.migrate()
    with Session(db.get_engine()) as s:
        yield s


def test_seed_people_start_unnamed(session):
    """A new install has two people with no names yet (the setup screen asks)
    and no restrictions."""
    people = session.exec(select(models.Person).order_by(models.Person.id)).all()
    assert [(p.id, p.name) for p in people] == [(1, ""), (2, "")]
    assert session.exec(select(models.Restriction)).all() == []


def test_popcorn_migration_never_duplicates(household):
    """Re-running 0003 (or running it after popcorn was added in Settings) is a no-op."""
    sql = (db.MIGRATIONS_DIR / "0003_corn_popcorn_alias.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(household)
    try:
        conn.executescript(sql)
        aliases = conn.execute("SELECT aliases FROM restriction WHERE term = 'corn'").fetchone()[0]
        assert json.loads(aliases) == ["popcorn"]
    finally:
        conn.close()


def test_seed_staples(session):
    staples = session.exec(select(models.Staple)).all()
    assert {s.name for s in staples} == {
        "salt", "black pepper", "olive oil", "vegetable oil", "butter",
        "flour", "sugar", "garlic", "onion", "eggs",
    }
    assert all(s.enabled for s in staples)


def test_check_constraints_reject_bad_level(db_file):
    db.migrate()
    conn = sqlite3.connect(db_file)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO restriction (person_id, term, level) VALUES (1, 'x', 'sometimes')"
            )
    finally:
        conn.close()


# ---------- Pages ----------


@pytest.mark.parametrize("key,label,url", TABS)
def test_tab_pages_render_with_active_tab(client, key, label, url):
    response = client.get(url)
    assert response.status_code == 200
    html = response.text
    assert 'class="tabbar"' in html
    for _key, other_label, other_url in TABS:
        assert f'href="{other_url}"' in html
    # Exactly one tab is marked current, and it's this one.
    assert html.count('aria-current="page"') == 1
    assert f'<a href="{url}" class="tab active"' in html


def test_base_layout_has_pwa_tags(client):
    html = client.get("/").text
    assert 'name="apple-mobile-web-app-capable"' in html
    assert 'name="apple-mobile-web-app-status-bar-style"' in html
    assert 'rel="apple-touch-icon"' in html
    assert 'rel="manifest"' in html
    assert "viewport-fit=cover" in html


# ---------- PWA files ----------


def test_service_worker_served_from_root(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert response.headers["service-worker-allowed"] == "/"


def test_manifest(client):
    response = client.get("/static/manifest.webmanifest")
    assert response.status_code == 200
    assert "manifest+json" in response.headers["content-type"]
    manifest = json.loads(response.text)
    assert manifest["name"] == "Pantry Chef"
    assert manifest["display"] == "standalone"
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes
    # Every icon the manifest names actually exists.
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200


def test_apple_touch_icon_is_180(client):
    from io import BytesIO

    from PIL import Image

    response = client.get("/static/icons/apple-touch-icon.png")
    assert response.status_code == 200
    assert Image.open(BytesIO(response.content)).size == (180, 180)


def test_css_and_js_urls_carry_a_version(client):
    """The service worker serves cached /static/ files first, so the page
    links them with ?v=<fingerprint>; a changed file gets a new URL."""
    from app.web import static_version

    html = client.get("/").text
    version = static_version()
    assert f'href="/static/style.css?v={version}"' in html
    assert f'src="/static/htmx.min.js?v={version}"' in html
    assert client.get(f"/static/style.css?v={version}").status_code == 200
