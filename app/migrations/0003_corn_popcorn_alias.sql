-- Popcorn is corn, but whole-word matching won't find "corn" inside "popcorn"
-- (on purpose: that's what keeps "acorn" and "peppercorn" from matching).
-- So add it as an alias on Partner's seeded corn restriction.
--
-- Does nothing if corn was deleted or popcorn is already there, so it never
-- overwrites edits made in Settings.

UPDATE restriction
SET aliases = json_insert(aliases, '$[#]', 'popcorn')
WHERE person_id = 2
  AND lower(term) = 'corn'
  AND NOT EXISTS (
      SELECT 1 FROM json_each(restriction.aliases) WHERE lower(json_each.value) = 'popcorn'
  );
