"""Recipe generation for the Cook tab (SPEC.md §2.2).

generate() does the whole job:
1. Work out who's eating and load their `never` restrictions from the
   database. These never come from the form, so they can't be switched off.
2. Build the prompt and call the LLM (app/llm.py).
3. Check every recipe with review(): the `never` filter, plus the
   "at most 2 things to buy" rule.
4. If any recipe fails, ask ONCE for replacements (naming what was wrong),
   and check the replacements with the same review(). Anything that still
   fails is dropped, with a note saying so.
5. Store what passed as the new batch of suggestions.
"""

from dataclasses import dataclass, field

from sqlmodel import Session, delete, select

from app import llm
from app.models import PantryItem, Person, Restriction, SavedRecipe, Staple, Suggestion
from app.pantry import normalize_name, plain_name
from app.restrictions import check_recipe, never_restrictions_for, violation_message
from app.schemas import MAX_MISSING, Recipe

MEAL_TYPES = ("any", "breakfast", "lunch", "dinner", "snack")
MAX_TIMES = ("any", "30", "45", "60")  # minutes
DEFAULT_SERVINGS, MAX_SERVINGS = 2, 12
DEFAULT_COUNT, MAX_COUNT = 4, 6
MAX_SKIP_LENGTH = 300
MAX_CUISINE_LENGTH = 60
RECENT_RATINGS = 15  # liked/disliked titles sent to the model


# ---------- The form ----------


@dataclass
class CookRequest:
    eaters: str = "both"  # "both", or one person's id as text
    # Ids of the `avoid` restrictions left ON for this request. None means
    # "all of them" (used before the form has been submitted).
    avoid_on: list[int] | None = None
    skip: str = ""
    meal_type: str = "any"
    max_time: str = "any"
    servings: int = DEFAULT_SERVINGS
    cuisine: str = ""
    count: int = DEFAULT_COUNT


def _int_in_range(text: str, default: int, low: int, high: int) -> int:
    try:
        value = int(text)
    except (TypeError, ValueError):
        return default
    return min(max(value, low), high)


def _one_line(text: str, limit: int) -> str:
    return " ".join((text or "").split())[:limit]


def parse_form(eaters="both", avoid=(), skip="", meal_type="any", max_time="any",
               servings="", cuisine="", count="") -> CookRequest:
    """Form fields -> CookRequest. Odd values fall back to the defaults."""
    avoid_on = []
    for value in avoid:
        try:
            avoid_on.append(int(value))
        except (TypeError, ValueError):
            pass
    return CookRequest(
        eaters=eaters or "both",
        avoid_on=avoid_on,
        skip=_one_line(skip, MAX_SKIP_LENGTH),
        meal_type=meal_type if meal_type in MEAL_TYPES else "any",
        max_time=max_time if max_time in MAX_TIMES else "any",
        servings=_int_in_range(servings, DEFAULT_SERVINGS, 1, MAX_SERVINGS),
        cuisine=_one_line(cuisine, MAX_CUISINE_LENGTH),
        count=_int_in_range(count, DEFAULT_COUNT, 1, MAX_COUNT),
    )


# ---------- Who's eating and their restrictions ----------


def all_people(session: Session) -> list[Person]:
    return list(session.exec(select(Person).order_by(Person.id)))


def eating(session: Session, eaters: str) -> list[Person]:
    """The people eating. Anything that isn't a known person's id means
    everyone, which is the safe direction (more restrictions, not fewer)."""
    people = all_people(session)
    chosen = [p for p in people if str(p.id) == eaters]
    return chosen or people


def active_restrictions(session: Session, people: list[Person], level: str) -> list[Restriction]:
    ids = [p.id for p in people]
    rows = session.exec(
        select(Restriction)
        .where(Restriction.person_id.in_(ids))
        .where(Restriction.level == level)
        .where(Restriction.active == True)  # noqa: E712
        .order_by(Restriction.term)
    )
    return list(rows)


def chips(session: Session, people: list[Person]) -> dict:
    """The restriction chips on the Cook form: never (locked) and avoid (toggleable)."""
    return {
        "never_chips": never_restrictions_for(session, [p.id for p in people]),
        "avoid_chips": active_restrictions(session, people, "avoid"),
    }


# ---------- Prompt ----------


def available_items(session: Session) -> dict[str, str]:
    """normalize_name -> "pantry" or "staple", for everything we count as on hand."""
    available = {}
    for staple in session.exec(select(Staple).where(Staple.enabled == True)):  # noqa: E712
        available[normalize_name(staple.name)] = "staple"
    for item in session.exec(select(PantryItem)):
        available[item.normalized_name] = "pantry"
    return available


def recent_ratings(session: Session, rating: int) -> list[str]:
    rows = session.exec(
        select(SavedRecipe.title)
        .where(SavedRecipe.rating == rating)
        .order_by(SavedRecipe.created_at.desc())
        .limit(RECENT_RATINGS)
    )
    return list(rows)


def household_context(session: Session, never: list[Restriction],
                      avoid: list[Restriction]) -> dict:
    """What _household.txt needs: what's on hand, plus the restrictions."""
    pantry = session.exec(select(PantryItem).order_by(PantryItem.name)).all()
    staples = session.exec(
        select(Staple).where(Staple.enabled == True).order_by(Staple.name)  # noqa: E712
    ).all()
    return {
        "pantry": [i.name for i in pantry],
        "staples": [s.name for s in staples],
        "never": never,
        "avoid": [r.term for r in avoid],
        "liked": recent_ratings(session, 1),
        "disliked": recent_ratings(session, -1),
    }


def prompt_context(session: Session, req: CookRequest, people: list[Person],
                   never: list[Restriction]) -> dict:
    """Everything generate.txt needs."""
    avoid = active_restrictions(session, people, "avoid")
    if req.avoid_on is not None:
        avoid = [r for r in avoid if r.id in req.avoid_on]
    return {
        "count": req.count,
        "eaters": [p.name for p in people],
        "skip": req.skip,
        "meal_type": req.meal_type,
        "max_time": "any" if req.max_time == "any" else f"{req.max_time} minutes",
        "servings": req.servings,
        "cuisine": req.cuisine,
        **household_context(session, never, avoid),
    }


# ---------- Checking one recipe ----------

RESTRICTED = "restricted"
TOO_MANY_BUYS = "too_many_buys"


@dataclass
class Problem:
    kind: str     # RESTRICTED or TOO_MANY_BUYS
    message: str  # told to the model when asking for a replacement


def tidy_buys(recipe: Recipe, available: dict[str, str]) -> Recipe:
    """Fix up what the recipe says we need to buy.

    "Need to buy" = missing_ingredients plus any ingredient marked "buy",
    as plain names, de-duplicated, minus anything that's actually in the
    pantry or staples (those ingredients get re-labelled). Returns a
    corrected copy; the ingredient list itself keeps its full names.
    """
    ingredients = []
    for ing in recipe.ingredients:
        on_hand = available.get(normalize_name(plain_name(ing.name)))
        if ing.source == "buy" and on_hand:
            ing = ing.model_copy(update={"source": on_hand})
        ingredients.append(ing)

    names = list(recipe.missing_ingredients) + [i.name for i in recipe.ingredients if i.source == "buy"]
    buys, seen = [], set()
    for name in names:
        plain = plain_name(name)
        key = normalize_name(plain)
        if key and key not in seen and key not in available:
            seen.add(key)
            buys.append(plain)
    return recipe.model_copy(update={"ingredients": ingredients, "missing_ingredients": buys})


def review(recipe: Recipe, never: list[Restriction],
           available: dict[str, str]) -> tuple[Recipe, Problem | None]:
    """Check one recipe. Returns (tidied recipe, problem or None)."""
    # Safety: run the never filter on the recipe exactly as the model returned
    # it, BEFORE tidying, so tidying can never hide text from the filter.
    violations = check_recipe(recipe.model_dump(), never)
    tidied = tidy_buys(recipe, available)
    buys = tidied.missing_ingredients

    if violations:
        return tidied, Problem(RESTRICTED, violation_message(violations))
    if len(buys) > MAX_MISSING:
        return tidied, Problem(
            TOO_MANY_BUYS,
            f"Needs {len(buys)} ingredients bought ({', '.join(buys)}); the limit is {MAX_MISSING}.",
        )
    return tidied, None


def removal_notes(kinds: list[str]) -> list[str]:
    notes = []
    restricted = kinds.count(RESTRICTED)
    if restricted:
        notes.append("1 recipe removed for containing a restricted ingredient." if restricted == 1
                     else f"{restricted} recipes removed for containing restricted ingredients.")
    too_many = kinds.count(TOO_MANY_BUYS)
    if too_many:
        notes.append(
            f"{'1 recipe' if too_many == 1 else f'{too_many} recipes'} removed for needing "
            f"more than {MAX_MISSING} extra ingredients."
        )
    return notes


# ---------- Generating ----------


@dataclass
class GenerateResult:
    suggestions: list[Suggestion]
    notes: list[str] = field(default_factory=list)


def review_batch(session: Session, kind: str, prompt: str, recipes: list[Recipe],
                 never: list[Restriction], available: dict[str, str]) -> tuple[list[Recipe], list[str]]:
    """Check every recipe; ask ONCE for replacements of any that fail.

    Used for both Cook ("generate") and spin-offs ("spinoff"), so every
    recipe anyone sees has been through review() and the never filter.
    Returns (recipes that passed, tidied) and notes about any we dropped.
    """
    kept: list[Recipe] = []
    failed: list[tuple[Recipe, Problem]] = []
    for recipe in recipes:
        tidied, problem = review(recipe, never, available)
        if problem:
            failed.append((recipe, problem))
        else:
            kept.append(tidied)

    removed: list[str] = []  # problem kinds of recipes we had to drop
    if failed:
        fix = llm.render_prompt(
            "fix.txt",
            problems=[(recipe.title, problem.message) for recipe, problem in failed],
            count=len(failed),
            keep_titles=[r.title for r in kept],
        )
        try:
            replacements = llm.request_recipes(session, kind, prompt + "\n" + fix).recipes[: len(failed)]
        except llm.GenerationError:
            replacements = []  # keep the good ones; the failed ones are dropped below

        # Slot i of the replacements stands in for failed recipe i.
        for i, (_original, problem) in enumerate(failed):
            if i >= len(replacements):
                removed.append(problem.kind)
                continue
            tidied, new_problem = review(replacements[i], never, available)
            if new_problem:
                removed.append(new_problem.kind)
            else:
                kept.append(tidied)

    return kept, removal_notes(removed)


def _batch_filter(query, parent_recipe_id: int | None):
    """Limit a Suggestion query to one batch: Cook's, or one recipe's spin-offs."""
    if parent_recipe_id is None:
        return query.where(Suggestion.parent_recipe_id == None)  # noqa: E711 (SQL IS NULL)
    return query.where(Suggestion.parent_recipe_id == parent_recipe_id)


def latest_suggestions(session: Session, parent_recipe_id: int | None = None) -> list[Suggestion]:
    """The Cook batch, or (with parent_recipe_id) that recipe's latest spin-offs."""
    query = _batch_filter(select(Suggestion), parent_recipe_id).order_by(Suggestion.id)
    return list(session.exec(query))


def save_batch(session: Session, recipes: list[Recipe],
               parent_recipe_id: int | None = None) -> list[Suggestion]:
    """Replace one batch (and only that batch) with these recipes."""
    session.exec(_batch_filter(delete(Suggestion), parent_recipe_id))
    rows = [Suggestion(recipe_json=r.model_dump(), parent_recipe_id=parent_recipe_id) for r in recipes]
    session.add_all(rows)
    session.commit()
    for row in rows:
        session.refresh(row)
    return rows


def generate(session: Session, req: CookRequest) -> GenerateResult:
    """Generate, filter and store a batch of recipes. Raises llm.GenerationError."""
    people = eating(session, req.eaters)
    never = never_restrictions_for(session, [p.id for p in people])
    available = available_items(session)
    prompt = llm.render_prompt("generate.txt", **prompt_context(session, req, people, never))

    llm.check_cost_guard(session)
    first = llm.request_recipes(session, "generate", prompt).recipes[: req.count]
    kept, notes = review_batch(session, "generate", prompt, first, never, available)
    return GenerateResult(save_batch(session, kept), notes)
