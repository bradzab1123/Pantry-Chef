-- Seed data from SPEC.md §1 and §2.1. Runs once; everything is editable in Settings.

INSERT INTO person (id, name) VALUES
    (1, 'Brad'),
    (2, 'Partner');

INSERT INTO restriction (person_id, term, aliases, exceptions, level, notes) VALUES
    (1, 'shrimp',
        '["prawn", "prawns", "shrimp paste", "dried shrimp", "shrimp stock", "XO sauce", "bagoong", "belacan"]',
        '[]', 'never', NULL),
    (1, 'fish', '[]', '[]', 'avoid', NULL),
    (2, 'broccoli', '["broccolini"]', '[]', 'never',
        'Crohn''s trigger (harder vegetable). Seeded as never to be conservative.'),
    (2, 'corn', '[]', '["cornstarch", "corn starch"]', 'never',
        'Crohn''s trigger (harder vegetable). Seeded as never to be conservative.');

INSERT INTO staple (name) VALUES
    ('salt'), ('black pepper'), ('olive oil'), ('vegetable oil'), ('butter'),
    ('flour'), ('sugar'), ('garlic'), ('onion'), ('eggs');
