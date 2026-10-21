# CLAUDE.md

## Project
Pantry Chef: a self-hosted PWA that suggests recipes from our pantry while respecting per-person dietary restrictions. **The full spec is in `SPEC.md`. Read it before starting any phase.**

## How to work
- Build **one phase at a time** (SPEC.md §7). At the end of each phase, stop and summarize: what was built, how to run or test it, and anything you decided that the spec left open.
- Use plan mode before starting a phase. Ask if something in the spec is ambiguous rather than guessing on anything that affects data or restrictions.
- Keep it simple. No JS framework, no build step, no extra services beyond the one container. Don't add dependencies unless they clearly earn their place.
- Brad is comfortable with Docker, Linux and Cloudflare, and is still learning Python. Write readable, plainly structured Python, and comment the non-obvious parts.

## Hard rules
- The `never` restriction filter is safety-relevant (one person has Crohn's triggers). It must be unit-tested, and it must run on **every** generated or spin-off recipe. Never weaken it to make a test pass.
- Never commit secrets. `ANTHROPIC_API_KEY` lives only in `.env`, which is git-ignored. Keep `.env.example` up to date.
- Tests must never call the real Anthropic API. Mock the client.
- The container publishes no ports. It's reached only through Cloudflare Tunnel on a shared Docker network.

## Commands (update as the project takes shape)
- Setup: `python -m venv .venv` then `.venv/Scripts/python -m pip install -r requirements-dev.txt`
- Run locally: `uvicorn app.main:app --reload`
- Tests: `pytest`
- Docker: `docker compose up --build` (needs `.env` and the external `TUNNEL_NETWORK` network; locally: `docker network create cloudflared`)
- Regenerate icons: `python scripts/make_icons.py`

## Conventions
- Schema changes: add a new `app/migrations/NNNN_*.sql` file (never edit a shipped one) and update `app/models.py`; `test_schema_matches_models` keeps them in sync.
- Seed data lives in migrations so it runs once and Settings edits are never overwritten.
- HTMX forms also carry `method`/`action`: routes return a partial when `is_htmx(request)`, else a 303 redirect. HTMX won't swap 4xx responses, so validation errors come back as 200 with the error in the form.
- `never` restrictions are always `active`; only `avoid` can be toggled off. Downgrading `never` → `avoid` needs a second, confirming save.
- Timestamps: use `models.utcnow()` (aware UTC); SQLModel rejects naive datetimes.
- Generation: `cook.generate()` loads `never` rules from the DB (never from the form); `cook.review_batch()` runs `cook.review()` on every recipe before tidying it, regenerates once, then drops. Spin-offs (`saved.spin_off()`) go through the same `review_batch()` with everyone's rules.
- The `suggestion` table holds the latest batch only: one Cook batch (`parent_recipe_id` NULL) plus one spin-off batch per saved recipe. Saving and rating copy from it by id (never recipe JSON from the form); `saved_recipe_id` marks which saved recipe a card became. Rating an unsaved card saves it.
- LLM calls go through `llm.request_recipes()` (structured output via `output_config.format`, one validation retry, every call logged). Tests use the `fake_llm` fixture; an autouse fixture makes un-faked calls fail.

## Layout (target)
```
app/
  main.py          # FastAPI app: wires routers, simple pages, /health, /sw.js
  web.py           # templates, tab bar, render/render_partial, is_htmx, redirect
  routers/         # one APIRouter per page (cook.py, pantry.py, saved.py, settings.py, ...)
  cook.py          # Cook tab: form parsing, prompt context, review/filter, regenerate-once
  saved.py         # save/rate cards, Saved list/search, heads-up for new never rules, spin-offs
  pantry.py        # name cleaning, plural-folding normalize_name, bulk add
  db.py            # engine, session, migrations
  models.py        # DB models
  schemas.py       # Pydantic LLM schemas
  llm.py           # Anthropic client, prompt building, structured output, retries
  restrictions.py  # never-filter matcher
  shopping.py      # shopping list aggregation
  prompts/         # prompt templates
  templates/       # Jinja2 + HTMX
  static/          # css, icons, manifest, service worker
tests/
data/              # SQLite (git-ignored)
```
