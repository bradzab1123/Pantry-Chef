-- Phase 5: saving, rating and spin-offs.
--
-- suggestion.parent_recipe_id: NULL for the Cook tab's batch; set for the
--   latest batch of spin-offs of that saved recipe. Deleting the saved recipe
--   removes its unsaved spin-offs.
-- suggestion.saved_recipe_id: the saved recipe this card became (so the card
--   shows "Saved" and a double tap can't save it twice). Deleting the saved
--   recipe clears it, and the card offers Save again.

ALTER TABLE suggestion ADD COLUMN parent_recipe_id INTEGER REFERENCES saved_recipe(id) ON DELETE CASCADE;
ALTER TABLE suggestion ADD COLUMN saved_recipe_id INTEGER REFERENCES saved_recipe(id) ON DELETE SET NULL;
CREATE INDEX ix_suggestion_parent ON suggestion(parent_recipe_id);
