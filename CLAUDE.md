# CLAUDE.md

## Project
Pantry Chef: a self-hosted PWA that suggests recipes from our pantry while respecting per-person dietary restrictions. **The full spec is in `SPEC.md`. Read it before starting any phase.**

## How to work
- Build **one phase at a time** (SPEC.md §7). At the end of each phase, stop and summarize: what was built, how to run or test it, and anything you decided that the spec left open.
- Use plan mode before starting a phase. Ask if something in the spec is ambiguous rather than guessing on anything that affects data or restrictions.
- Keep it simple. No JS framework, no build step, no extra services beyond the one container. Don't add dependencies unless they clearly earn their place.
- Brad is comfortable with Docker, Linux and Cloudflare, and is still learning Python. Write readable, plainly structured Python, and comment the non-obvious parts.

## Hard rules
- The `never` restriction filter is safety-relevant (people rely on it for allergies and medical triggers). It must be unit-tested, and it must run on **every** generated or spin-off recipe. Never weaken it to make a test pass.
- Never commit secrets. `ANTHROPIC_API_KEY` lives only in `.env`, which is git-ignored. Keep `.env.example` up to date.
- Tests must never call the real Anthropic API. Mock the client.
- The container publishes no ports. It's reached only through Cloudflare Tunnel on a shared Docker network.

## Commands (update as the project takes shape)
- Setup: `python -m venv .venv` then `.venv/Scripts/python -m pip install -r requirements-dev.txt`
- Run locally: `uvicorn app.main:app --reload`
- Tests: `pytest`
- Docker: `docker compose up --build` (needs `.env` and the external `TUNNEL_NETWORK` network; locally: `docker network create cloudflared`)
- Regenerate icons: `python scripts/make_icons.py`
- CI: `.github/workflows/ci.yml` runs pytest on PRs and pushes to `main`; on `main` it then pushes `ghcr.io/<owner>/pantry-chef:latest` and `:sha-<short>`
- Server update: `docker compose pull && docker compose up -d` (roll back with `IMAGE_TAG=sha-...` in `.env`). Deploy, Cloudflare and backup steps are in README.md.

## Conventions
- Schema changes: add a new `app/migrations/NNNN_*.sql` file (never edit a shipped one) and update `app/models.py`; `test_schema_matches_models` keeps them in sync.
- Seed data lives in migrations so it runs once and Settings edits are never overwritten. A new install seeds two unnamed people and no restrictions; `main.require_setup` sends every GET to `/setup` until both are named (names must differ).
- Tests: the `client` and `household` fixtures name the people Alex and Jordan and add the example restrictions (shrimp/fish, broccoli/corn) from `conftest.HOUSEHOLD_SQL`; `new_install_client` is a fresh, unnamed install.
- HTMX forms also carry `method`/`action`: routes return a partial when `is_htmx(request)`, else a 303 redirect. HTMX won't swap 4xx responses, so validation errors come back as 200 with the error in the form.
- `never` restrictions are always `active`; only `avoid` can be toggled off. Downgrading `never` → `avoid` needs a second, confirming save.
- Timestamps: use `models.utcnow()` (aware UTC); SQLModel rejects naive datetimes.
- Generation: `cook.generate()` loads `never` rules from the DB (never from the form); `cook.review_batch()` runs `cook.review()` on every recipe before tidying it, regenerates once, then drops. Spin-offs (`saved.spin_off()`) go through the same `review_batch()` with everyone's rules.
- The `suggestion` table holds the latest batch only: one Cook batch (`parent_recipe_id` NULL) plus one spin-off batch per saved recipe. Saving and rating copy from it by id (never recipe JSON from the form); `saved_recipe_id` marks which saved recipe a card became. Rating an unsaved card saves it.
- Plan: `plan_item` always holds its own copy of the recipe JSON (copied server-side from a suggestion or saved recipe by id), plus `saved_recipe_id` when saved; deleting that saved recipe cascades it off the plan. "Already planned" = equal recipe JSON.
- Shopping list (`shopping.py`): unchecked `plan` rows are owned by `shopping.sync()` (run after plan changes and on every `/list` load); it merges by `normalize_name` and skips Have, Buying and enabled staples. It never touches `manual` or checked rows. Buying items are shown on the List straight from `pantry_item`, not copied. Checking moves the item into Have and records `pantry_item_id` so unchecking undoes exactly that; a checked Buying item becomes a `buying` row so undo puts it back in Buying.
- Steps are shown in an `<ol>`, so `schemas.strip_step_number` drops any number the model adds (on parse, and as a template filter for older saved recipes).
- LLM calls go through `llm.request_recipes()` (structured output via `output_config.format`, one validation retry, every call logged). Tests use the `fake_llm` fixture; an autouse fixture makes un-faked calls fail.

## Layout (target)
```
app/
  main.py          # FastAPI app: wires routers, /health, /sw.js
  web.py           # templates, tab bar, render/render_partial, is_htmx, redirect
  routers/         # one APIRouter per page (cook.py, pantry.py, saved.py, settings.py, setup.py, shopping.py)
  cook.py          # Cook tab: form parsing, prompt context, review/filter, regenerate-once
  saved.py         # save/rate cards, Saved list/search, heads-up for new never rules, spin-offs
  pantry.py        # name cleaning, plural-folding normalize_name, bulk add
  db.py            # engine, session, migrations
  models.py        # DB models
  schemas.py       # Pydantic LLM schemas
  llm.py           # Anthropic client, prompt building, structured output, retries
  restrictions.py  # never-filter matcher
  shopping.py      # this week's plan, shopping list sync, check/uncheck into the pantry
  prompts/         # prompt templates
  templates/       # Jinja2 + HTMX
  static/          # css, icons, manifest, service worker
tests/
.github/workflows/ci.yml  # test, then build + push to GHCR
data/              # SQLite (git-ignored)
```
