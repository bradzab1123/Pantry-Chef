"""Saved recipes, ratings and spin-offs (SPEC.md §2.3).

- Saving copies a recipe from the `suggestion` table by id. Recipe JSON is
  never taken from the form, so only recipes that passed the `never` filter
  can be saved.
- Rating a card that isn't saved yet saves it with that rating (ratings live
  on saved_recipe, and they steer future suggestions).
- A spin-off asks for 3 variations of a saved recipe. It always uses
  EVERYONE's restrictions (there's no "who's eating" here, and more
  restrictions is the safe direction), and goes through the same
  review / regenerate-once / drop pipeline as the Cook tab.
"""

from dataclasses import dataclass

import pydantic
from sqlmodel import Session, select

from app import cook, llm
from app.models import Person, SavedRecipe, Suggestion
from app.restrictions import check_recipe, never_restrictions_for
from app.schemas import Recipe

RATINGS = (1, -1)  # thumbs up, thumbs down (0 = no rating)
SPINOFF_COUNT = 3


class NotFound(Exception):
    """The recipe or suggestion doesn't exist (any more)."""


# ---------- Saving and rating ----------


def get_saved(session: Session, saved_id: int) -> SavedRecipe:
    recipe = session.get(SavedRecipe, saved_id)
    if recipe is None:
        raise NotFound(saved_id)
    return recipe


def get_suggestion(session: Session, suggestion_id: int) -> Suggestion:
    suggestion = session.get(Suggestion, suggestion_id)
    if suggestion is None:
        raise NotFound(suggestion_id)
    return suggestion


def save_suggestion(session: Session, suggestion: Suggestion) -> SavedRecipe:
    """Save a card (once: saving it again returns the same saved recipe)."""
    if suggestion.saved_recipe_id is not None:
        existing = session.get(SavedRecipe, suggestion.saved_recipe_id)
        if existing is not None:
            return existing

    recipe = SavedRecipe(
        title=suggestion.recipe_json.get("title") or "Untitled recipe",
        recipe_json=suggestion.recipe_json,
        parent_recipe_id=suggestion.parent_recipe_id,
    )
    session.add(recipe)
    session.flush()  # gives `recipe` its id
    suggestion.saved_recipe_id = recipe.id
    session.add(suggestion)
    session.commit()
    session.refresh(recipe)
    return recipe


def rate(session: Session, recipe: SavedRecipe, rating: int) -> SavedRecipe:
    """Set 👍 (1) or 👎 (-1). Tapping the rating it already has clears it."""
    if rating not in RATINGS:
        raise ValueError(f"rating must be one of {RATINGS}")
    recipe.rating = 0 if recipe.rating == rating else rating
    session.add(recipe)
    session.commit()
    session.refresh(recipe)
    return recipe


def delete_saved(session: Session, recipe: SavedRecipe) -> None:
    # The database does the tidying up (see migrations 0001 and 0005):
    # its spin-offs' parent_recipe_id is cleared, its unsaved spin-off cards
    # are deleted, and any Cook card it came from offers Save again.
    session.delete(recipe)
    session.commit()


def saved_for(session: Session, suggestions: list[Suggestion]) -> dict[int, SavedRecipe]:
    """{saved_recipe_id: SavedRecipe} for the cards that have been saved,
    so the card templates can show "Saved" and the current rating."""
    ids = [s.saved_recipe_id for s in suggestions if s.saved_recipe_id is not None]
    if not ids:
        return {}
    rows = session.exec(select(SavedRecipe).where(SavedRecipe.id.in_(ids)))
    return {r.id: r for r in rows}


# ---------- The Saved tab ----------


def list_saved(session: Session, q: str = "", favorites: bool = False) -> list[SavedRecipe]:
    """Saved recipes, newest first. `q` searches titles (case-insensitive)."""
    query = select(SavedRecipe)
    q = (q or "").strip()
    if q:
        # autoescape: a "%" or "_" typed in the search box is just a character.
        # SQLite's LIKE ignores case for plain A-Z letters.
        query = query.where(SavedRecipe.title.contains(q, autoescape=True))
    if favorites:
        query = query.where(SavedRecipe.rating == 1)
    return list(session.exec(query.order_by(SavedRecipe.created_at.desc(), SavedRecipe.id.desc())))


def spinoffs_of(session: Session, recipe: SavedRecipe) -> list[SavedRecipe]:
    """Saved recipes that were spun off this one."""
    query = (select(SavedRecipe)
             .where(SavedRecipe.parent_recipe_id == recipe.id)
             .order_by(SavedRecipe.created_at, SavedRecipe.id))
    return list(session.exec(query))


@dataclass
class HeadsUp:
    term: str            # the restriction, e.g. "pea"
    person: str          # whose restriction it is
    matched: list[str]   # what was found, e.g. ["pea", "peas"]


def warnings(session: Session, recipes: list[SavedRecipe]) -> dict[int, list[HeadsUp]]:
    """`never` items in saved recipes, checked against TODAY's rules for everyone.

    Saved recipes passed the filter when they were generated, but a rule
    added later (or a level raised to never) can catch an old recipe. This
    is display only: a heads-up on the Saved tab (and for planned recipes on
    the List tab: `recipes` can be anything with .id and .recipe_json).
    Returns {id: one HeadsUp per restriction hit}, with only the recipes
    that have a hit.
    """
    people = {p.id: p.name for p in session.exec(select(Person))}
    rules = never_restrictions_for(session, people.keys())
    result = {}
    for recipe in recipes:
        by_rule: dict[int | None, HeadsUp] = {}
        for v in check_recipe(recipe.recipe_json, rules):
            heads_up = by_rule.setdefault(
                v.restriction_id, HeadsUp(v.term, people.get(v.person_id, "someone"), []))
            if v.matched not in heads_up.matched:
                heads_up.matched.append(v.matched)
        if by_rule:
            result[recipe.id] = list(by_rule.values())
    return result


# ---------- Spin-offs ----------


def spin_off(session: Session, recipe: SavedRecipe) -> cook.GenerateResult:
    """Ask for variations of a saved recipe, filter them, and store them as
    that recipe's spin-off batch. Raises llm.GenerationError."""
    people = cook.all_people(session)
    # Loaded from the database, never from the form, like the Cook tab.
    never = never_restrictions_for(session, [p.id for p in people])
    avoid = cook.active_restrictions(session, people, "avoid")
    available = cook.available_items(session)

    # Re-validate the stored JSON, so the prompt always gets a proper recipe.
    try:
        original = Recipe.model_validate(recipe.recipe_json)
    except pydantic.ValidationError as error:
        raise llm.GenerationError("This saved recipe is missing details, so it can't be spun off.") from error
    prompt = llm.render_prompt(
        "spinoff.txt",
        count=SPINOFF_COUNT,
        original=original,
        eaters=[p.name for p in people],
        **cook.household_context(session, never, avoid),
    )

    llm.check_cost_guard(session)
    first = llm.request_recipes(session, "spinoff", prompt).recipes[:SPINOFF_COUNT]
    kept, notes = cook.review_batch(session, "spinoff", prompt, first, never, available)
    return cook.GenerateResult(cook.save_batch(session, kept, parent_recipe_id=recipe.id), notes)
