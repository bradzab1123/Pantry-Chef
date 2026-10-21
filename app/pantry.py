"""Pantry helpers: cleaning names, spotting duplicates, and bulk add.

`normalize_name` builds the key used to decide whether two items are "the same
thing" (pantry, staples, and later the shopping list). It is never shown to
anyone; we show the cleaned name the person typed.

This is NOT the restriction matcher. The safety-relevant `never` filter lives
in restrictions.py and has its own, stricter rules.
"""

import re
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.models import PantryItem

VOWELS = "aeiou"


def clean_name(text: str) -> str:
    """Trim, lowercase and collapse runs of spaces: '  Red  Onion ' -> 'red onion'."""
    return " ".join(text.split()).lower()


def _stem(word: str) -> str:
    """Reduce a word so that its singular and plural give the same key.

    The trick is that both forms are cut down to a shared stem, which may not
    be a real word (that's fine, nobody sees it):
        tomato, tomatoes  -> tomato     (drop "s", then "e")
        olive, olives     -> oliv       (drop "s", then "e")
        peach, peaches    -> peach
        berry, berries    -> berri      (consonant + "y" becomes "i")
        cookie, cookies   -> cooki
    Words ending in "ss" or "us" are left alone (glass, hummus, asparagus).
    Nothing is cut below 3 letters, so "pea"/"peas" and "pie"/"pies" still match.
    Known miss: "leaf" vs "leaves".
    """
    if word.endswith(("ss", "us")):
        return word
    if word.endswith("s") and len(word) > 3:
        word = word[:-1]
    if word.endswith("e") and len(word) > 3:
        word = word[:-1]
    if word.endswith("y") and len(word) > 3 and word[-2] not in VOWELS:
        word = word[:-1] + "i"
    return word


def normalize_name(text: str) -> str:
    """Duplicate-detection key. Only the last word (the main noun) is stemmed,
    so 'cherry tomatoes' and 'cherry tomato' match but 'cherry' isn't touched."""
    words = clean_name(text).split()
    if not words:
        return ""
    words[-1] = _stem(words[-1])
    return " ".join(words)


def _split(text: str) -> list[str]:
    """Split on commas and newlines; collapse whitespace; drop blanks."""
    parts = (" ".join(p.split()) for p in re.split(r"[,\n]", text))
    return [p for p in parts if p]


def split_items(text: str) -> list[str]:
    """Bulk-add input -> cleaned, lowercased names, without repeats
    (repeats judged by normalize_name, so 'egg, Eggs' gives just 'egg')."""
    seen = set()
    items = []
    for part in _split(text):
        name = clean_name(part)
        key = normalize_name(name)
        if key not in seen:
            seen.add(key)
            items.append(name)
    return items


def split_terms(text: str) -> list[str]:
    """Aliases/exceptions input -> list of terms. Keeps the case as typed
    ('XO sauce'); matching is case-insensitive later anyway."""
    seen = set()
    terms = []
    for term in _split(text):
        if term.lower() not in seen:
            seen.add(term.lower())
            terms.append(term)
    return terms


# ---------- Bulk add ----------


@dataclass
class AddResult:
    added: list[str] = field(default_factory=list)
    already_here: list[str] = field(default_factory=list)  # on the same list
    on_other_list: list[str] = field(default_factory=list)  # on the other list

    def note(self, status: str) -> str:
        """A short message for the UI, or "" if there's nothing to say."""
        other = "Buying" if status == "have" else "Have"
        parts = []
        if self.already_here:
            n = len(self.already_here)
            parts.append(f"{n} already on the list" if n > 1 else f"{self.already_here[0]} is already on the list")
        if self.on_other_list:
            names = ", ".join(self.on_other_list)
            verb = "are" if len(self.on_other_list) > 1 else "is"
            parts.append(f"{names} {verb} already in {other}")
        return ". ".join(parts) + ("." if parts else "")


def add_items(session: Session, text: str, status: str) -> AddResult:
    """Add each item from `text` to the `status` list ("have" or "buying").

    An item lives on one list only: anything already on either list is
    skipped and reported back in the result.
    """
    existing = {item.normalized_name: item for item in session.exec(select(PantryItem))}
    result = AddResult()
    for name in split_items(text):
        key = normalize_name(name)
        if key in existing:
            if existing[key].status == status:
                result.already_here.append(name)
            else:
                result.on_other_list.append(name)
            continue
        item = PantryItem(name=name, normalized_name=key, status=status)
        session.add(item)
        existing[key] = item
        result.added.append(name)
    session.commit()
    return result


def find_item(session: Session, name: str) -> PantryItem | None:
    """The pantry item (on either list) that counts as the same thing as `name`."""
    return session.exec(
        select(PantryItem).where(PantryItem.normalized_name == normalize_name(name))
    ).first()


def move_to_have(session: Session, item: PantryItem) -> PantryItem:
    """Move a Buying item to Have, and return the Have row (commits).

    If the same thing is somehow in Have already, the Buying entry is simply
    done: it's deleted and the existing Have row is returned instead.
    """
    if item.status != "buying":
        return item
    duplicate = session.exec(
        select(PantryItem).where(
            PantryItem.status == "have",
            PantryItem.normalized_name == item.normalized_name,
        )
    ).first()
    if duplicate:
        session.delete(item)
        item = duplicate
    else:
        item.status = "have"
        session.add(item)
    session.commit()
    return item
