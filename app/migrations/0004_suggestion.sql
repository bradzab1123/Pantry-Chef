-- The latest batch of generated recipes, shown on the Cook tab.
-- Each successful generation replaces every row. Every recipe here has
-- already passed the `never` filter; later phases (Save, plan) copy from
-- this table by id rather than trusting recipe JSON posted from the page.

CREATE TABLE suggestion (
    id          INTEGER PRIMARY KEY,
    recipe_json TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
