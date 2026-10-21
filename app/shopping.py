"""This week's plan and the shopping list (SPEC.md §2.4).

The plan
- A plan item always holds its own copy of the recipe JSON, copied from a
  suggestion or a saved recipe by id (never from the form). It also points
  at the saved recipe when there is one, so deleting that saved recipe takes
  it off the plan (ON DELETE CASCADE in migration 0001).

The shopping list (the List tab) shows three kinds of thing:
- 'plan' rows: the planned recipes' missing ingredients. sync() keeps these
  in step with the plan: duplicates are merged by normalize_name, and
  anything already in the pantry (Have or Buying) or an enabled staple is
  left off.
- 'manual' rows: typed in on the List tab. Always added, even if the item is
  in Have (you might be running low).
- The Pantry's Buying items. These are NOT copied into shopping_item while
  they're waiting to be bought; the page reads them straight from the
  pantry. Checking one off moves it to Have and records a checked 'buying'
  row, so it can be unchecked back into Buying.

Checking an item off puts it in Have. Checked items stay on the list (struck
through) until "Clear checked", and unchecking undoes the pantry change.
"""

from dataclasses import dataclass, field

from sqlmodel import Session, delete, select

from app import saved
from app.models import PantryItem, PlanItem, ShoppingItem, Staple, Suggestion
from app.pantry import clean_name, find_item, move_to_have, normalize_name, split_items

# ---------- The plan ----------


def planned(session: Session) -> list[PlanItem]:
    """This week's plan, in the order things were added."""
    return list(session.exec(select(PlanItem).order_by(PlanItem.added_at, PlanItem.id)))


def is_planned(session: Session, recipe_json: dict) -> bool:
    # Comparing whole recipes is fine: the plan is a handful of rows. A saved
    # recipe and the card it was saved from hold identical JSON, so either
    # one counts as "in the plan".
    return any(item.recipe_json == recipe_json for item in planned(session))


def in_plan(session: Session, suggestions: list[Suggestion]) -> set[int]:
    """Ids of the suggestions whose recipe is in the plan (for the cards)."""
    plan = [item.recipe_json for item in planned(session)]
    return {s.id for s in suggestions if s.recipe_json in plan}


def add_to_plan(session: Session, recipe_json: dict, saved_recipe_id: int | None = None) -> None:
    """Add a recipe to the plan (once: adding it again does nothing)."""
    if is_planned(session, recipe_json):
        return
    session.add(PlanItem(recipe_json=dict(recipe_json), saved_recipe_id=saved_recipe_id))
    session.commit()
    sync(session)


def remove_from_plan(session: Session, plan_id: int) -> None:
    item = session.get(PlanItem, plan_id)
    if item is not None:  # already gone (double tap) is fine
        session.delete(item)
        session.commit()
    sync(session)


def clear_plan(session: Session) -> None:
    session.exec(delete(PlanItem))
    session.commit()
    sync(session)


# ---------- Keeping the list in step with the plan ----------


@dataclass
class Need:
    name: str                                         # as the first recipe wrote it
    recipes: list[str] = field(default_factory=list)  # titles of the recipes that need it


def needs(plan: list[PlanItem]) -> dict[str, Need]:
    """{normalized name: Need} for every planned recipe's missing ingredients."""
    result: dict[str, Need] = {}
    for item in plan:
        title = item.recipe_json.get("title") or "Untitled recipe"
        for name in item.recipe_json.get("missing_ingredients") or []:
            key = normalize_name(name)
            if not key:
                continue
            need = result.setdefault(key, Need(clean_name(name)))
            if title not in need.recipes:
                need.recipes.append(title)
    return result


def sync(session: Session) -> None:
    """Bring the unchecked 'plan' rows in line with the plan and the pantry.

    Safe to run any time (it's run on every List page load, so pantry edits
    show up). It never touches manual rows or checked rows.
    """
    needed = needs(planned(session))

    # Things we won't put on the list from a recipe: anything in the pantry
    # (Have, or Buying, which the list shows anyway) and enabled staples.
    on_hand = {item.normalized_name for item in session.exec(select(PantryItem))}
    staples = session.exec(select(Staple).where(Staple.enabled == True))  # noqa: E712
    on_hand |= {normalize_name(s.name) for s in staples}

    rows = list(session.exec(select(ShoppingItem).order_by(ShoppingItem.id)))
    plan_rows = [r for r in rows if r.source == "plan" and not r.checked]
    plan_ids = {r.id for r in plan_rows}
    # Already on the list some other way (typed in, or checked off).
    elsewhere = {r.normalized_name for r in rows if r.id not in plan_ids}

    kept = set()
    for row in plan_rows:
        key = row.normalized_name
        if key not in needed or key in on_hand or key in elsewhere or key in kept:
            session.delete(row)
        else:
            kept.add(key)

    for key, need in needed.items():
        if key not in kept and key not in on_hand and key not in elsewhere:
            session.add(ShoppingItem(name=need.name, normalized_name=key, source="plan"))
    session.commit()


# ---------- Adding, checking and removing items ----------


def add_manual(session: Session, text: str) -> str:
    """Add typed-in items (split on commas and newlines). Returns a note for
    the page about anything skipped because it's already on the list."""
    on_list = {r.normalized_name for r in session.exec(
        select(ShoppingItem).where(ShoppingItem.checked == False))}  # noqa: E712
    on_list |= {i.normalized_name for i in session.exec(
        select(PantryItem).where(PantryItem.status == "buying"))}

    skipped = []
    for name in split_items(text):
        key = normalize_name(name)
        if key in on_list:
            skipped.append(name)
            continue
        session.add(ShoppingItem(name=name, normalized_name=key, source="manual"))
        on_list.add(key)
    session.commit()

    if not skipped:
        return ""
    if len(skipped) == 1:
        return f"{skipped[0]} is already on the list."
    return f"{', '.join(skipped)} are already on the list."


def _record_buying_checked(session: Session, buying: PantryItem, row: ShoppingItem) -> None:
    """Move a Buying item to Have and turn `row` into its checked 'buying' row."""
    buying_id = buying.id
    have = move_to_have(session, buying)
    row.source = "buying"
    # move_to_have hands back a different row only if the item was somehow
    # in Have already. Then there's nothing of ours to undo.
    row.pantry_item_id = have.id if have.id == buying_id else None
    row.checked = True
    session.add(row)
    session.commit()


def check(session: Session, row: ShoppingItem) -> None:
    """Check off a plan or manual item: it goes into Have."""
    if row.checked:
        return
    in_pantry = find_item(session, row.name)
    if in_pantry is not None and in_pantry.status == "buying":
        # It was on the Buying list too, so treat it as that Buying item
        # (unchecking then puts it back in Buying).
        _record_buying_checked(session, in_pantry, row)
        return
    if in_pantry is None:
        have = PantryItem(name=row.name, normalized_name=row.normalized_name, status="have")
        session.add(have)
        session.flush()  # gives `have` its id
        row.pantry_item_id = have.id
    # else: already in Have. Leave the pantry alone, and nothing to undo later.
    row.checked = True
    session.add(row)
    session.commit()


def check_buying(session: Session, buying: PantryItem) -> None:
    """Check off one of the Pantry's Buying items: it moves to Have."""
    if buying.status != "buying":
        return
    row = ShoppingItem(name=buying.name, normalized_name=buying.normalized_name, source="buying")
    _record_buying_checked(session, buying, row)


def uncheck(session: Session, row: ShoppingItem) -> None:
    """Undo a check: take back the pantry change and put the item back on the list."""
    if not row.checked:
        return
    ours = session.get(PantryItem, row.pantry_item_id) if row.pantry_item_id else None

    if row.source == "buying":
        # Back onto the Pantry's Buying list, which is where the list shows it.
        if ours is not None:
            ours.status = "buying"
            session.add(ours)
        elif find_item(session, row.name) is None:
            session.add(PantryItem(name=row.name, normalized_name=row.normalized_name,
                                   status="buying"))
        session.delete(row)
    else:
        row.checked = False
        row.pantry_item_id = None
        session.add(row)
        if ours is not None:
            session.delete(ours)
    session.commit()
    sync(session)


def delete_manual(session: Session, row: ShoppingItem) -> None:
    """Remove a typed-in item. Plan items go away with their recipe instead."""
    if row.source == "manual":
        session.delete(row)
        session.commit()


def clear_checked(session: Session) -> None:
    session.exec(delete(ShoppingItem).where(ShoppingItem.checked == True))  # noqa: E712
    session.commit()


# ---------- The List tab ----------


@dataclass
class Entry:
    """One unchecked line on the shopping list, whatever it came from."""
    name: str
    check_url: str
    hint: str = ""                # e.g. "for Lemon Chicken" or "Buying"
    delete_url: str | None = None  # manual items only


def list_context(session: Session, note: str = "") -> dict:
    """Everything partials/list_page.html needs."""
    plan = planned(session)
    needed = needs(plan)
    rows = session.exec(select(ShoppingItem)).all()
    buying = session.exec(select(PantryItem).where(PantryItem.status == "buying")).all()

    to_buy = []
    for row in rows:
        if row.checked:
            continue
        if row.source == "plan" and row.normalized_name in needed:
            hint = "for " + ", ".join(needed[row.normalized_name].recipes)
        else:
            hint = ""
        to_buy.append(Entry(
            row.name, f"/list/items/{row.id}/check", hint,
            f"/list/items/{row.id}/delete" if row.source == "manual" else None,
        ))
    for item in buying:
        to_buy.append(Entry(item.name, f"/list/buying/{item.id}/check", "Buying"))
    to_buy.sort(key=lambda e: e.name)

    return {
        "plan": plan,
        # The same display-only heads-up as the Saved tab, against today's
        # `never` rules (warnings() only needs .id and .recipe_json).
        "heads_up": saved.warnings(session, plan),
        "to_buy": to_buy,
        "in_cart": sorted((r for r in rows if r.checked), key=lambda r: r.name),
        "note": note,
    }
