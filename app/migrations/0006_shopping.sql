-- Phase 6: the shopping list.
--
-- SQLite can't change a CHECK constraint in place, so shopping_item is
-- rebuilt (it was unused until now; any rows are copied across anyway).
--
-- source: 'plan'   = a planned recipe's missing ingredient (kept in step
--                    with the plan by shopping.sync())
--         'manual' = typed in on the List tab
--         'buying' = a checked-off item that came from the Pantry's Buying
--                    list, so unchecking it can put it back in Buying
-- pantry_item_id: the pantry row that checking the item created or moved
--   into Have, so unchecking undoes exactly that. NULL when the item was
--   already in Have (unchecking then leaves the pantry alone).

CREATE TABLE shopping_item_new (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    checked         INTEGER NOT NULL DEFAULT 0,
    source          TEXT NOT NULL CHECK (source IN ('plan', 'manual', 'buying')),
    pantry_item_id  INTEGER REFERENCES pantry_item(id) ON DELETE SET NULL
);

INSERT INTO shopping_item_new (id, name, normalized_name, checked, source)
    SELECT id, name, normalized_name, checked, source FROM shopping_item;

DROP TABLE shopping_item;
ALTER TABLE shopping_item_new RENAME TO shopping_item;
