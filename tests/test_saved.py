"""Phase 5 tests: saving, ratings, the Saved tab and spin-offs.

The Anthropic client is always the FakeLLM from conftest.py; the autouse
no_real_anthropic fixture makes any un-faked call fail loudly.
"""

import re

import pytest
from sqlmodel import select

from app import config
from app.models import GenerationLog, Restriction, SavedRecipe, Suggestion
from test_generation import (
    HTMX, REMOVED_RESTRICTED, SHRIMP_RECIPE, add_log_rows, connection_error, generate, log_rows,
    recipe, session,
)

# ---------- Helpers ----------


def cook_batch(client, fake_llm, *recipes) -> list[int]:
    """Generate a Cook batch of these recipes; return the suggestion ids."""
    fake_llm.reply(list(recipes))
    generate(client)
    with session() as s:
        rows = s.exec(select(Suggestion).where(Suggestion.parent_recipe_id == None)  # noqa: E711
                      .order_by(Suggestion.id)).all()
        return [r.id for r in rows]


def add_saved(title="Lemon Chicken", rating=0, parent=None, **fields) -> int:
    with session() as s:
        row = SavedRecipe(title=title, recipe_json=recipe(title, **fields), rating=rating,
                          parent_recipe_id=parent)
        s.add(row)
        s.commit()
        return row.id


def saved_rows() -> list[SavedRecipe]:
    with session() as s:
        return s.exec(select(SavedRecipe).order_by(SavedRecipe.id)).all()


def get_saved(saved_id) -> SavedRecipe | None:
    with session() as s:
        return s.get(SavedRecipe, saved_id)


def get_suggestion(suggestion_id) -> Suggestion | None:
    with session() as s:
        return s.get(Suggestion, suggestion_id)


def spinoff_titles(parent_id) -> list[str]:
    with session() as s:
        rows = s.exec(select(Suggestion).where(Suggestion.parent_recipe_id == parent_id)
                      .order_by(Suggestion.id)).all()
        return [r.recipe_json["title"] for r in rows]


def cook_titles() -> list[str]:
    with session() as s:
        rows = s.exec(select(Suggestion).where(Suggestion.parent_recipe_id == None)  # noqa: E711
                      .order_by(Suggestion.id)).all()
        return [r.recipe_json["title"] for r in rows]


def variation(title, note="Swapped the chicken for tofu.", **fields):
    return recipe(title, variation_note=note, **fields)


def spin_off(client, saved_id):
    return client.post(f"/saved/{saved_id}/spinoff", headers=HTMX)


def thumbs_up_pressed(html) -> bool:
    return re.search(r'aria-label="Thumbs up"\s+aria-pressed="true"', html) is not None


def thumbs_down_pressed(html) -> bool:
    return re.search(r'aria-label="Thumbs down"\s+aria-pressed="true"', html) is not None


# ---------- Saving a card ----------


def test_save_copies_the_suggestion(client, fake_llm):
    [sid] = cook_batch(client, fake_llm, recipe("Lemon Chicken"))
    html = client.post(f"/suggestions/{sid}/save", headers=HTMX).text

    [row] = saved_rows()
    assert row.title == "Lemon Chicken"
    assert row.recipe_json == get_suggestion(sid).recipe_json
    assert row.rating == 0 and row.parent_recipe_id is None
    assert get_suggestion(sid).saved_recipe_id == row.id
    assert f'href="/saved/{row.id}">✓ Saved' in html
    assert ">Save<" not in html


def test_double_tap_saves_once(client, fake_llm):
    [sid] = cook_batch(client, fake_llm, recipe())
    client.post(f"/suggestions/{sid}/save", headers=HTMX)
    client.post(f"/suggestions/{sid}/save", headers=HTMX)
    assert len(saved_rows()) == 1


def test_cook_page_remembers_saved_cards(client, fake_llm):
    first, second = cook_batch(client, fake_llm, recipe("A"), recipe("B"))
    client.post(f"/suggestions/{first}/save", headers=HTMX)
    html = client.get("/").text
    assert html.count("✓ Saved") == 1
    assert f'action="/suggestions/{second}/save"' in html
    assert f'action="/suggestions/{first}/save"' not in html


def test_save_unknown_suggestion_is_404(client):
    assert client.post("/suggestions/999/save", headers=HTMX).status_code == 404
    assert client.post("/suggestions/999/rate", data={"rating": "1"}, headers=HTMX).status_code == 404


def test_save_plain_post_redirects(client, fake_llm):
    [sid] = cook_batch(client, fake_llm, recipe())
    response = client.post(f"/suggestions/{sid}/save", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert len(saved_rows()) == 1


def test_recipe_json_never_comes_from_the_form(client, fake_llm):
    """Only the suggestion id is used; anything else posted is ignored."""
    [sid] = cook_batch(client, fake_llm, recipe("Lemon Chicken"))
    client.post(f"/suggestions/{sid}/save", headers=HTMX,
                data={"title": "Shrimp Scampi", "recipe_json": '{"title": "Shrimp Scampi"}'})
    [row] = saved_rows()
    assert row.title == "Lemon Chicken"


# ---------- Ratings ----------


def test_thumbs_up_on_unsaved_card_saves_it(client, fake_llm):
    [sid] = cook_batch(client, fake_llm, recipe())
    html = client.post(f"/suggestions/{sid}/rate", data={"rating": "1"}, headers=HTMX).text

    [row] = saved_rows()
    assert row.rating == 1
    assert "✓ Saved" in html
    assert thumbs_up_pressed(html) and not thumbs_down_pressed(html)


def test_tapping_the_same_rating_clears_it_but_keeps_it_saved(client, fake_llm):
    [sid] = cook_batch(client, fake_llm, recipe())
    client.post(f"/suggestions/{sid}/rate", data={"rating": "1"}, headers=HTMX)
    html = client.post(f"/suggestions/{sid}/rate", data={"rating": "1"}, headers=HTMX).text
    [row] = saved_rows()
    assert row.rating == 0
    assert "✓ Saved" in html and not thumbs_up_pressed(html)


def test_thumbs_down_then_up_switches(client, fake_llm):
    [sid] = cook_batch(client, fake_llm, recipe())
    client.post(f"/suggestions/{sid}/rate", data={"rating": "-1"}, headers=HTMX)
    assert saved_rows()[0].rating == -1
    client.post(f"/suggestions/{sid}/rate", data={"rating": "1"}, headers=HTMX)
    assert [r.rating for r in saved_rows()] == [1]


@pytest.mark.parametrize("value", ["0", "2", "up", ""])
def test_bad_rating_is_400(client, fake_llm, value):
    [sid] = cook_batch(client, fake_llm, recipe())
    saved_id = add_saved()
    assert client.post(f"/suggestions/{sid}/rate", data={"rating": value}, headers=HTMX).status_code == 400
    assert client.post(f"/saved/{saved_id}/rate", data={"rating": value}, headers=HTMX).status_code == 400
    assert len(saved_rows()) == 1  # the bad rating didn't save the card


def test_rate_saved_recipe_toggles(client):
    saved_id = add_saved()
    html = client.post(f"/saved/{saved_id}/rate", data={"rating": "-1"}, headers=HTMX).text
    assert get_saved(saved_id).rating == -1 and thumbs_down_pressed(html)
    client.post(f"/saved/{saved_id}/rate", data={"rating": "-1"}, headers=HTMX)
    assert get_saved(saved_id).rating == 0


def test_rate_saved_plain_post_redirects(client):
    saved_id = add_saved()
    response = client.post(f"/saved/{saved_id}/rate", data={"rating": "1"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == f"/saved/{saved_id}"


def test_ratings_steer_the_next_generation(client, fake_llm):
    add_saved("Chicken Tikka", rating=1)
    add_saved("Bland Soup", rating=-1)
    add_saved("Just Saved", rating=0)
    fake_llm.reply([recipe()])
    generate(client)
    prompt = fake_llm.prompt()
    assert "Recipes we liked:\n- Chicken Tikka" in prompt
    assert "Recipes we didn't like:\n- Bland Soup" in prompt
    assert "Just Saved" not in prompt


# ---------- The Saved tab ----------


def test_saved_list_shows_recipes_newest_first(client):
    add_saved("Older")
    add_saved("Newer")
    html = client.get("/saved").text
    assert html.index("Newer") < html.index("Older")


def test_search_is_case_insensitive(client):
    add_saved("Lemon Chicken")
    add_saved("Fish Tacos")
    html = client.get("/saved?q=CHICK").text
    assert "Lemon Chicken" in html and "Fish Tacos" not in html


def test_search_percent_is_literal(client):
    add_saved("100% Rye Toast")
    add_saved("Lemon Chicken")
    html = client.get("/saved?q=%25").text
    assert "100% Rye Toast" in html and "Lemon Chicken" not in html


def test_search_with_htmx_returns_just_the_list(client):
    add_saved("Lemon Chicken")
    html = client.get("/saved?q=lemon", headers=HTMX).text
    assert 'id="saved-list"' in html and "Lemon Chicken" in html
    assert 'class="tabbar"' not in html
    assert 'href="/saved?fav=1&amp;q=lemon"' in html  # the filter keeps the search


def test_favorites_filter(client):
    add_saved("Loved It", rating=1)
    add_saved("Meh", rating=0)
    add_saved("Nope", rating=-1)
    html = client.get("/saved?fav=1").text
    assert "Loved It" in html and "Meh" not in html and "Nope" not in html


def test_empty_messages(client):
    assert "Nothing saved yet" in client.get("/saved").text
    assert "No favorites yet" in client.get("/saved?fav=1").text
    assert "No saved recipes match" in client.get("/saved?q=zzz").text


def test_detail_page_shows_the_recipe(client):
    saved_id = add_saved("Lemon Chicken", steps=["Season the chicken.", "Roast it."])
    html = client.get(f"/saved/{saved_id}").text
    assert "Lemon Chicken" in html
    assert "Roast it." in html and "chicken thighs" in html
    assert f'action="/saved/{saved_id}/spinoff"' in html
    assert f'action="/saved/{saved_id}/delete"' in html


def test_detail_unknown_is_404(client):
    assert client.get("/saved/999").status_code == 404


def test_heads_up_when_a_later_never_rule_matches(client):
    """A recipe saved before a rule existed gets flagged (display only)."""
    saved_id = add_saved("Pea Risotto", ingredients=[("frozen peas", "pantry"), ("rice", "pantry")])
    assert "Contains" not in client.get("/saved").text

    with session() as s:
        s.add(Restriction(person_id=2, term="pea", level="never", aliases=[], exceptions=[]))
        s.commit()
    assert "⚠ Contains pea<" in client.get("/saved").text
    detail = client.get(f"/saved/{saved_id}").text
    # One heads-up per restriction, listing everything it matched.
    assert detail.count("Heads up:") == 1
    assert "Heads up: contains “pea”, “peas”" in detail
    assert "for Jordan (pea)" in detail


def test_avoid_rules_do_not_raise_a_heads_up(client):
    add_saved("Fish Tacos", ingredients=[("fish", "pantry")])
    assert "Contains" not in client.get("/saved").text


# ---------- Deleting ----------


def test_delete_cleans_up(client, fake_llm):
    [sid] = cook_batch(client, fake_llm, recipe("Lemon Chicken"))
    client.post(f"/suggestions/{sid}/save", headers=HTMX)
    parent_id = get_suggestion(sid).saved_recipe_id
    child_id = add_saved("Tofu Lemon", parent=parent_id)
    fake_llm.reply([variation("Spin A")])
    spin_off(client, parent_id)

    response = client.post(f"/saved/{parent_id}/delete", follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/saved"
    assert get_saved(parent_id) is None
    assert get_saved(child_id).parent_recipe_id is None  # the saved spin-off stays
    assert spinoff_titles(parent_id) == []                # unsaved spin-offs go
    assert get_suggestion(sid).saved_recipe_id is None    # the Cook card can be saved again
    assert f'action="/suggestions/{sid}/save"' in client.get("/").text


def test_delete_twice_is_fine(client):
    saved_id = add_saved()
    client.post(f"/saved/{saved_id}/delete")
    assert client.post(f"/saved/{saved_id}/delete", follow_redirects=False).status_code == 303


# ---------- Spin-offs ----------


def test_spinoff_prompt_has_the_original_and_everyones_restrictions(client, fake_llm):
    saved_id = add_saved("Lemon Chicken", steps=["Season the chicken.", "Roast it."])
    fake_llm.reply([variation("A"), variation("B"), variation("C")])
    spin_off(client, saved_id)

    prompt = fake_llm.prompt()
    assert "Original: Lemon Chicken" in prompt
    assert "- 1 chicken thighs" in prompt
    assert "2. Roast it." in prompt
    assert "the protein, the cuisine, or the cooking method" in prompt
    assert "Who's eating: Alex and Jordan" in prompt
    assert "- shrimp (also: prawn" in prompt                        # Alex's never
    assert "- corn (also: popcorn) (fine: cornstarch" in prompt     # Jordan's never
    assert "- broccoli (also: broccolini)" in prompt
    assert "Avoid:\n- fish" in prompt
    assert "unless the request asks for variations" in fake_llm.calls[0]["system"]


def test_spinoff_shows_cards_and_logs_as_spinoff(client, fake_llm):
    saved_id = add_saved("Lemon Chicken")
    fake_llm.reply([variation("Tofu Lemon"), variation("Lemon Chicken Tacos", "Made it Mexican."),
                    variation("Grilled Lemon Chicken", "Grilled instead of roasted."),
                    variation("A Fourth One")])
    html = spin_off(client, saved_id).text

    assert spinoff_titles(saved_id) == ["Tofu Lemon", "Lemon Chicken Tacos", "Grilled Lemon Chicken"]
    assert "What changed:</strong> Made it Mexican." in html
    assert "A Fourth One" not in html
    [row] = log_rows()
    assert row.kind == "spinoff" and row.success
    # Still there after a reload.
    assert "Grilled instead of roasted." in client.get(f"/saved/{saved_id}").text


def test_saving_a_spinoff_records_its_parent(client, fake_llm):
    parent_id = add_saved("Lemon Chicken")
    fake_llm.reply([variation("Tofu Lemon")])
    spin_off(client, parent_id)
    with session() as s:
        sid = s.exec(select(Suggestion).where(Suggestion.parent_recipe_id == parent_id)).one().id
    client.post(f"/suggestions/{sid}/rate", data={"rating": "1"}, headers=HTMX)

    child = [r for r in saved_rows() if r.title == "Tofu Lemon"][0]
    assert child.parent_recipe_id == parent_id and child.rating == 1
    assert child.recipe_json["variation_note"] == "Swapped the chicken for tofu."
    parent_page = client.get(f"/saved/{parent_id}").text
    assert f'href="/saved/{child.id}">Tofu Lemon' in parent_page
    assert f'Spin-off of <a href="/saved/{parent_id}">Lemon Chicken' in client.get(f"/saved/{child.id}").text


def test_spinoff_card_plain_post_returns_to_parent(client, fake_llm):
    parent_id = add_saved()
    fake_llm.reply([variation("Tofu Lemon")])
    spin_off(client, parent_id)
    with session() as s:
        sid = s.exec(select(Suggestion).where(Suggestion.parent_recipe_id == parent_id)).one().id
    response = client.post(f"/suggestions/{sid}/save", follow_redirects=False)
    assert response.headers["location"] == f"/saved/{parent_id}"


# ---------- Spin-offs: the never filter (safety) ----------


def test_spinoff_violation_regenerates_once_then_drops(client, fake_llm):
    saved_id = add_saved("Garlic Noodles")
    still_bad = variation("Noodle Bowl", steps=["Boil noodles.", "Top with a few prawns."])
    fake_llm.reply([variation("A"), SHRIMP_RECIPE, variation("C")]).reply([still_bad])
    html = spin_off(client, saved_id).text

    assert len(fake_llm.calls) == 2  # regenerate ONCE, never more
    assert "restricted: shrimp" in fake_llm.prompt(1)
    assert REMOVED_RESTRICTED in html
    assert spinoff_titles(saved_id) == ["A", "C"]
    assert [r.kind for r in log_rows()] == ["spinoff", "spinoff"]


def test_spinoff_violation_is_replaced(client, fake_llm):
    saved_id = add_saved()
    corny = variation("Chicken Corn Chowder", ingredients=[("chicken", "pantry"), ("sweet corn", "buy")],
                      missing=["sweet corn"])
    fake_llm.reply([corny]).reply([variation("Chicken Soup")])
    html = spin_off(client, saved_id).text
    assert spinoff_titles(saved_id) == ["Chicken Soup"]
    assert "removed" not in html


@pytest.mark.parametrize("bad", [
    SHRIMP_RECIPE,                                                      # Alex's never
    recipe("Broccoli Stir-Fry"),                                        # Jordan's never
    recipe(steps=["Thicken with cornstarch.", "Add the corn kernels."]),  # Jordan's, past an exception
    recipe(ingredients=[("chicken thighs, or shrimp if you like", "pantry")]),  # hidden by a prep note
])
def test_spinoffs_always_use_everyones_never_rules(client, fake_llm, bad):
    saved_id = add_saved()
    fake_llm.reply([bad]).reply([bad])
    assert REMOVED_RESTRICTED in spin_off(client, saved_id).text
    assert spinoff_titles(saved_id) == []


def test_spinoff_enforces_never_added_in_settings(client, fake_llm):
    saved_id = add_saved()
    with session() as s:
        s.add(Restriction(person_id=1, term="mushroom", level="never", aliases=[], exceptions=[]))
        s.commit()
    mushroom = variation("Risotto", ingredients=[("cremini mushrooms", "pantry")])
    fake_llm.reply([mushroom]).reply([mushroom])
    assert REMOVED_RESTRICTED in spin_off(client, saved_id).text
    assert "- mushroom" in fake_llm.prompt(0)


def test_spinoff_ignores_an_inactive_rule_only_if_avoid(client, fake_llm):
    """Turning off an avoid rule leaves it out; never rules can't be switched off."""
    with session() as s:
        fish = s.exec(select(Restriction).where(Restriction.term == "fish")).one()
        fish.active = False
        s.add(fish)
        s.commit()
    saved_id = add_saved()
    fake_llm.reply([variation("A")])
    spin_off(client, saved_id)
    assert "Avoid:\n- (none)" in fake_llm.prompt()


# ---------- Spin-offs: batches, errors and limits ----------


def test_spinoff_and_cook_batches_are_separate(client, fake_llm):
    cook_batch(client, fake_llm, recipe("Cook One"), recipe("Cook Two"))
    first, second = add_saved("First"), add_saved("Second")

    fake_llm.reply([variation("First A")])
    spin_off(client, first)
    fake_llm.reply([variation("Second A")])
    spin_off(client, second)
    assert cook_titles() == ["Cook One", "Cook Two"]  # spin-offs don't touch Cook

    cook_batch(client, fake_llm, recipe("Cook Three"))
    assert spinoff_titles(first) == ["First A"]        # Cook doesn't touch spin-offs

    fake_llm.reply([variation("First B")])
    spin_off(client, first)
    assert spinoff_titles(first) == ["First B"]        # replaces only its own batch
    assert spinoff_titles(second) == ["Second A"]
    assert "First B" not in client.get("/").text


def test_spinoff_cost_guard(client, fake_llm, monkeypatch):
    monkeypatch.setattr(config, "MAX_GENERATIONS_PER_HOUR", 3)
    add_log_rows(3)
    saved_id = add_saved()
    response = spin_off(client, saved_id)
    assert response.status_code == 200
    assert "limit of 3 recipe requests an hour" in response.text
    assert fake_llm.calls == []


def test_spinoff_api_error_is_friendly_and_keeps_old_batch(client, fake_llm):
    saved_id = add_saved()
    fake_llm.reply([variation("Keep Me")])
    spin_off(client, saved_id)
    fake_llm.fail(connection_error())
    response = spin_off(client, saved_id)
    assert response.status_code == 200
    assert "reach the recipe service" in response.text and 'role="alert"' in response.text
    assert spinoff_titles(saved_id) == ["Keep Me"]
    with session() as s:
        assert s.exec(select(GenerationLog)).all()[-1].success is False


def test_spinoff_of_broken_saved_recipe_is_friendly(client, fake_llm):
    with session() as s:
        row = SavedRecipe(title="Old", recipe_json={"title": "Old"})
        s.add(row)
        s.commit()
        saved_id = row.id
    html = spin_off(client, saved_id).text
    assert "can&#39;t be spun off" in html
    assert fake_llm.calls == []


def test_spinoff_unknown_recipe_is_404(client, fake_llm):
    assert spin_off(client, 999).status_code == 404


def test_spinoff_plain_post_redirects(client, fake_llm):
    saved_id = add_saved()
    fake_llm.reply([variation("A")])
    response = client.post(f"/saved/{saved_id}/spinoff", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == f"/saved/{saved_id}"


def test_spinoff_needs_the_api_key(client):
    """No fake installed: the request fails before reaching get_client()."""
    saved_id = add_saved()
    assert "ANTHROPIC_API_KEY isn&#39;t set" in spin_off(client, saved_id).text
    assert log_rows() == []

