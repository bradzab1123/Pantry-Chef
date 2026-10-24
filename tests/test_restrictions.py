"""Phase 3 tests: the `never` restriction filter (app/restrictions.py).

This filter is safety-relevant (medical triggers, allergies). If one of these
tests fails, fix the matcher, never the test. When in doubt the matcher
should flag a recipe; a false alarm costs one regenerated recipe, a miss
costs a bad night.
"""

import pytest
from sqlmodel import Session, select

from app import db
from app.models import Person, Restriction
from app.restrictions import (
    Violation,
    check_recipe,
    find_matches,
    never_restrictions_for,
    recipe_texts,
    violation_message,
)


def never(term, aliases=(), exceptions=(), **kw):
    """A `never` restriction that isn't in any database."""
    return Restriction(id=kw.pop("id", None), person_id=kw.pop("person_id", 1), term=term,
                       aliases=list(aliases), exceptions=list(exceptions),
                       level=kw.pop("level", "never"), active=kw.pop("active", True))


# The seed restrictions from SPEC.md §1 (plus the popcorn alias from 0003).
SHRIMP = never("shrimp", aliases=["prawn", "prawns", "shrimp paste", "dried shrimp",
                                  "shrimp stock", "XO sauce", "bagoong", "belacan"])
BROCCOLI = never("broccoli", aliases=["broccolini"], person_id=2)
CORN = never("corn", aliases=["popcorn"], exceptions=["cornstarch", "corn starch"], person_id=2)


def flagged(text, restriction):
    return bool(find_matches(text, restriction))


# ---------- Word boundaries ----------


@pytest.mark.parametrize("text", [
    "corn",
    "Sweet Corn",
    "CORN KERNELS",
    "corn on the cob",
    "1 cup corn, drained",
    "black beans (corn optional)",
    "corn.",
    "grilled corn salsa",
    "corn\ttortillas",
])
def test_corn_matches_as_a_word(text):
    assert flagged(text, CORN)


@pytest.mark.parametrize("text", [
    "acorn squash",
    "Acorn Squash, halved",
    "peppercorn",
    "peppercorns",
    "black peppercorns, crushed",
    "unicorn sprinkles",
    "cornish hen",
    "cornstarch",
    "cornichons",
    "scorned",
])
def test_corn_does_not_match_inside_other_words(text):
    assert not flagged(text, CORN)


# ---------- Popcorn ----------


def test_popcorn_matches_with_the_seeded_alias():
    assert flagged("buttered popcorn", CORN)
    assert flagged("Popcorn kernels", CORN)


def test_popcorn_needs_the_alias():
    # Documents how popcorn is caught: by alias, not by special-casing "corn".
    bare = never("corn", exceptions=["cornstarch", "corn starch"])
    assert not flagged("popcorn", bare)


def test_household_corn_restriction_catches_popcorn(household):
    with Session(db.get_engine()) as session:
        corn = session.exec(select(Restriction).where(Restriction.term == "corn")).one()
        assert flagged("caramel popcorn", corn)
        assert not flagged("acorn squash", corn)
        assert not flagged("1 tbsp corn starch", corn)


# ---------- Exceptions ----------


@pytest.mark.parametrize("text", [
    "corn starch",
    "Corn Starch",
    "1 tbsp corn starch",
    "corn starches",
    "corn-starch",
    "cornstarch slurry",
    "whisk the cornstarch into the stock",
])
def test_exceptions_are_not_flagged(text):
    assert not flagged(text, CORN)


def test_exception_only_covers_its_own_occurrence():
    assert find_matches("corn starch and corn kernels", CORN) == ["corn"]
    assert flagged("thicken with cornstarch, then fold in the corn", CORN)


def test_exception_equal_to_the_term_does_not_switch_it_off():
    # An exception must say more than the term. "corn" as an exception for
    # "corn" would otherwise silently disable the restriction.
    sneaky = never("corn", exceptions=["corn", "corns"])
    assert flagged("corn", sneaky)
    assert flagged("corns", sneaky)


def test_exceptions_belong_to_their_own_restriction():
    # Corn's "corn starch" exception does nothing for a different restriction.
    starch = never("starch")
    assert flagged("corn starch", starch)


def test_exception_covers_alias_matches_too():
    r = never("shrimp", aliases=["shrimp paste"], exceptions=["shrimp paste substitute"])
    assert not flagged("vegan shrimp paste substitute", r)
    assert flagged("shrimp paste", r)


# ---------- Plurals ----------


@pytest.mark.parametrize("restriction, text", [
    (never("shrimp"), "shrimps"),
    (never("prawn"), "king prawns"),
    (never("prawns"), "1 prawn"),           # plural term still catches the singular
    (never("tomato"), "tomatoes"),
    (never("tomatoes"), "a tomato"),
    (never("berry"), "mixed berries"),
    (never("berries"), "1 berry"),
    (never("dish"), "dishes"),
    (never("peach"), "peaches"),
    (never("broccoli"), "broccolis"),
    (never("shrimp paste"), "shrimp pastes"),  # last word of a phrase
])
def test_simple_plurals(restriction, text):
    assert flagged(text, restriction)


def test_plural_forms_still_respect_word_boundaries():
    assert not flagged("peppercorns", CORN)
    assert not flagged("acorns", CORN)


# ---------- Aliases ----------


@pytest.mark.parametrize("text", [
    "shrimp",
    "2 tsp shrimp paste",
    "dried shrimp",
    "shrimp stock",
    "XO sauce",
    "xo sauce",
    "XO-sauce",
    "a spoon of Bagoong",
    "toasted belacan",
    "tiger prawns",
    "prawn crackers",
    "shrimp's tails",
])
def test_shrimp_and_aliases(text):
    assert flagged(text, SHRIMP)


@pytest.mark.parametrize("text", ["broccoli", "Broccoli florets", "broccolini", "Broccolini, trimmed"])
def test_broccoli_and_aliases(text):
    assert flagged(text, BROCCOLI)


def test_shrimp_free_is_still_flagged():
    # A hyphen is a word boundary, so "shrimp-free" mentions shrimp. That's why
    # diet_notes (the one place meant for "no shrimp") isn't checked.
    assert flagged("shrimp-free", SHRIMP)


def test_unlisted_dishes_are_not_guessed():
    # Only the term and aliases count; "scampi" would need to be an alias.
    assert not flagged("scampi-style chicken", SHRIMP)


def test_multi_word_terms_allow_any_spacing():
    assert flagged("shrimp   paste", SHRIMP)
    assert flagged("XO\nsauce", SHRIMP)


def test_multi_word_alias_needs_all_its_words():
    r = never("xo sauce")
    assert not flagged("xo", r)
    assert not flagged("sauce", r)


# ---------- Conservative over-matches (documented on purpose) ----------


@pytest.mark.parametrize("restriction, text", [
    (BROCCOLI, "broccoli rabe"),        # not botanically broccoli, still flagged
    (CORN, "corn-fed chicken"),         # hyphen counts as a word boundary
    (CORN, "corn syrup"),
    (CORN, "candy corn"),
])
def test_conservative_matches(restriction, text):
    # These are flagged on purpose. If one is fine for someone, add it as an
    # exception in Settings (e.g. "corn-fed").
    assert flagged(text, restriction)


def test_hyphenated_exception_fixes_corn_fed():
    r = never("corn", exceptions=["corn-fed"])
    assert not flagged("corn-fed chicken", r)
    assert not flagged("corn fed chicken", r)
    assert flagged("corn-fed chicken with corn salsa", r)


# ---------- Robustness ----------


def test_accents_are_ignored():
    assert flagged("jalapeno", never("jalapeño"))
    assert flagged("Jalapeño slices", never("jalapeno"))
    assert flagged("crème fraîche", never("creme fraiche"))


def test_unicode_dashes_count_as_hyphens():
    assert flagged("XO–sauce", SHRIMP)  # en dash


def test_regex_characters_in_terms_are_literal():
    assert flagged("c++ sauce", never("c++"))
    assert not flagged("cat", never("c.t"))
    assert flagged("(corn)", never("(corn)"))


def test_blank_terms_are_ignored():
    r = never("shrimp", aliases=["", "   "], exceptions=["", " "])
    assert not flagged("chicken", r)
    assert flagged("shrimp", r)


def test_blank_main_term_with_alias_still_works():
    assert flagged("prawn", never("  ", aliases=["prawn"]))


def test_empty_text():
    assert find_matches("", SHRIMP) == []


def test_overlapping_matches_report_the_longest():
    assert find_matches("2 tsp shrimp paste", SHRIMP) == ["shrimp paste"]
    assert find_matches("shrimp and prawns", SHRIMP) == ["shrimp", "prawns"]


# ---------- check_recipe ----------


def recipe(**overrides):
    """A clean recipe in the SPEC.md §3 shape."""
    base = {
        "title": "Lemon Garlic Chicken",
        "summary": "Pan-seared chicken thighs with a bright lemon sauce.",
        "cuisine": "American",
        "servings": 2,
        "total_time_minutes": 30,
        "difficulty": "easy",
        "ingredients": [
            {"name": "chicken thighs", "quantity": "4", "unit": None, "source": "pantry"},
            {"name": "lemon", "quantity": "1", "unit": None, "source": "buy"},
            {"name": "garlic", "quantity": "3", "unit": "cloves", "source": "staple"},
        ],
        "steps": ["Season the chicken.", "Sear 6 minutes per side.", "Add lemon and garlic."],
        "missing_ingredients": ["lemon"],
        "diet_notes": "No shrimp, broccoli or corn.",
        "variation_note": None,
    }
    base.update(overrides)
    return base


ALL = [SHRIMP, BROCCOLI, CORN]


def test_clean_recipe_passes():
    assert check_recipe(recipe(), ALL) == []


@pytest.mark.parametrize("overrides, where", [
    ({"title": "Shrimp Scampi"}, "title"),
    ({"summary": "A quick stir-fry finished with XO sauce."}, "summary"),
    ({"ingredients": [{"name": "sweet corn", "quantity": "1", "unit": "cup", "source": "buy"}]},
     "ingredient"),
    ({"missing_ingredients": ["broccolini"]}, "missing ingredient"),
    ({"steps": ["Boil water.", "Toss in the prawns."]}, "step 2"),
])
def test_each_checked_field_is_checked(overrides, where):
    violations = check_recipe(recipe(**overrides), ALL)
    assert [v.where for v in violations] == [where]


def test_diet_notes_and_variation_note_are_not_checked():
    r = recipe(diet_notes="Shrimp-free, no corn or broccoli.",
               variation_note="Swapped the shrimp for chicken.")
    assert check_recipe(r, ALL) == []


def test_quantity_and_unit_are_not_checked():
    r = recipe(ingredients=[{"name": "rice", "quantity": "corn", "unit": "shrimp", "source": "pantry"}])
    assert check_recipe(r, ALL) == []


def test_avoid_and_inactive_restrictions_are_ignored():
    fish_avoid = never("fish", level="avoid")
    inactive = never("chicken", active=False)
    assert check_recipe(recipe(title="Fish and Chicken"), [fish_avoid, inactive]) == []


def test_violations_name_the_restriction_and_place():
    shrimp = never("shrimp", id=1, person_id=1)
    corn = never("corn", id=4, person_id=2)
    r = recipe(title="Shrimp and Corn Chowder",
               steps=["Simmer the corn.", "Add shrimp."])
    assert check_recipe(r, [shrimp, corn]) == [
        Violation(restriction_id=1, person_id=1, term="shrimp", matched="shrimp", where="title"),
        Violation(restriction_id=4, person_id=2, term="corn", matched="corn", where="title"),
        Violation(restriction_id=4, person_id=2, term="corn", matched="corn", where="step 1"),
        Violation(restriction_id=1, person_id=1, term="shrimp", matched="shrimp", where="step 2"),
    ]


def test_missing_fields_are_tolerated():
    assert check_recipe({"title": "Corn Fritters"}, ALL)[0].where == "title"
    assert check_recipe({}, ALL) == []
    assert check_recipe({"title": None, "steps": None, "ingredients": None}, ALL) == []


def test_recipe_texts_lists_checked_fields_in_order():
    assert recipe_texts(recipe()) == [
        ("title", "Lemon Garlic Chicken"),
        ("summary", "Pan-seared chicken thighs with a bright lemon sauce."),
        ("ingredient", "chicken thighs"),
        ("ingredient", "lemon"),
        ("ingredient", "garlic"),
        ("missing ingredient", "lemon"),
        ("step 1", "Season the chicken."),
        ("step 2", "Sear 6 minutes per side."),
        ("step 3", "Add lemon and garlic."),
    ]


# ---------- violation_message ----------


def test_violation_message():
    vs = [
        Violation(restriction_id=4, person_id=2, term="corn", matched="sweet corn", where="ingredient"),
        Violation(restriction_id=1, person_id=1, term="shrimp", matched="prawns", where="step 2"),
    ]
    assert violation_message(vs) == (
        'Contains "sweet corn" (restricted: corn) in ingredient; '
        '"prawns" (restricted: shrimp) in step 2.'
    )


def test_violation_message_empty():
    assert violation_message([]) == ""


# ---------- Loading restrictions for the people eating ----------


@pytest.fixture
def session(household):
    with Session(db.get_engine()) as s:
        yield s


def person_id(session, name):
    return session.exec(select(Person).where(Person.name == name)).one().id


def terms(rows):
    return sorted(r.term for r in rows)


def test_never_restrictions_for_each_person(session):
    alex, jordan = person_id(session, "Alex"), person_id(session, "Jordan")
    assert terms(never_restrictions_for(session, [alex])) == ["shrimp"]
    assert terms(never_restrictions_for(session, [jordan])) == ["broccoli", "corn"]
    assert terms(never_restrictions_for(session, [alex, jordan])) == ["broccoli", "corn", "shrimp"]
    assert never_restrictions_for(session, []) == []


def test_never_restrictions_skip_avoid_rows(session):
    alex = person_id(session, "Alex")
    rows = never_restrictions_for(session, [alex])
    assert all(r.level == "never" for r in rows)
    assert "fish" not in terms(rows)


def test_seeded_restrictions_end_to_end(session):
    both = never_restrictions_for(session, [person_id(session, "Alex"), person_id(session, "Jordan")])
    bad = recipe(title="Shrimp Fried Rice", steps=["Thicken with corn starch.", "Add popcorn."])
    assert [(v.term, v.matched, v.where) for v in check_recipe(bad, both)] == [
        ("shrimp", "shrimp", "title"),
        ("corn", "popcorn", "step 2"),
    ]
    assert check_recipe(recipe(), both) == []
