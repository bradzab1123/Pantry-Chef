# Pantry Chef

A self-hosted, mobile-first PWA that suggests recipes from what's in the pantry while respecting each person's dietary restrictions. See [SPEC.md](SPEC.md) for the full spec.

> Full deploy, Cloudflare Tunnel/Access and backup docs come in Phase 7.

## Local development

Needs Python 3.11+ (the container uses 3.12).

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows (Git Bash); on Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Open http://localhost:8000. The SQLite database is created at `data/pantry.db` and migrated automatically on startup.

## Tests

```bash
pytest
```

Tests use a throwaway database per test and never call the Anthropic API.

## Docker

```bash
cp .env.example .env    # then fill in ANTHROPIC_API_KEY, GHCR_USER, TUNNEL_NETWORK
docker compose up --build
```

The container publishes **no ports**. It joins the existing external Docker network that `cloudflared` uses (`TUNNEL_NETWORK`), and the tunnel reaches it at `http://pantry-chef:8000`. On a machine without that network, create one for testing with `docker network create cloudflared`.

## Project notes

- **Migrations:** numbered SQL files in `app/migrations/`, applied in order at startup and tracked in `schema_version`. To change the schema, add a new file with the next number. Never edit a file that has already run on the server. Also update `app/models.py`; a test checks that the two match.
- **Icons:** `python scripts/make_icons.py` redraws the PNGs in `app/static/icons/`.
- **htmx** is vendored at `app/static/htmx.min.js` (v2.0.4).
