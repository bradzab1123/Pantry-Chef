-- Seed data. Runs once on a new install; everything is editable in Settings.
--
-- The two people start without names: until both are named, every page sends
-- you to the setup screen (app/routers/setup.py). Restrictions start empty, so
-- each person adds their own in Settings.
--
-- This file was rewritten when the project went public. Databases that ran the
-- earlier version keep the data it seeded, because migrations never re-run.

INSERT INTO person (id, name) VALUES
    (1, ''),
    (2, '');

INSERT INTO staple (name) VALUES
    ('salt'), ('black pepper'), ('olive oil'), ('vegetable oil'), ('butter'),
    ('flour'), ('sugar'), ('garlic'), ('onion'), ('eggs');
