# Pantry Chef — Build Spec

A self-hosted, mobile-first web app (PWA) for a two-person household. We enter what food we have and what we're about to buy, and the app suggests recipes that respect each person's dietary restrictions. It's used mostly **before grocery shopping**, so recipes may need 1–2 extra ingredients, and the app turns those into a shopping list.

Installed on iPhone via Safari → Share → Add to Home Screen. Hosted on a personal Linux Docker host behind an existing Cloudflare Tunnel, protected by Cloudflare Access.

---

## 1. Users & restrictions

Two people share **one household** (one pantry, one set of saved recipes). There are no app-level accounts; Cloudflare Access handles login.

Each person has a list of restrictions. Each restriction has a **level**:

| Level | Meaning | Enforced how |
|---|---|---|
| `never` | Must not appear in a recipe | Sent in the prompt **and** enforced by a code-level filter on the returned recipe |
| `avoid` | Dislike / prefer not | Sent in the prompt only |

### First run

A new install has two unnamed people and no restrictions. Until both are named, every page goes to a **setup screen** that asks for the two names; after that it hands off to Settings to add restrictions. Names stay editable in Settings and must differ from each other.

### Example restrictions

These are used throughout this spec and the tests (they're not seeded):

- Person 1
  - `never`: shrimp — aliases: prawn, prawns, shrimp paste, dried shrimp, shrimp stock, XO sauce, bagoong, belacan
  - `avoid`: fish
- Person 2
  - `never`: broccoli — aliases: broccolini
  - `never`: corn — aliases: popcorn; exceptions: cornstarch, corn starch

### Restriction fields
- `term` (e.g. "corn")
- `aliases` (extra terms that count as a match)
- `exceptions` (terms that contain the word but are fine, e.g. "cornstarch")
- `level` (`never` | `avoid`)
- `notes` (optional)

### The `never` filter (critical — must be unit-tested)
After every generation, check each recipe's title, ingredient names and step text against all active `never` restrictions:
- Case-insensitive, **word-boundary** matching on the term and each alias (so "corn" matches "corn", "sweet corn", "corn kernels" but not "acorn squash" or "peppercorn").
- Skip matches where an `exceptions` term covers the matched text (so "cornstarch" isn't flagged).
- Handle simple plurals (term + "s"/"es").
- If a recipe fails: regenerate **once** with a message naming the violation. If it still fails, drop it and show "1 recipe removed for containing a restricted ingredient."
- Write the tests before wiring the filter into generation. Include the examples above plus false-positive cases (acorn, peppercorn, popcorn should only match if it's actually corn — popcorn *is* corn, so it should match).

---

## 2. Features

### 2.1 Pantry
- Two lists: **Have** and **Buying**. Both count as "available" when generating recipes.
- **Bulk add**: a textarea that splits on commas and newlines, trims and lowercases items, and drops duplicates already on the list.
- Tap to delete ("used it up"); tap to move from Buying to Have.
- No quantities in v1. Presence only.
- **Staples**: a separate, editable list that is always assumed to be on hand (seed: salt, black pepper, olive oil, vegetable oil, butter, flour, sugar, garlic, onion, eggs). Each staple can be toggled off.

### 2.2 Generate recipes (main screen)
Inputs:
- **Who's eating**: either person by name, or Both (default Both). Only restrictions for the people eating apply.
- **Restriction chips**: every active restriction for the people eating, shown as chips. Tapping an `avoid` chip turns it off **for this request only**. `never` chips are shown but locked; they only drop off via "who's eating."
- **Skip tonight**: free text for one-off exclusions ("no pasta, had it yesterday").
- Options: meal type (any/breakfast/lunch/dinner/snack), max total time (any/30/45/60 min), servings (default 2), cuisine (optional free text), number of recipes (default 4, max 6).
- Max missing ingredients per recipe: **2** (fixed in v1).

Also sent to the model:
- Pantry (Have + Buying), enabled staples
- Recent ratings: titles of the last ~15 thumbs-up and ~15 thumbs-down recipes, so suggestions drift toward what we like

Output: recipe cards showing title, short summary, time, and a highlighted "Need to buy: x, y" line. Expanding a card shows ingredients and steps. Actions on each card: **Save**, **👍 / 👎**, **Add to plan**.

### 2.3 Saved recipes, ratings & spin-offs
- Saving stores the full recipe JSON.
- Ratings: thumbs up, thumbs down, or none. A favorite is a saved recipe with a thumbs up.
- **Spin-off** button on a saved recipe sends that recipe, the current pantry and the active restrictions, and asks for 3 variations that keep the core idea but change one main thing (protein, cuisine, or cooking method). Each variation states what changed. Spin-offs record `parent_recipe_id`.
- Saved view: filter by favorites, search by title.

### 2.4 Plan & shopping list
- "Add to plan" puts recipes into a **This week** plan (simple list; no calendar in v1).
- The shopping list is built from the planned recipes' missing ingredients: merge duplicates by normalized name, skip anything already in Have, and allow manual additions.
- Checking off a shopping item moves it to **Have** in the pantry.
- "Clear plan" button.

### 2.5 Settings
- People: display names
- Restrictions: add, edit, delete, set level, aliases and exceptions
- Staples: add, remove, toggle
- Usage: this month's generation count, input/output tokens and estimated cost (prices per million tokens come from env vars)

### 2.6 Cooked it & pantry check
Keeps the pantry accurate after cooking without tracking quantities.

- **Cooked it** on Cook cards (and spin-off cards), the saved recipe page, and each planned recipe on the List. It expands a small form in place listing the recipe's ingredients that are in **Have** (sources `pantry` and `buy`, since a "buy" item may have been bought since; `staple` ingredients are skipped).
- Matching an ingredient to a Have item: same `normalize_name` first (after dropping prep notes), else the pantry name appears as whole words inside the ingredient ("chicken breast" matches "boneless chicken breasts"; "corn" doesn't match "cornstarch"). The most specific item wins. This is not the restriction matcher.
- Each item is a toggle, default "still have"; tap the ones used up. "Put used-up items on the shopping list" (on by default) moves them to Buying; off deletes them from the pantry. With nothing in Have, the form just says so and offers Done.
- Done also takes the recipe off this week's plan (any plan item with the same recipe JSON) and syncs the shopping list. A short confirmation replaces the form ("Moved chicken breast, rice to Buying."); cooking from the List redraws the List with it on top.
- The recipe is loaded by id (suggestion, saved recipe or plan item), never from the form. Posted pantry ids are accepted only if they're still in Have and match this recipe.
- **Pantry check** on the Cook form, above "Suggest recipes": a collapsed line "Cooking from N pantry items · M buying · Review". Expanded, it shows Have as chips with ✕ to delete, plus a quick add to Have. It never blocks or delays generating.
- The model is asked to write "pantry" ingredient names exactly as they appear on the pantry list, so most matches are exact.

---

## 3. LLM integration

- Anthropic Python SDK, API key from the `ANTHROPIC_API_KEY` env var (pay-as-you-go Console account).
- Model from the `CLAUDE_MODEL` env var. Default: the current Haiku model (check the model ID and pricing at docs.claude.com before setting the default).
- **Structured output is required.** Force JSON with tool use (a single tool whose `input_schema` matches the response model, with `tool_choice` set to that tool) or the API's structured outputs feature if the installed SDK supports it. Validate with Pydantic. On a validation failure, retry once, then show a friendly error.
- Log every call to `generation_log` (type, model, input tokens, output tokens, timestamp, success).
- **Cost guard**: refuse new generations beyond `MAX_GENERATIONS_PER_HOUR` (default 30) and show a message.
- Put the prompt templates in `app/prompts/` as plain text or Jinja files so they're easy to tweak.

### Recipe schema (Pydantic)
```
Recipe:
  title: str
  summary: str              # 1–2 sentences
  cuisine: str | None
  servings: int
  total_time_minutes: int
  difficulty: "easy" | "medium" | "hard"
  ingredients: list[Ingredient]
  steps: list[str]
  missing_ingredients: list[str]   # max 2; must match ingredients with source="buy"
  diet_notes: str           # one line on how it respects the restrictions
  variation_note: str | None       # spin-offs only: what changed vs. the original

Ingredient:
  name: str                 # plain ingredient name, no quantity
  quantity: str | None      # "2", "1/2", "a pinch"
  unit: str | None
  source: "pantry" | "staple" | "buy"

RecipeResponse:
  recipes: list[Recipe]
```
Server-side checks: `missing_ingredients` has at most 2 items, and the `never` filter passes.

### System prompt rules (in substance)
- Use only pantry items, enabled staples, and at most 2 "buy" items per recipe.
- Treat `never` items as absolute, including derivatives, sauces, stocks and pastes made from them.
- Respect `avoid` items and the skip-tonight text.
- Give realistic home-cooking recipes with clear steps and US measurements.
- Lean toward styles similar to the liked recipes, and away from the disliked ones.

---

## 4. Tech stack

- **Python 3.12**, **FastAPI**, **Jinja2** templates + **HTMX** (no JS framework, no build step)
- **SQLite** at `/app/data/pantry.db` (Docker volume), using SQLModel or SQLAlchemy plus simple migrations (Alembic, or a small versioned-migration helper; keep it simple)
- **Pydantic** for the LLM schemas
- **pytest** for tests; mock the Anthropic client so tests never call the API
- Minimal CSS: one stylesheet, mobile-first, large tap targets, light/dark via `prefers-color-scheme`. Pico.css or similar is fine.

### Data model (starting point)
- `person` (id, name)
- `restriction` (id, person_id, term, aliases JSON, exceptions JSON, level, notes, active)
- `pantry_item` (id, name, normalized_name, status `have|buying`, created_at)
- `staple` (id, name, enabled)
- `saved_recipe` (id, title, recipe_json, rating `-1|0|1`, parent_recipe_id, created_at)
- `plan_item` (id, saved_recipe_id or recipe_json, added_at)
- `shopping_item` (id, name, normalized_name, checked, source `plan|manual`)
- `generation_log` (id, kind `generate|spinoff`, model, input_tokens, output_tokens, success, created_at)

Recipes added to the plan straight from results, without being saved, can be stored as JSON on `plan_item`. Pick whichever is simpler.

---

## 5. PWA / iPhone

- `manifest.webmanifest`: name "Pantry Chef", `display: standalone`, theme and background colors, icons at 192 and 512 px
- `apple-touch-icon` (180 px), plus the `apple-mobile-web-app-capable` and `apple-mobile-web-app-status-bar-style` meta tags
- Respect safe-area insets (notch / home bar)
- A minimal service worker that caches static assets only. No offline API caching.
- Bottom tab bar navigation: **Cook · Pantry · Saved · List · Settings**
- Generate a simple, original icon (e.g. a pot or a leaf) as PNGs; no third-party logos

---

## 6. Deployment

- `Dockerfile`: `python:3.12-slim`, non-root user, `uvicorn app.main:app --host 0.0.0.0 --port 8000`
- `docker-compose.yml`:
  - app service, image `ghcr.io/<github-user>/pantry-chef:latest`
  - `env_file: .env`, volume `./data:/app/data`
  - **No published ports.** Join the existing external Docker network that the cloudflared container uses (network name set via variable or placeholder).
  - `restart: unless-stopped`, healthcheck on `/health`
- `.env.example` with `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, `MAX_GENERATIONS_PER_HOUR`, `PRICE_INPUT_PER_MTOK`, `PRICE_OUTPUT_PER_MTOK`, `TZ=America/New_York`. `.env` must be in `.gitignore`.
- **GitHub Actions**: on push to `main`, run pytest, then build and push to GHCR with tags `latest` and the short commit SHA.
- Server update: `docker compose pull && docker compose up -d` (manual). Document Watchtower as optional.
- `README.md` should cover:
  - local dev
  - deploy steps
  - `docker login ghcr.io` with a `read:packages` token if the repo/image is private
  - Cloudflare Tunnel public hostname → `http://pantry-chef:8000`
  - a Cloudflare Access policy allowing two email addresses, with session duration around 1 month (the iOS home screen app keeps its own cookies, so you log in once inside it)
  - nightly SQLite backup via cron using `sqlite3 pantry.db ".backup ..."`

---

## 7. Build phases

Stop after each phase so it can be tested by hand.

1. **Skeleton**: FastAPI app, DB and migrations, seed data, base layout with the tab bar, PWA manifest and icons, `/health`, Dockerfile and compose, `.env.example`, pytest setup.
2. **Pantry, staples & settings**: bulk add, Have/Buying, staples, people and restrictions editor.
3. **Restriction filter**: implement and fully unit-test the `never` matcher (aliases, exceptions, plurals, word boundaries).
4. **Generation**: prompt templates, Anthropic call with structured output, validation, filter plus retry, cost guard, usage logging, results UI.
5. **Saved, ratings & spin-offs**.
6. **Plan & shopping list**, including moving checked items into the pantry.
7. **CI/CD & docs**: GitHub Actions to GHCR, README deploy and Cloudflare steps, backup instructions.

---

## 8. Out of scope for v1
- Quantities and expiration dates
- Photo or receipt scanning (a good v2 candidate using Claude vision)
- Nutrition info
- Multiple households or in-app accounts
- Native iOS app
