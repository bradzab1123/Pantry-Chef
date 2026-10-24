"""The "Cooked it" step: keep the pantry accurate after cooking (SPEC.md §2.6).

The pantry stays a have/don't-have list. After cooking, we show the
recipe's ingredients that are in Have and the person taps the ones they
used up. Those move to Buying (so they're on the shopping list) or, if they
don't want them back, are deleted. The recipe also comes off this week's
plan, and the shopping list is synced.

- The recipe is always loaded from the database by kind and id (a
  suggestion, a saved recipe or a plan item), never from the form.
- The form posts pantry item ids. Only ids that are still in Have AND match
  one of this recipe's ingredients right now are acted on; anything else
  (a double tap, a stale page, a made-up id) is quietly ignored.
"""

from dataclasses import dataclass, field

from sqlmodel import Session, select

from app import saved, shopping
from app.models import PantryItem, PlanItem, SavedRecipe, ShoppingItem
from app.pantry import match_pantry_item

KINDS = ("suggestion", "saved", "plan")


class NotFound(Exception):
    """The recipe doesn't exist (any more)."""


@dataclass
class Source:
    """A recipe someone cooked, and where it came from."""
    kind: str          # one of KINDS
    id: int
    recipe_json: dict
    back_url: str      # the page the recipe is shown on

    @property
    def url(self) -> str:
        return f"/cooked/{self.kind}/{self.id}"


def find_recipe(session: Session, kind: str, recipe_id: int) -> Source:
    if kind == "suggestion":
        try:
            suggestion = saved.get_suggestion(session, recipe_id)
        except saved.NotFound:
            raise NotFound(recipe_id)
        return Source(kind, recipe_id, suggestion.recipe_json, saved.card_page(suggestion))
    if kind == "saved":
        recipe = session.get(SavedRecipe, recipe_id)
        if recipe is not None:
            return Source(kind, recipe_id, recipe.recipe_json, f"/saved/{recipe_id}")
    if kind == "plan":
        item = session.get(PlanItem, recipe_id)
        if item is not None and item.recipe_json:
            return Source(kind, recipe_id, item.recipe_json, "/list")
    raise NotFound(recipe_id)


def used_up_candidates(session: Session, recipe_json: dict) -> list[PantryItem]:
    """The Have items this recipe used, in ingredient order, each once.

    "pantry" and "buy" ingredients both count (a "buy" item may have been
    bought since). Staples are always assumed on hand, so they're skipped.
    """
    have = list(session.exec(
        select(PantryItem).where(PantryItem.status == "have").order_by(PantryItem.name)))
    found: dict[int, PantryItem] = {}
    for ingredient in recipe_json.get("ingredients") or []:
        if ingredient.get("source") not in ("pantry", "buy"):
            continue
        item = match_pantry_item(ingredient.get("name") or "", have)
        if item is not None and item.id not in found:
            found[item.id] = item
    return list(found.values())


@dataclass
class CookedResult:
    used_up: list[str] = field(default_factory=list)  # names of the items we changed
    to_list: bool = True                              # moved to Buying (else deleted)
    unplanned: bool = False                           # taken off this week's plan

    def message(self) -> str:
        if not self.used_up:
            text = "Nothing marked used up."
        elif self.to_list:
            text = f"Moved {', '.join(self.used_up)} to Buying."
        else:
            text = f"Removed {', '.join(self.used_up)} from the pantry."
        if self.unplanned:
            text += " Taken off this week's plan."
        return text


def _move_to_buying(session: Session, item: PantryItem) -> None:
    item.status = "buying"
    session.add(item)
    # A checked-off cart row pointing at this item would put it back to how
    # it was before the shopping trip if unchecked. The cooking is newer, so
    # unhook it.
    for row in list(session.exec(select(ShoppingItem).where(ShoppingItem.pantry_item_id == item.id))):
        row.pantry_item_id = None
        session.add(row)
    # A typed-in line for the same thing would now show twice on the List
    # (once as that line, once as this Buying item).
    for row in list(session.exec(select(ShoppingItem).where(
            ShoppingItem.source == "manual",
            ShoppingItem.checked == False,  # noqa: E712
            ShoppingItem.normalized_name == item.normalized_name))):
        session.delete(row)


def cook_it(session: Session, recipe_json: dict, used_ids: list[int], to_list: bool) -> CookedResult:
    """Apply a "Cooked it": used-up items to Buying (or deleted), recipe off the plan."""
    allowed = {item.id: item for item in used_up_candidates(session, recipe_json)}
    result = CookedResult(to_list=to_list)
    done = set()
    for item_id in used_ids:
        item = allowed.get(item_id)
        if item is None or item_id in done:
            continue  # gone, not in Have, or not part of this recipe
        done.add(item_id)
        result.used_up.append(item.name)
        if to_list:
            _move_to_buying(session, item)
        else:
            # shopping_item.pantry_item_id is ON DELETE SET NULL, so cart
            # rows pointing at it let go by themselves.
            session.delete(item)

    # Off the plan: every plan row holding this same recipe (that includes
    # the plan row itself when it was cooked from the List).
    for plan_item in shopping.planned(session):
        if plan_item.recipe_json == recipe_json:
            session.delete(plan_item)
            result.unplanned = True
    session.commit()
    shopping.sync(session)
    return result
