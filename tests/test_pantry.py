"""Phase 2 tests: pantry name helpers and the Pantry page routes."""

import pytest
from sqlmodel import Session, select

from app import db
from app.models import PantryItem
from app.pantry import clean_name, normalize_name, split_items, split_terms

HTMX = {"HX-Request": "true"}

# ---------- normalize_name ----------


def test_clean_name_trims_lowercases_and_collapses_spaces():
    assert clean_name("  Red   Onion ") == "red onion"


@pytest.mark.parametrize("singular,plural", [
    ("tomato", "tomatoes"),
    ("potato", "potatoes"),
    ("berry", "berries"),
    ("cookie", "cookies"),
    ("peach", "peaches"),
    ("radish", "radishes"),
    ("egg", "eggs"),
    ("olive", "olives"),
    ("cheese", "cheeses"),
    ("chili", "chilis"),
    ("pea", "peas"),
    ("pie", "pies"),
    ("glass", "glasses"),
    ("cherry tomato", "cherry tomatoes"),
    ("Red Onion", "red  onions"),
])
def test_singular_and_plural_match(singular, plural):
    assert normalize_name(singular) == normalize_name(plural)


@pytest.mark.parametrize("word", ["hummus", "asparagus", "couscous", "swiss", "sea bass"])
def test_words_ending_in_ss_or_us_are_left_alone(word):
    assert normalize_name(word) == word


def test_only_the_last_word_is_folded():
    # "cherry" isn't turned into "cherri" when it isn't the main noun.
    assert normalize_name("cherry tomatoes").startswith("cherry ")


def test_different_foods_stay_different():
    assert normalize_name("pea") != normalize_name("pear")
    assert normalize_name("lime") != normalize_name("lemon")
    assert normalize_name("bean sprouts") != normalize_name("beans")


def test_normalize_empty():
    assert normalize_name("   ") == ""


# ---------- split_items / split_terms ----------


def test_split_items_on_commas_and_newlines():
    assert split_items("Milk, eggs\nbread,,\n  \n Red  Onion ") == [
        "milk", "eggs", "bread", "red onion",
    ]


def test_split_items_drops_repeats_in_the_same_batch():
    assert split_items("egg, Eggs, EGG, tomato, tomatoes") == ["egg", "tomato"]


def test_split_terms_keeps_case_and_drops_repeats():
    assert split_terms("XO sauce, prawn\nxo sauce,  shrimp   paste ") == [
        "XO sauce", "prawn", "shrimp paste",
    ]


# ---------- Routes ----------


def pantry(status=None):
    """All pantry items as {name: status}, read straight from the DB."""
    with Session(db.get_engine()) as s:
        items = s.exec(select(PantryItem)).all()
    return {i.name: i.status for i in items if status is None or i.status == status}


def item_id(name):
    with Session(db.get_engine()) as s:
        return s.exec(select(PantryItem).where(PantryItem.name == name)).one().id


def add(client, names, status="have"):
    return client.post("/pantry/items", data={"names": names, "status": status}, headers=HTMX)


def test_pantry_page_renders(client):
    html = client.get("/pantry").text
    assert "Add to Have" in html
    assert "Add to Buying" in html
    assert "black pepper" in html  # staples summary


def test_bulk_add_to_have_and_buying(client):
    add(client, "Milk, eggs\nBread")
    add(client, "lemons", status="buying")
    assert pantry() == {"milk": "have", "eggs": "have", "bread": "have", "lemons": "buying"}


def test_bulk_add_stores_normalized_name(client):
    add(client, "Cherry Tomatoes")
    with Session(db.get_engine()) as s:
        item = s.exec(select(PantryItem)).one()
    assert item.name == "cherry tomatoes"
    assert item.normalized_name == normalize_name("cherry tomato")


def test_bulk_add_drops_duplicates_already_on_the_list(client):
    add(client, "tomatoes, milk")
    response = add(client, "Tomato, milk, bread")
    assert pantry() == {"tomatoes": "have", "milk": "have", "bread": "have"}
    assert "2 already on the list" in response.text


def test_bulk_add_skips_items_on_the_other_list_with_note(client):
    add(client, "milk")
    response = add(client, "milk, bread", status="buying")
    assert pantry() == {"milk": "have", "bread": "buying"}
    assert "milk is already in Have" in response.text


def test_bulk_add_rejects_unknown_status(client):
    response = client.post("/pantry/items", data={"names": "milk", "status": "fridge"})
    assert response.status_code == 400
    assert pantry() == {}


def test_htmx_gets_partial_and_plain_post_gets_redirect(client):
    response = add(client, "milk")
    assert response.status_code == 200
    assert 'id="pantry-lists"' in response.text
    assert 'class="tabbar"' not in response.text  # just the partial

    response = client.post(
        "/pantry/items", data={"names": "bread", "status": "have"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/pantry"
    assert "bread" in pantry()


def test_delete_item(client):
    add(client, "milk, bread")
    client.post(f"/pantry/items/{item_id('milk')}/delete", headers=HTMX)
    assert pantry() == {"bread": "have"}


def test_delete_missing_item_is_harmless(client):
    response = client.post("/pantry/items/999/delete", headers=HTMX)
    assert response.status_code == 200


def test_move_buying_to_have(client):
    add(client, "lemons", status="buying")
    client.post(f"/pantry/items/{item_id('lemons')}/have", headers=HTMX)
    assert pantry() == {"lemons": "have"}


def test_move_to_have_when_already_in_have_removes_the_buying_entry(client):
    # Can't happen through bulk add, but make sure we never end up with two.
    with Session(db.get_engine()) as s:
        s.add(PantryItem(name="lemon", normalized_name=normalize_name("lemon"), status="have"))
        s.add(PantryItem(name="lemons", normalized_name=normalize_name("lemons"), status="buying"))
        s.commit()
    client.post(f"/pantry/items/{item_id('lemons')}/have", headers=HTMX)
    assert pantry() == {"lemon": "have"}
