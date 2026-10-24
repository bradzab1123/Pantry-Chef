"""Phase 4 tests: recipe generation (app/cook.py, app/llm.py, the Cook page).

The Anthropic client is always the FakeLLM from conftest.py; the autouse
no_real_anthropic fixture makes any un-faked call fail loudly.
"""

from datetime import timedelta

import anthropic
import httpx2
import pytest
from sqlmodel import Session, select

from app import config, cook, db, llm
from app.models import GenerationLog, PantryItem, Restriction, Staple, Suggestion, utcnow
from app.schemas import Recipe, strip_step_number

HTMX = {"HX-Request": "true"}
REMOVED_RESTRICTED = "1 recipe removed for containing a restricted ingredient."


# ---------- Helpers ----------


def recipe(title="Lemon Chicken", ingredients=None, steps=None, missing=None, **fields):
    """A valid recipe dict (SPEC §3). Ingredients are (name, source) pairs."""
    ingredients = ingredients or [("chicken thighs", "pantry"), ("salt", "staple")]
    data = {
        "title": title,
        "summary": "A quick weeknight dish.",
        "cuisine": None,
        "servings": 2,
        "total_time_minutes": 30,
        "difficulty": "easy",
        "ingredients": [{"name": n, "quantity": "1", "unit": None, "source": s} for n, s in ingredients],
        "steps": steps or ["Season the chicken.", "Cook until done."],
        "missing_ingredients": missing if missing is not None else [],
        "diet_notes": "Fits everyone's restrictions.",
        "variation_note": None,
    }
    data.update(fields)
    return data


SHRIMP_RECIPE = recipe("Garlic Noodles", ingredients=[("noodles", "pantry"), ("shrimp", "buy")],
                       missing=["shrimp"])


def generate(client, **fields):
    data = {"eaters": "both", "avoid": [], "skip": "", "meal_type": "any", "max_time": "any",
            "servings": "2", "cuisine": "", "count": "4"}
    data.update(fields)
    return client.post("/cook/generate", data=data, headers=HTMX)


def session():
    return Session(db.get_engine())


def log_rows():
    with session() as s:
        return s.exec(select(GenerationLog).order_by(GenerationLog.id)).all()


def suggestion_titles():
    with session() as s:
        return [row.recipe_json["title"] for row in s.exec(select(Suggestion).order_by(Suggestion.id))]


def restriction_id(term):
    with session() as s:
        return s.exec(select(Restriction).where(Restriction.term == term)).one().id


def add_pantry(name, status="have"):
    from app.pantry import add_items
    with session() as s:
        add_items(s, name, status)


def add_log_rows(n, age=timedelta(0)):
    with session() as s:
        for _ in range(n):
            s.add(GenerationLog(kind="generate", model="m", success=True, created_at=utcnow() - age))
        s.commit()


def connection_error():
    return anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))


# ---------- Happy path ----------


def test_generate_shows_cards_and_logs_usage(client, fake_llm):
    fake_llm.reply([recipe("Lemon Chicken"), recipe("Fried Rice")], input_tokens=1200, output_tokens=3400)
    html = generate(client).text

    assert "Lemon Chicken" in html and "Fried Rice" in html
    assert "Nothing to buy" in html
    assert suggestion_titles() == ["Lemon Chicken", "Fried Rice"]
    [row] = log_rows()
    assert (row.kind, row.success, row.input_tokens, row.output_tokens) == ("generate", True, 1200, 3400)
    assert row.model == config.CLAUDE_MODEL


def test_request_uses_structured_output(client, fake_llm):
    fake_llm.reply([recipe()])
    generate(client)
    call = fake_llm.calls[0]
    assert call["model"] == config.CLAUDE_MODEL
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["format"]["schema"] == llm.RESPONSE_SCHEMA
    assert "Never mention" in call["system"]
    # Haiku 5.5 rejects sampling parameters; newer models reject forced tool use.
    assert "temperature" not in call and "tool_choice" not in call


@pytest.mark.parametrize("step,expected", [
    ("1. Preheat the oven.", "Preheat the oven."),
    ("  2) Chop the onion.", "Chop the onion."),
    ("Step 3: Simmer.", "Simmer."),
    ("4 - Serve.", "Serve."),
    ("1.5 cups of stock go in next.", "1.5 cups of stock go in next."),  # a quantity, not a number
    ("2 eggs, beaten, go in last.", "2 eggs, beaten, go in last."),
    ("Bake for 20 minutes.", "Bake for 20 minutes."),
])
def test_strip_step_number(step, expected):
    assert strip_step_number(step) == expected


def test_numbered_steps_are_shown_once(client, fake_llm):
    """The model sometimes numbers its steps; the page's list numbers them too."""
    fake_llm.reply([recipe(steps=["1. Preheat the oven.", "2. Roast the chicken."])])
    html = generate(client).text
    assert "<li>Preheat the oven.</li>" in html
    with session() as s:
        [row] = s.exec(select(Suggestion)).all()
    assert row.recipe_json["steps"] == ["Preheat the oven.", "Roast the chicken."]


def test_need_to_buy_shown(client, fake_llm):
    fake_llm.reply([recipe(ingredients=[("chicken", "pantry"), ("basil", "buy")], missing=["basil"])])
    html = generate(client).text
    assert "Need to buy:</strong> basil" in html


def test_count_limits_number_of_recipes(client, fake_llm):
    fake_llm.reply([recipe("A"), recipe("B"), recipe("C")])
    generate(client, count="2")
    assert suggestion_titles() == ["A", "B"]


def test_new_batch_replaces_old(client, fake_llm):
    fake_llm.reply([recipe("Old One")]).reply([recipe("New One")])
    generate(client)
    generate(client)
    assert suggestion_titles() == ["New One"]


def test_cook_page_shows_latest_batch(client, fake_llm):
    fake_llm.reply([recipe("Lemon Chicken")])
    generate(client)
    assert "Lemon Chicken" in client.get("/").text


def test_plain_form_post_redirects(client, fake_llm):
    fake_llm.reply([recipe()])
    response = client.post("/cook/generate", data={"eaters": "both"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


# ---------- What goes into the prompt ----------


def test_prompt_includes_pantry_staples_and_restrictions(client, fake_llm):
    add_pantry("chicken thighs")
    add_pantry("lemons", "buying")
    with session() as s:
        sugar = s.exec(select(Staple).where(Staple.name == "sugar")).one()
        sugar.enabled = False
        s.add(sugar)
        s.commit()
    fake_llm.reply([recipe()])
    generate(client, avoid=[str(restriction_id("fish"))], skip="no pasta", cuisine="Thai")

    prompt = fake_llm.prompt()
    assert "- chicken thighs" in prompt and "- lemons" in prompt  # Have and Buying
    assert "- salt" in prompt and "- sugar" not in prompt          # only enabled staples
    assert "- shrimp (also: prawn, prawns, shrimp paste" in prompt
    assert "- corn (also: popcorn) (fine: cornstarch, corn starch)" in prompt
    assert "- broccoli (also: broccolini)" in prompt
    assert "Avoid:\n- fish" in prompt
    assert "Skip tonight: no pasta" in prompt
    assert "Cuisine: Thai" in prompt
    assert "Who's eating: Alex and Jordan" in prompt


def test_never_restrictions_sent_even_when_form_has_none(client, fake_llm):
    """The form only carries avoid toggles; never items always come from the DB."""
    fake_llm.reply([recipe()])
    client.post("/cook/generate", data={}, headers=HTMX)
    prompt = fake_llm.prompt()
    for term in ("shrimp", "broccoli", "corn"):
        assert f"- {term}" in prompt


def test_avoid_toggled_off_is_not_sent(client, fake_llm):
    fake_llm.reply([recipe()])
    generate(client, avoid=[])
    assert "Avoid:\n- (none)" in fake_llm.prompt()


def test_partner_only_leaves_out_alexs_restrictions(client, fake_llm):
    fake_llm.reply([recipe()])
    generate(client, eaters="2", avoid=[str(restriction_id("fish"))])
    prompt = fake_llm.prompt()
    assert "Who's eating: Jordan" in prompt
    assert "shrimp" not in prompt and "fish" not in prompt  # Alex's, and they aren't eating
    assert "- broccoli" in prompt and "- corn" in prompt


def test_unknown_eater_means_everyone(client, fake_llm):
    fake_llm.reply([recipe()])
    generate(client, eaters="999")
    assert "- shrimp" in fake_llm.prompt() and "- corn" in fake_llm.prompt()


# ---------- The never filter, wired in ----------


def test_violation_regenerates_once_and_shows_replacement(client, fake_llm):
    fake_llm.reply([recipe("Lemon Chicken"), SHRIMP_RECIPE]).reply([recipe("Chicken Fried Rice")])
    html = generate(client).text

    assert len(fake_llm.calls) == 2
    fix_prompt = fake_llm.prompt(1)
    assert '"Garlic Noodles"' in fix_prompt
    assert "restricted: shrimp" in fix_prompt
    assert "Suggest exactly 1 new recipe" in fix_prompt
    assert "Lemon Chicken" in fix_prompt  # kept, so the replacement should differ
    assert suggestion_titles() == ["Lemon Chicken", "Chicken Fried Rice"]
    assert "Garlic Noodles" not in html
    assert "removed" not in html


def test_violation_twice_drops_recipe_with_note(client, fake_llm):
    still_bad = recipe("Noodle Bowl", steps=["Boil noodles.", "Top with a few prawns."])
    fake_llm.reply([recipe("Lemon Chicken"), SHRIMP_RECIPE]).reply([still_bad])
    html = generate(client).text

    assert len(fake_llm.calls) == 2  # regenerate ONCE, never more
    assert REMOVED_RESTRICTED in html
    assert suggestion_titles() == ["Lemon Chicken"]


@pytest.mark.parametrize("bad", [
    recipe("Shrimp Scampi"),                                                 # title
    recipe(summary="Like pad thai, but with dried shrimp."),                 # summary
    recipe(ingredients=[("chicken", "pantry"), ("frozen sweet corn", "buy")]),  # ingredient
    recipe(missing=["broccoli"]),                                            # missing list only
    recipe(steps=["Roast the broccolini.", "Serve."]),                       # step text
    recipe(steps=["Toss with a spoonful of XO sauce."]),                     # alias in a step
    recipe(steps=["Top with popcorn."]),                                     # popcorn alias
])
def test_violations_anywhere_are_caught(client, fake_llm, bad):
    fake_llm.reply([bad]).reply([bad])
    html = generate(client).text
    assert REMOVED_RESTRICTED in html
    assert suggestion_titles() == []


def test_exception_is_not_flagged(client, fake_llm):
    fake_llm.reply([recipe(ingredients=[("chicken", "pantry"), ("cornstarch", "pantry")],
                           steps=["Toss the chicken in corn starch."])])
    generate(client)
    assert len(fake_llm.calls) == 1
    assert suggestion_titles() == ["Lemon Chicken"]


def test_avoid_items_are_not_filtered(client, fake_llm):
    """avoid is prompt-only (SPEC §1); the code filter is for never items."""
    fake_llm.reply([recipe("Fish Tacos", ingredients=[("fish", "pantry")])])
    generate(client)
    assert suggestion_titles() == ["Fish Tacos"]


def test_filter_uses_only_the_people_eating(client, fake_llm):
    fake_llm.reply([SHRIMP_RECIPE])
    generate(client, eaters="2")  # Jordan only; shrimp is Alex's restriction
    assert suggestion_titles() == ["Garlic Noodles"]


def test_never_added_in_settings_is_enforced(client, fake_llm):
    with session() as s:
        s.add(Restriction(person_id=1, term="mushroom", level="never", aliases=[], exceptions=[]))
        s.commit()
    mushroom = recipe("Risotto", ingredients=[("cremini mushrooms", "pantry")])
    fake_llm.reply([mushroom]).reply([mushroom])
    assert REMOVED_RESTRICTED in generate(client).text


def test_fix_call_error_keeps_the_good_recipes(client, fake_llm):
    fake_llm.reply([recipe("Lemon Chicken"), SHRIMP_RECIPE]).fail(connection_error())
    html = generate(client).text
    assert suggestion_titles() == ["Lemon Chicken"]
    assert REMOVED_RESTRICTED in html


def test_replacement_short_still_drops(client, fake_llm):
    """Two bad recipes, only one replacement returned: the other is removed."""
    fake_llm.reply([SHRIMP_RECIPE, recipe("Corn Chowder")]).reply([recipe("Chicken Soup")])
    html = generate(client).text
    assert suggestion_titles() == ["Chicken Soup"]
    assert REMOVED_RESTRICTED in html


def test_extra_replacements_are_ignored(client, fake_llm):
    fake_llm.reply([SHRIMP_RECIPE]).reply([recipe("A"), recipe("B")])
    generate(client)
    assert suggestion_titles() == ["A"]


# ---------- Need-to-buy list ----------


def test_buy_list_drops_pantry_and_staples(client, fake_llm):
    add_pantry("lemons", "buying")
    fake_llm.reply([recipe(
        ingredients=[("chicken", "pantry"), ("lemon", "buy"), ("garlic", "buy"), ("basil", "buy")],
        missing=["lemon", "Basil"],
    )])
    generate(client)
    with session() as s:
        saved = s.exec(select(Suggestion)).one().recipe_json
    assert saved["missing_ingredients"] == ["basil"]
    sources = {i["name"]: i["source"] for i in saved["ingredients"]}
    assert sources == {"chicken": "pantry", "lemon": "pantry", "garlic": "staple", "basil": "buy"}


@pytest.mark.parametrize("name,plain", [
    ("potatoes, cut into 1-inch pieces", "potatoes"),
    ("Chicken Thighs (bone-in)", "chicken thighs"),
    ("bone-in pork chops, about 1 inch thick", "bone-in pork chops"),
    ("soy sauce", "soy sauce"),
    ("(optional) parsley", "(optional) parsley"),  # nothing before the bracket: keep it all
])
def test_plain_name(name, plain):
    assert cook.plain_name(name) == plain


def test_prep_notes_are_cut_from_buy_list_and_matched_to_pantry(client, fake_llm):
    """Real reply from the first live test: prep notes in the names."""
    add_pantry("chicken thighs")
    fake_llm.reply([recipe(
        "Garlic Butter Chicken Thighs",
        ingredients=[("chicken thighs, bone-in or boneless", "buy"),
                     ("potatoes, cut into 1-inch pieces", "buy"), ("olive oil", "staple")],
        missing=["chicken thighs, bone-in or boneless", "potatoes, cut into 1-inch pieces"],
    )])
    html = generate(client).text
    with session() as s:
        saved = s.exec(select(Suggestion)).one().recipe_json
    assert saved["missing_ingredients"] == ["potatoes"]
    assert "Need to buy:</strong> potatoes</p>" in html
    # The ingredient list keeps the full name, re-labelled as on hand.
    chicken = saved["ingredients"][0]
    assert (chicken["name"], chicken["source"]) == ("chicken thighs, bone-in or boneless", "pantry")


def test_prep_note_variants_count_once(client, fake_llm):
    fake_llm.reply([recipe(
        ingredients=[("chicken", "pantry"), ("potatoes, diced", "buy"), ("potatoes (for garnish)", "buy"),
                     ("lime", "buy")],
        missing=["potatoes", "lime"],
    )])
    generate(client)
    assert len(fake_llm.calls) == 1  # 2 distinct buys, so no regenerate
    with session() as s:
        assert s.exec(select(Suggestion)).one().recipe_json["missing_ingredients"] == ["potatoes", "lime"]


def test_cutting_prep_notes_cannot_hide_a_never_item(client, fake_llm):
    """The filter reads the full name, so text after the comma still counts."""
    sneaky = recipe(ingredients=[("chicken thighs, or shrimp if you like", "pantry")])
    fake_llm.reply([sneaky]).reply([sneaky])
    assert REMOVED_RESTRICTED in generate(client).text


def test_system_prompt_asks_for_plain_names_and_requested_servings():
    system = llm.system_prompt()
    assert 'not "potatoes, cut into 1-inch pieces"' in system
    assert "requested number of servings" in system
    assert "two-person" not in system


def test_system_prompt_asks_for_pantry_names_as_listed():
    # Lets "Cooked it" match ingredients to pantry items exactly.
    assert "exactly as it appears on the pantry list" in llm.system_prompt()


def test_too_many_buys_regenerates(client, fake_llm):
    greedy = recipe("Feast", ingredients=[("a1", "buy"), ("b2", "buy"), ("c3", "buy")])
    fake_llm.reply([greedy]).reply([recipe("Simple")])
    generate(client)
    assert "Needs 3 ingredients bought" in fake_llm.prompt(1)
    assert suggestion_titles() == ["Simple"]


def test_too_many_buys_twice_drops_with_note(client, fake_llm):
    greedy = recipe("Feast", ingredients=[("a1", "buy"), ("b2", "buy"), ("c3", "buy")])
    fake_llm.reply([greedy]).reply([greedy])
    html = generate(client).text
    assert "1 recipe removed for needing more than 2 extra ingredients." in html
    assert suggestion_titles() == []


# ---------- Validation retries and errors ----------


def test_invalid_reply_retries_once(client, fake_llm):
    fake_llm.reply(text="not json at all").reply([recipe()])
    generate(client)
    assert suggestion_titles() == ["Lemon Chicken"]
    assert [r.success for r in log_rows()] == [False, True]


def test_invalid_twice_shows_friendly_error(client, fake_llm):
    fake_llm.reply([recipe("Keep Me")])
    generate(client)
    fake_llm.reply(text='{"recipes": [{"title": "x"}]}').reply(text="{}")
    html = generate(client).text

    assert "unusable twice" in html
    assert 'role="alert"' in html
    assert len(fake_llm.calls) == 3  # one good batch, then exactly one retry
    assert [r.success for r in log_rows()] == [True, False, False]
    assert suggestion_titles() == ["Keep Me"]  # the previous batch is untouched


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_refusal_or_cutoff_counts_as_unusable(client, fake_llm, stop_reason):
    fake_llm.reply([recipe()], stop_reason=stop_reason).reply([recipe()], stop_reason=stop_reason)
    assert "unusable twice" in generate(client).text


def test_api_error_is_friendly_and_logged(client, fake_llm):
    fake_llm.fail(connection_error())
    html = generate(client).text
    assert "reach the recipe service" in html
    [row] = log_rows()
    assert row.success is False and row.input_tokens == 0


def test_missing_api_key_makes_no_call(client):
    """No fake installed: if get_client() were called, the guard would raise."""
    html = generate(client).text
    assert "ANTHROPIC_API_KEY isn&#39;t set" in html
    assert log_rows() == []


def test_error_without_htmx_renders_page(client):
    response = client.post("/cook/generate", data={"eaters": "both"}, follow_redirects=False)
    assert response.status_code == 200
    assert "ANTHROPIC_API_KEY isn&#39;t set" in response.text
    assert 'class="tabbar"' in response.text


# ---------- Cost guard ----------


def test_cost_guard_refuses_at_the_limit(client, fake_llm, monkeypatch):
    monkeypatch.setattr(config, "MAX_GENERATIONS_PER_HOUR", 5)
    add_log_rows(5)
    html = generate(client).text
    assert "limit of 5 recipe requests an hour" in html
    assert fake_llm.calls == []


def test_cost_guard_allows_below_limit_and_ignores_old_calls(client, fake_llm, monkeypatch):
    monkeypatch.setattr(config, "MAX_GENERATIONS_PER_HOUR", 5)
    add_log_rows(4)
    add_log_rows(20, age=timedelta(hours=2))
    fake_llm.reply([recipe()])
    generate(client)
    assert len(fake_llm.calls) == 1


# ---------- Chips and form ----------


def test_chips_follow_who_is_eating(client):
    partner = client.get("/cook/chips?eaters=2").text
    assert "broccoli" in partner and "corn" in partner
    assert "shrimp" not in partner and "fish" not in partner

    both = client.get("/cook/chips?eaters=both").text
    assert "shrimp" in both
    # fish is an avoid item: a checkbox, on by default. Never items have no input.
    assert f'name="avoid" value="{restriction_id("fish")}"' in both and "checked" in both
    assert f'value="{restriction_id("shrimp")}"' not in both


def test_cook_page_renders_form(client):
    html = client.get("/").text
    assert 'hx-post="/cook/generate"' in html
    assert 'value="both"' in html and "Alex" in html and "Jordan" in html
    assert "shrimp" in html  # Both is the default, so all chips show


def test_turned_off_avoid_is_not_a_chip(client):
    with session() as s:
        fish = s.get(Restriction, restriction_id("fish"))
        fish.active = False
        s.add(fish)
        s.commit()
    assert "fish" not in client.get("/cook/chips?eaters=both").text


def test_parse_form_clamps_and_defaults():
    req = cook.parse_form(eaters="", avoid=["3", "x"], skip="  a  " + "b" * 400, meal_type="brunch",
                          max_time="90", servings="99", cuisine="", count="0")
    assert req.eaters == "both"
    assert req.avoid_on == [3]
    assert len(req.skip) == cook.MAX_SKIP_LENGTH and req.skip.startswith("a b")
    assert (req.meal_type, req.max_time) == ("any", "any")
    assert (req.servings, req.count) == (cook.MAX_SERVINGS, 1)
    assert cook.parse_form(servings="abc").servings == cook.DEFAULT_SERVINGS


# ---------- review() directly ----------


def test_review_checks_the_recipe_before_tidying(household):
    """Even if the only mention of a never item is in a "buy" entry that
    tidying would remove (because it's in the pantry), it's still caught."""
    add_pantry("corn")
    with session() as s:
        never = cook.never_restrictions_for(s, [1, 2])
        available = cook.available_items(s)
    r = Recipe.model_validate(recipe(missing=["corn"]))
    tidied, problem = cook.review(r, never, available)
    assert tidied.missing_ingredients == []
    assert problem is not None and problem.kind == cook.RESTRICTED


# ---------- Usage (Settings) ----------


def test_usage_this_month(client, monkeypatch):
    monkeypatch.setattr(config, "PRICE_INPUT_PER_MTOK", 0.10)
    monkeypatch.setattr(config, "PRICE_OUTPUT_PER_MTOK", 0.50)
    with session() as s:
        s.add(GenerationLog(kind="generate", model="m", success=True,
                            input_tokens=1_000_000, output_tokens=2_000_000))
        s.add(GenerationLog(kind="generate", model="m", success=False))
        s.add(GenerationLog(kind="generate", model="m", success=True, input_tokens=5_000_000,
                            created_at=utcnow() - timedelta(days=40)))  # last month
        s.commit()
        usage = llm.usage_this_month(s)
    assert (usage.calls, usage.failed) == (2, 1)
    assert (usage.input_tokens, usage.output_tokens) == (1_000_000, 2_000_000)
    assert usage.cost == pytest.approx(1.10)

    html = client.get("/settings").text
    assert "1,000,000" in html and "2,000,000" in html
    assert "$1.10" in html
    assert "(1 failed)" in html
