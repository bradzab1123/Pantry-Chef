-- Initial schema. See SPEC.md §4 "Data model".
-- Timestamps are UTC text ("YYYY-MM-DD HH:MM:SS").
-- JSON columns (aliases, exceptions, recipe_json) hold JSON text.

CREATE TABLE person (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE restriction (
    id         INTEGER PRIMARY KEY,
    person_id  INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    term       TEXT NOT NULL,
    aliases    TEXT NOT NULL DEFAULT '[]',
    exceptions TEXT NOT NULL DEFAULT '[]',
    level      TEXT NOT NULL CHECK (level IN ('never', 'avoid')),
    notes      TEXT,
    active     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX ix_restriction_person ON restriction(person_id);

CREATE TABLE pantry_item (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('have', 'buying')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX ix_pantry_item_normalized ON pantry_item(normalized_name);

CREATE TABLE staple (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE saved_recipe (
    id               INTEGER PRIMARY KEY,
    title            TEXT NOT NULL,
    recipe_json      TEXT NOT NULL,
    rating           INTEGER NOT NULL DEFAULT 0 CHECK (rating IN (-1, 0, 1)),
    parent_recipe_id INTEGER REFERENCES saved_recipe(id) ON DELETE SET NULL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A plan item points at a saved recipe, OR carries the recipe JSON itself
-- (for recipes added to the plan straight from results without saving).
CREATE TABLE plan_item (
    id              INTEGER PRIMARY KEY,
    saved_recipe_id INTEGER REFERENCES saved_recipe(id) ON DELETE CASCADE,
    recipe_json     TEXT,
    added_at        TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (saved_recipe_id IS NOT NULL OR recipe_json IS NOT NULL)
);

CREATE TABLE shopping_item (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    checked         INTEGER NOT NULL DEFAULT 0,
    source          TEXT NOT NULL CHECK (source IN ('plan', 'manual'))
);

CREATE TABLE generation_log (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (kind IN ('generate', 'spinoff')),
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    success       INTEGER NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX ix_generation_log_created ON generation_log(created_at);
