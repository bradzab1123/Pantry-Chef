"""Phase 6 tests: this week's plan and the shopping list (app/shopping.py, the List tab).

The Anthropic client is always the FakeLLM from conftest.py; the autouse
no_real_anthropic fixture makes any un-faked call fail loudly.
"""

import json

from sqlmodel import select

from app import shopping
from app.models import PantryItem, PlanItem, Restriction, SavedRecipe, ShoppingItem, Staple
from test_generation import HTMX, add_pantry, recipe, session
from test_saved import add_saved, cook_batch, get_suggestion

# ---------- Helpers ----------


def buying_recipe(title, *buys):
    """A recipe whose missing ingredients are `buys`."""
    return recipe(title, ingredients=[("chicken thighs", "pantry")] + [(b, "buy") for b in buys],
                  missing=list(buys))


def plan_rows() -> list[PlanItem]:
    with session() as s:
        return s.exec(select(PlanItem).order_by(PlanItem.id)).all()


def list_rows() -> list[ShoppingItem]:
    with session() as s:
        return s.exec(select(ShoppingItem).order_by(ShoppingItem.id)).all()


def unchecked_names() -> list[str]:
    return sorted(r.name for r in list_rows() if not r.checked)


def checked_names() -> list[str]:
    return sorted(r.name for r in list_rows() if r.checked)


def pantry(status) -> list[str]:
    with session() as s:
        return sorted(i.name for i in s.exec(select(PantryItem).where(PantryItem.status == status)))


def plan_saved(client, *recipes) -> list[int]:
    """Save these recipe dicts and add them all to the plan; return the saved ids."""
    ids = []
    with session() as s:
        for r in recipes:
            row = SavedRecipe(title=r["title"], recipe_json=r)
            s.add(row)
            s.commit()
            ids.append(row.id)
    for saved_id in ids:
        client.post(f"/saved/{saved_id}/plan", headers=HTMX)
    return ids


def item_id(name) -> int:
    return next(r.id for r in list_rows() if r.name == name)


def pantry_id(name) -> int:
    with session() as s:
        return s.exec(select(PantryItem).where(PantryItem.name == name)).one().id


def check(client, name):
    return client.post(f"/list/items/{item_id(name)}/check", headers=HTMX)


def uncheck(client, name):
    return client.post(f"/list/items/{item_id(name)}/uncheck", headers=HTMX)


def add_manual(client, names):
    return client.post("/list/items", data={"names": names}, headers=HTMX)


# ---------- Adding to the plan ----------


def test_add_card_to_plan_copies_the_suggestion(client, fake_llm):
    [sid] = cook_batch(client, fake_llm, buying_recipe("Lemon Chicken", "lemons"))
    # Anything posted alongside is ignored: the recipe comes from the suggestion row.
    fake = json.dumps(recipe("Shrimp Surprise", missing=["shrimp"]))
    html = client.post(f"/suggestions/{sid}/plan", data={"recipe_json": fake}, headers=HTMX).text

    [row] = plan_rows()
    assert row.recipe_json == get_suggestion(sid).recipe_json
    assert row.saved_recipe_id is None  # unsaved cards can be planned too
    assert "✓ In plan" in html
    assert unchecked_names() == ["lemons"]


def test_double_tap_plans_once(client, fake_llm):
    [sid] = cook_batch(client, fake_llm, recipe())
    client.post(f"/suggestions/{sid}/plan", headers=HTMX)
    client.post(f"/suggestions/{sid}/plan", headers=HTMX)
    assert len(plan_rows()) == 1


def test_cards_remember_they_are_planned(client, fake_llm):
    first, second = cook_batch(client, fake_llm, recipe("A"), recipe("B"))
    client.post(f"/suggestions/{first}/plan", headers=HTMX)
    html = client.get("/").text
    assert html.count("✓ In plan") == 1
    assert f'action="/suggestions/{second}/plan"' in html
    assert f'action="/suggestions/{first}/plan"' not in html


def test_saved_card_planned_links_to_saved_recipe(client, fake_llm):
    [sid] = cook_batch(client, fake_llm, recipe("Lemon Chicken"))
    client.post(f"/suggestions/{sid}/save", headers=HTMX)
    client.post(f"/suggestions/{sid}/plan", headers=HTMX)
    [row] = plan_rows()
    assert row.saved_recipe_id == get_suggestion(sid).saved_recipe_id
    # The saved recipe's own page knows it's planned (same recipe JSON).
    assert "✓ In plan" in client.get(f"/saved/{row.saved_recipe_id}").text


def test_add_saved_recipe_to_plan(client):
    saved_id = add_saved("Pesto Pasta", missing=["basil"])
    page = client.get(f"/saved/{saved_id}").text
    assert f'action="/saved/{saved_id}/plan"' in page

    html = client.post(f"/saved/{saved_id}/plan", headers=HTMX).text
    assert "✓ In plan" in html
    [row] = plan_rows()
    assert row.saved_recipe_id == saved_id and row.recipe_json["title"] == "Pesto Pasta"
    assert "Pesto Pasta" in client.get("/list").text


def test_deleting_saved_recipe_takes_it_off_the_plan(client):
    [saved_id] = plan_saved(client, buying_recipe("Pesto Pasta", "basil"))
    client.post(f"/saved/{saved_id}/delete")
    assert plan_rows() == []
    client.get("/list")  # the page catches the list up
    assert unchecked_names() == []


def test_plan_unknown_ids_are_404(client):
    assert client.post("/suggestions/999/plan", headers=HTMX).status_code == 404
    assert client.post("/saved/999/plan", headers=HTMX).status_code == 404


def test_plan_plain_posts_redirect(client, fake_llm):
    [sid] = cook_batch(client, fake_llm, recipe())
    response = client.post(f"/suggestions/{sid}/plan", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"

    saved_id = add_saved("Other")
    response = client.post(f"/saved/{saved_id}/plan", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == f"/saved/{saved_id}"


# ---------- Building the list from the plan ----------


def test_list_merges_duplicates_across_recipes(client):
    plan_saved(client, buying_recipe("A", "lemons", "Fresh Basil"),
               buying_recipe("B", "lemon", "feta"))
    assert unchecked_names() == ["feta", "fresh basil", "lemons"]
    # Each item says which recipes need it.
    html = client.get("/list").text
    assert "for A, B" in html


def test_list_skips_have_buying_and_enabled_staples(client):
    add_pantry("feta")
    add_pantry("capers", "buying")
    plan_saved(client, buying_recipe("A", "Feta", "capers", "butter", "lemons"))
    # feta is in Have, capers in Buying (shown on its own), butter is a staple.
    assert unchecked_names() == ["lemons"]


def test_disabled_staple_goes_on_the_list(client):
    with session() as s:
        butter = s.exec(select(Staple).where(Staple.name == "butter")).one()
        butter.enabled = False
        s.add(butter)
        s.commit()
    plan_saved(client, buying_recipe("A", "butter"))
    assert unchecked_names() == ["butter"]


def test_list_catches_up_with_pantry_changes(client):
    plan_saved(client, buying_recipe("A", "lemons", "feta"))
    add_pantry("lemon")
    client.get("/list")
    assert unchecked_names() == ["feta"]


def test_removing_a_recipe_drops_its_items_but_keeps_checked_ones(client):
    plan_saved(client, buying_recipe("A", "lemons", "feta"), buying_recipe("B", "feta", "basil"))
    check(client, "basil")
    first, second = plan_rows()

    client.post(f"/plan/{second.id}/delete", headers=HTMX)
    assert [p.id for p in plan_rows()] == [first.id]
    assert unchecked_names() == ["feta", "lemons"]  # feta is still needed by A
    assert checked_names() == ["basil"]             # already in the cart


def test_clear_plan(client):
    plan_saved(client, buying_recipe("A", "lemons"), buying_recipe("B", "feta"))
    add_manual(client, "foil")
    check(client, "feta")

    html = client.post("/plan/clear", headers=HTMX).text
    assert plan_rows() == []
    assert unchecked_names() == ["foil"]  # manual items stay
    assert checked_names() == ["feta"]
    assert "Nothing planned" in html


def test_sync_is_idempotent(client):
    plan_saved(client, buying_recipe("A", "lemons", "feta"))
    before = [(r.id, r.name) for r in list_rows()]
    with session() as s:
        shopping.sync(s)
        shopping.sync(s)
    assert [(r.id, r.name) for r in list_rows()] == before


# ---------- Manual items ----------


def test_manual_add_splits_and_dedupes(client):
    html = add_manual(client, "Milk, paper towels\nmilk").text
    assert unchecked_names() == ["milk", "paper towels"]
    assert all(r.source == "manual" for r in list_rows())
    assert "already" not in html  # repeats within one add are just dropped


def test_manual_add_allowed_when_in_have(client):
    add_pantry("milk")
    add_manual(client, "milk")
    assert unchecked_names() == ["milk"]


def test_manual_add_skips_whats_already_on_the_list(client):
    add_pantry("capers", "buying")
    plan_saved(client, buying_recipe("A", "lemons"))
    html = add_manual(client, "lemon, capers, foil").text
    assert unchecked_names() == ["foil", "lemons"]
    assert "lemon, capers are already on the list." in html


def test_manual_item_can_be_deleted(client):
    add_manual(client, "foil")
    client.post(f"/list/items/{item_id('foil')}/delete", headers=HTMX)
    assert list_rows() == []
    # A double tap on something already gone is fine.
    assert client.post("/list/items/999/delete", headers=HTMX).status_code == 200


def test_plan_items_cannot_be_deleted_one_by_one(client):
    plan_saved(client, buying_recipe("A", "lemons"))
    lemons = item_id("lemons")
    assert f"/list/items/{lemons}/delete" not in client.get("/list").text  # no × button
    client.post(f"/list/items/{lemons}/delete", headers=HTMX)
    assert unchecked_names() == ["lemons"]


# ---------- Checking off ----------


def test_checking_a_plan_item_moves_it_to_have(client):
    plan_saved(client, buying_recipe("A", "lemons"))
    html = check(client, "lemons").text
    assert pantry("have") == ["lemons"]
    assert checked_names() == ["lemons"] and unchecked_names() == []
    assert "In the cart" in html
    # Still checked after the page catches up (it doesn't come back as "to buy").
    client.get("/list")
    assert checked_names() == ["lemons"] and unchecked_names() == []


def test_unchecking_takes_it_back_out_of_have(client):
    plan_saved(client, buying_recipe("A", "lemons"))
    check(client, "lemons")
    uncheck(client, "lemons")
    assert pantry("have") == []
    assert unchecked_names() == ["lemons"] and checked_names() == []


def test_checking_when_already_in_have_doesnt_duplicate(client):
    add_pantry("milk")
    add_manual(client, "milk")
    check(client, "milk")
    assert pantry("have") == ["milk"]
    uncheck(client, "milk")
    # The milk that was already there stays.
    assert pantry("have") == ["milk"]
    assert unchecked_names() == ["milk"]


def test_buying_items_show_on_the_list(client):
    add_pantry("capers", "buying")
    html = client.get("/list").text
    assert f'action="/list/buying/{pantry_id("capers")}/check"' in html
    assert "capers" in html and "Buying" in html


def test_checking_a_buying_item_moves_it_to_have_and_back(client):
    add_pantry("capers", "buying")
    client.post(f"/list/buying/{pantry_id('capers')}/check", headers=HTMX)
    assert pantry("have") == ["capers"] and pantry("buying") == []
    [row] = list_rows()
    assert row.checked and row.source == "buying"

    uncheck(client, "capers")
    assert pantry("buying") == ["capers"] and pantry("have") == []
    assert list_rows() == []  # it's shown from the Buying list again


def test_checking_a_manual_item_thats_also_in_buying(client):
    add_manual(client, "capers")
    add_pantry("capers", "buying")  # added on the Pantry tab afterwards
    check(client, "capers")
    assert pantry("have") == ["capers"] and pantry("buying") == []
    uncheck(client, "capers")
    assert pantry("buying") == ["capers"]


def test_unchecking_a_buying_item_whose_pantry_row_was_deleted(client):
    add_pantry("capers", "buying")
    client.post(f"/list/buying/{pantry_id('capers')}/check", headers=HTMX)
    client.post(f"/pantry/items/{pantry_id('capers')}/delete", headers=HTMX)  # "used it up"
    uncheck(client, "capers")
    assert pantry("buying") == ["capers"]


def test_clear_checked(client):
    plan_saved(client, buying_recipe("A", "lemons", "feta"))
    check(client, "lemons")
    client.post("/list/clear-checked", headers=HTMX)
    assert checked_names() == []
    assert unchecked_names() == ["feta"]
    assert pantry("have") == ["lemons"]  # clearing doesn't touch the pantry


def test_check_unknown_items_is_404(client):
    assert client.post("/list/items/999/check", headers=HTMX).status_code == 404
    assert client.post("/list/items/999/uncheck", headers=HTMX).status_code == 404
    assert client.post("/list/buying/999/check", headers=HTMX).status_code == 404


def test_double_tap_check_is_harmless(client):
    plan_saved(client, buying_recipe("A", "lemons"))
    check(client, "lemons")
    check(client, "lemons")
    assert pantry("have") == ["lemons"]


# ---------- The page ----------


def test_list_page_empty(client):
    html = client.get("/list").text
    assert "Nothing planned" in html
    assert "Nothing to buy" in html


def test_list_plain_posts_redirect(client):
    response = client.post("/list/items", data={"names": "foil"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/list"
    response = client.post("/plan/clear", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/list"


def test_planned_recipe_with_a_never_hit_gets_a_heads_up(client):
    # Planned before a new `never` rule existed.
    plan_saved(client, recipe("Pea Risotto", ingredients=[("arborio rice", "pantry"), ("peas", "pantry")]))
    with session() as s:
        s.add(Restriction(person_id=1, term="pea", level="never"))
        s.commit()
    html = client.get("/list").text
    assert "Heads up" in html and "never" in html
