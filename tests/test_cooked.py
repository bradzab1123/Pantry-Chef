"""Tests for "Cooked it" (app/cooked.py, app/routers/cooked.py).

The Anthropic client is always the FakeLLM from conftest.py; the autouse
no_real_anthropic fixture makes any un-faked call fail loudly.
"""

import re

from sqlmodel import select

from app.models import PantryItem, PlanItem, SavedRecipe, ShoppingItem
from app.pantry import normalize_name
from test_generation import HTMX, add_pantry, recipe, session
from test_saved import cook_batch

# ---------- Helpers ----------


def stir_fry(title="Chicken Stir-Fry"):
    """Pantry chicken breast and rice, a staple, and two things to buy."""
    return recipe(title, ingredients=[
        ("boneless chicken breasts", "pantry"),
        ("rice", "pantry"),
        ("olive oil", "staple"),
        ("bell pepper", "buy"),
        ("ginger", "buy"),
    ], missing=["bell pepper", "ginger"])


def add_saved_recipe(r) -> int:
    with session() as s:
        row = SavedRecipe(title=r["title"], recipe_json=r)
        s.add(row)
        s.commit()
        return row.id


def add_plan(r, saved_recipe_id=None) -> int:
    with session() as s:
        row = PlanItem(recipe_json=r, saved_recipe_id=saved_recipe_id)
        s.add(row)
        s.commit()
        return row.id


def pantry() -> dict[str, str]:
    """{name: status} for everything in the pantry."""
    with session() as s:
        return {i.name: i.status for i in s.exec(select(PantryItem))}


def pantry_id(name) -> int:
    with session() as s:
        return s.exec(select(PantryItem).where(PantryItem.name == name)).one().id


def plan_ids() -> list[int]:
    with session() as s:
        return [p.id for p in s.exec(select(PlanItem))]


def list_rows() -> list[ShoppingItem]:
    with session() as s:
        return s.exec(select(ShoppingItem).order_by(ShoppingItem.id)).all()


def form(client, url):
    return client.get(url, headers=HTMX)


def cooked(client, url, used=(), to_list=True, **kwargs):
    data = {"used": [str(pantry_id(n)) if isinstance(n, str) else str(n) for n in used]}
    if to_list:
        data["to_list"] = "1"
    return client.post(url, data=data, headers=kwargs.pop("headers", HTMX), **kwargs)


def offered(html) -> list[str]:
    """Names of the pantry items the form offers, in order."""
    return re.findall(r'name="used" value="\d+">\s*<span>([^<]+)</span>', html)


# ---------- The form ----------


def test_cook_card_shows_cooked_it(client, fake_llm):
    [sid] = cook_batch(client, fake_llm, stir_fry())
    assert f'hx-get="/cooked/suggestion/{sid}"' in client.get("/").text


def test_form_from_a_cook_card(client, fake_llm):
    [sid] = cook_batch(client, fake_llm, stir_fry())
    add_pantry("chicken breast, rice, olive oil, tofu")
    html = form(client, f"/cooked/suggestion/{sid}").text
    # Containment match for the chicken; the staple and tofu aren't offered.
    assert offered(html) == ["chicken breast", "rice"]
    assert 'name="to_list" value="1" checked' in html
    assert f'hx-post="/cooked/suggestion/{sid}"' in html
    # Every item starts as "still have": nothing ticked.
    assert 'value="1" checked' not in html.split('name="to_list"')[0]


def test_form_from_a_saved_recipe(client):
    saved_id = add_saved_recipe(stir_fry())
    add_pantry("chicken breast")
    assert f'hx-get="/cooked/saved/{saved_id}"' in client.get(f"/saved/{saved_id}").text
    assert offered(form(client, f"/cooked/saved/{saved_id}").text) == ["chicken breast"]


def test_form_from_a_planned_recipe(client):
    plan_id = add_plan(stir_fry())
    add_pantry("rice")
    assert f'hx-get="/cooked/plan/{plan_id}"' in client.get("/list").text
    html = form(client, f"/cooked/plan/{plan_id}").text
    assert offered(html) == ["rice"]
    assert 'hx-target="#list-page"' in html


def test_bought_since_buy_ingredient_is_offered_but_staples_are_not(client):
    saved_id = add_saved_recipe(stir_fry())
    add_pantry("ginger")           # was "buy" in the recipe, bought since
    add_pantry("olive oil")        # a staple ingredient, even though it's in Have
    add_pantry("bell pepper", "buying")  # not in Have yet
    assert offered(form(client, f"/cooked/saved/{saved_id}").text) == ["ginger"]


def test_form_lists_each_item_once(client):
    r = recipe("Rice Bowl", ingredients=[("rice", "pantry"), ("cooked rice", "pantry")])
    saved_id = add_saved_recipe(r)
    add_pantry("rice")
    assert offered(form(client, f"/cooked/saved/{saved_id}").text) == ["rice"]


def test_form_when_nothing_is_in_have(client):
    saved_id = add_saved_recipe(stir_fry())
    html = form(client, f"/cooked/saved/{saved_id}").text
    assert "None of this recipe's ingredients are in Have." in html
    assert "Done" in html
    assert 'name="to_list"' not in html


def test_old_recipe_without_sources_still_works(client):
    saved_id = add_saved_recipe({"title": "Old Recipe", "ingredients": [{"name": "rice"}]})
    add_pantry("rice")
    response = form(client, f"/cooked/saved/{saved_id}")
    assert response.status_code == 200
    assert offered(response.text) == []


def test_cancel_puts_the_button_back(client):
    saved_id = add_saved_recipe(stir_fry())
    html = client.get(f"/cooked/saved/{saved_id}/cancel", headers=HTMX).text
    assert ">Cooked it</a>" in html


def test_unknown_recipes_are_404(client):
    for url in ["/cooked/suggestion/999", "/cooked/saved/999", "/cooked/plan/999", "/cooked/bogus/1"]:
        assert form(client, url).status_code == 404
        assert client.post(url, headers=HTMX).status_code == 404


# ---------- Submitting ----------


def test_used_up_items_move_to_buying_by_default(client, fake_llm):
    [sid] = cook_batch(client, fake_llm, stir_fry())
    add_pantry("chicken breast, rice")
    response = cooked(client, f"/cooked/suggestion/{sid}", used=["chicken breast"])
    assert pantry() == {"chicken breast": "buying", "rice": "have"}
    assert "Moved chicken breast to Buying." in response.text
    assert response.headers["HX-Trigger"] == "pantry-changed"
    # ...and Buying items are on the shopping list.
    assert "chicken breast" in client.get("/list").text


def test_used_up_items_are_deleted_when_unticked(client):
    saved_id = add_saved_recipe(stir_fry())
    add_pantry("chicken breast, rice")
    response = cooked(client, f"/cooked/saved/{saved_id}", used=["chicken breast", "rice"],
                      to_list=False)
    assert pantry() == {}
    assert "Removed chicken breast, rice from the pantry." in response.text


def test_nothing_used_up(client):
    saved_id = add_saved_recipe(stir_fry())
    add_pantry("rice")
    response = cooked(client, f"/cooked/saved/{saved_id}")
    assert pantry() == {"rice": "have"}
    assert "Nothing marked used up." in response.text


def test_cooking_a_planned_recipe_takes_it_off_and_syncs(client):
    plan_id = add_plan(stir_fry())
    client.get("/list")  # sync: bell pepper and ginger go on the list
    assert sorted(r.name for r in list_rows()) == ["bell pepper", "ginger"]
    add_pantry("rice")

    response = cooked(client, f"/cooked/plan/{plan_id}", used=["rice"])
    assert plan_ids() == []
    assert list_rows() == []  # the recipe's missing ingredients went with it
    assert pantry() == {"rice": "buying"}
    # The whole List comes back, with the message on top and rice to buy.
    assert 'id="list-page"' in response.text
    assert "Moved rice to Buying. Taken off this week&#39;s plan." in response.text
    assert "Nothing planned." in response.text


def test_cooking_a_saved_recipe_thats_planned_takes_it_off(client):
    r = stir_fry()
    saved_id = add_saved_recipe(r)
    client.post(f"/saved/{saved_id}/plan", headers=HTMX)
    other = add_plan(stir_fry("Something Else"))
    response = cooked(client, f"/cooked/saved/{saved_id}")
    assert plan_ids() == [other]
    assert "Taken off this week&#39;s plan." in response.text
    assert sorted(r.name for r in list_rows()) == ["bell pepper", "ginger"]  # Something Else's


def test_cooking_a_card_thats_planned_takes_it_off(client, fake_llm):
    [sid] = cook_batch(client, fake_llm, stir_fry())
    client.post(f"/suggestions/{sid}/plan", headers=HTMX)
    assert len(plan_ids()) == 1
    cooked(client, f"/cooked/suggestion/{sid}")
    assert plan_ids() == []
    assert list_rows() == []


def test_unplanned_recipe_doesnt_mention_the_plan(client):
    saved_id = add_saved_recipe(stir_fry())
    assert "plan" not in cooked(client, f"/cooked/saved/{saved_id}").text


def test_stale_and_foreign_ids_are_ignored(client):
    saved_id = add_saved_recipe(stir_fry())
    add_pantry("chicken breast, rice, tofu")  # tofu isn't in the recipe
    add_pantry("ginger", "buying")            # in the recipe, but not in Have
    gone = pantry_id("rice")
    with session() as s:
        s.delete(s.get(PantryItem, gone))
        s.commit()

    response = cooked(client, f"/cooked/saved/{saved_id}", to_list=False,
                      used=["tofu", "ginger", gone, 999, "chicken breast"])
    assert response.status_code == 200
    assert pantry() == {"tofu": "have", "ginger": "buying"}
    assert "Removed chicken breast from the pantry." in response.text


def test_junk_ids_are_ignored(client):
    saved_id = add_saved_recipe(stir_fry())
    add_pantry("rice")
    response = client.post(f"/cooked/saved/{saved_id}", data={"used": ["abc", "", "-1"]},
                           headers=HTMX)
    assert response.status_code == 200
    assert pantry() == {"rice": "have"}


def test_double_submit_is_harmless(client):
    saved_id = add_saved_recipe(stir_fry())
    add_pantry("rice")
    rice = pantry_id("rice")
    cooked(client, f"/cooked/saved/{saved_id}", used=[rice])
    response = cooked(client, f"/cooked/saved/{saved_id}", used=[rice], to_list=False)
    # Already in Buying, so the second (delete) post leaves it alone.
    assert pantry() == {"rice": "buying"}
    assert "Nothing marked used up." in response.text


def test_recipe_comes_from_the_db_not_the_form(client):
    saved_id = add_saved_recipe(stir_fry())
    add_pantry("tofu")
    client.post(f"/cooked/saved/{saved_id}", headers=HTMX, data={
        "used": [str(pantry_id("tofu"))], "to_list": "1",
        "recipe_json": '{"ingredients": [{"name": "tofu", "source": "pantry"}]}',
    })
    assert pantry() == {"tofu": "have"}


def test_moving_to_buying_drops_a_duplicate_manual_line(client):
    saved_id = add_saved_recipe(stir_fry())
    add_pantry("rice")
    with session() as s:
        s.add(ShoppingItem(name="rice", normalized_name=normalize_name("rice"), source="manual"))
        s.commit()
    cooked(client, f"/cooked/saved/{saved_id}", used=["rice"])
    assert list_rows() == []
    html = client.get("/list").text
    assert html.count("Got rice") == 1  # once, as the Buying item


def test_old_cart_row_no_longer_undoes_the_cooking(client):
    """Bought rice on the List (checked, so it's in Have), cooked with it,
    then unchecked the old cart row: rice should stay on the Buying list."""
    saved_id = add_saved_recipe(stir_fry())
    client.post("/list/items", data={"names": "rice"}, headers=HTMX)
    row_id = list_rows()[0].id
    client.post(f"/list/items/{row_id}/check", headers=HTMX)
    assert pantry() == {"rice": "have"}

    cooked(client, f"/cooked/saved/{saved_id}", used=["rice"])
    client.post(f"/list/items/{row_id}/uncheck", headers=HTMX)
    assert pantry() == {"rice": "buying"}


def test_plain_posts_redirect_back(client, fake_llm):
    [sid] = cook_batch(client, fake_llm, stir_fry())
    saved_id = add_saved_recipe(stir_fry("Saved One"))
    plan_id = add_plan(stir_fry("Planned One"))
    for url, back in [(f"/cooked/suggestion/{sid}", "/"),
                      (f"/cooked/saved/{saved_id}", f"/saved/{saved_id}"),
                      (f"/cooked/plan/{plan_id}", "/list")]:
        response = client.post(url, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == back


def test_plain_get_is_a_page_of_its_own(client):
    saved_id = add_saved_recipe(stir_fry())
    add_pantry("rice")
    html = client.get(f"/cooked/saved/{saved_id}").text
    assert "<nav" in html or "tabbar" in html
    assert "Cooked Chicken Stir-Fry" in html
    assert offered(html) == ["rice"]
    response = client.get(f"/cooked/saved/{saved_id}/cancel", follow_redirects=False)
    assert response.headers["location"] == f"/saved/{saved_id}"
