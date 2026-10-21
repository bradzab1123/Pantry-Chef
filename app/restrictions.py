"""The `never` restriction filter (SPEC.md §1).

Every generated or spin-off recipe is checked here before anyone sees it.
This is safety-relevant (Crohn's triggers, shellfish), so the rules lean
toward flagging: a false alarm costs one regenerated recipe, a miss is worse.

Matching rules, for each `never` restriction's term and each of its aliases:
- Case-insensitive, accent-insensitive ("jalapeño" == "jalapeno").
- Whole words only: "corn" matches "sweet corn" and "corn," but not "acorn",
  "peppercorn" or "cornstarch". Popcorn is caught by an alias, not a special case.
- Simple plurals on the last word: +s / +es, consonant+y -> ies, and a plural
  term also catches its singular ("prawns" catches "prawn").
- Words in a phrase can be split by any spaces or hyphens ("XO-sauce").
- An exception ("corn starch") cancels a match only where its own match covers
  that match AND is longer than it, so an exception can never switch off the
  bare term itself.

Tests: tests/test_restrictions.py. Never weaken this to make a test pass.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from sqlmodel import Session, select

from app.models import Restriction

VOWELS = "aeiou"
MIN_STEM = 3  # don't strip a plural ending if it would leave fewer letters

# Hyphen-like characters that should count as a plain "-".
DASHES = re.compile("[‐‑‒–—―−]")

# Between the words of a multi-word term: any run of spaces and/or hyphens.
WORD_GAP = r"[\s\-]+"


# ---------- Text normalizing ----------


def _normalize(text: str) -> str:
    """Lowercase, strip accents, and turn fancy dashes into "-".

    NFKD splits "é" into "e" plus a separate accent mark; we then drop the
    marks. casefold() is a stronger lower() meant for comparisons.
    """
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return DASHES.sub("-", text).casefold()


# ---------- Building a pattern for one term ----------


def _singular_stems(word: str) -> set[str]:
    """The word itself, plus what it might be the plural of."""
    candidates = []
    if word.endswith("ies"):
        candidates.append(word[:-3] + "y")      # berries -> berry
    if word.endswith("es"):
        candidates.append(word[:-2])            # tomatoes -> tomato
    if word.endswith("s") and not word.endswith("ss"):
        candidates.append(word[:-1])            # prawns -> prawn
    return {word} | {c for c in candidates if len(c) >= MIN_STEM}


def _last_word_regex(word: str) -> str:
    """Regex for the last word of a term, allowing simple plural endings."""
    options = set()
    for stem in _singular_stems(word):
        options.add(re.escape(stem) + "(?:s|es)?")       # corn, corns, dishes
        if stem.endswith("y") and len(stem) > 2 and stem[-2] not in VOWELS:
            options.add(re.escape(stem[:-1]) + "ies")    # berry -> berries
    # Longest first, so the result doesn't depend on set order.
    return "(?:" + "|".join(sorted(options, key=lambda o: (-len(o), o))) + ")"


def _pattern(term: str) -> re.Pattern | None:
    """Compile a whole-word pattern for a term, or None if it's blank.

    (?<!\\w) and (?!\\w) mean "no letter/digit right before / right after",
    which is what stops "corn" matching inside "acorn" or "cornstarch".
    """
    words = [w for w in re.split(WORD_GAP, _normalize(term).strip()) if w]
    if not words:
        return None
    parts = [re.escape(w) for w in words[:-1]] + [_last_word_regex(words[-1])]
    return re.compile(r"(?<!\w)" + WORD_GAP.join(parts) + r"(?!\w)")


def _spans(terms: Iterable[str], text: str) -> list[tuple[int, int]]:
    """(start, end) of every match of any of `terms` in (normalized) `text`."""
    spans = []
    for term in terms:
        pattern = _pattern(term)
        if pattern is not None:
            spans.extend(m.span() for m in pattern.finditer(text))
    return spans


# ---------- Matching a restriction against text ----------


def find_matches(text: str, restriction: Restriction) -> list[str]:
    """The bits of `text` that hit this restriction (empty list = no hit).

    Ignores level and active; check_recipe decides which restrictions count.
    Returned snippets are lowercased/normalized, e.g. "sweet corn" -> ["corn"].
    """
    text = _normalize(text or "")
    hits = _spans([restriction.term, *restriction.aliases], text)
    excused = _spans(restriction.exceptions, text)

    def is_excused(start: int, end: int) -> bool:
        # The exception must cover the whole hit and be strictly longer, so
        # "corn starch" excuses the "corn" in it, but "corn" can't excuse "corn".
        return any(es <= start and end <= ee and (ee - es) > (end - start)
                   for es, ee in excused)

    kept = [(s, e) for s, e in hits if not is_excused(s, e)]

    # When matches overlap (term "shrimp" inside alias "shrimp paste"), report
    # only the longest. Sort by start, longest first, then skip anything that
    # sits inside the previous kept match.
    kept.sort(key=lambda span: (span[0], -span[1]))
    result, last_end = [], -1
    for start, end in kept:
        if end <= last_end:
            continue
        result.append(text[start:end])
        last_end = end
    return result


# ---------- Checking a whole recipe ----------


@dataclass
class Violation:
    restriction_id: int | None
    person_id: int
    term: str      # the restriction's term, e.g. "corn"
    matched: str   # what was found, e.g. "popcorn"
    where: str     # "title", "summary", "ingredient", "missing ingredient", "step 3"


def recipe_texts(recipe: dict) -> list[tuple[str, str]]:
    """The (where, text) pairs that get checked, in display order.

    `recipe` is the SPEC.md §3 Recipe as a dict (Recipe.model_dump() or a saved
    recipe_json). diet_notes and variation_note are deliberately NOT checked:
    they exist to say things like "no shrimp" or "swapped shrimp for chicken".
    """
    texts = []
    for key in ("title", "summary"):
        if recipe.get(key):
            texts.append((key, recipe[key]))
    for ingredient in recipe.get("ingredients") or []:
        # Normally a dict; check a bare string too rather than skip it.
        name = ingredient.get("name") if isinstance(ingredient, dict) else ingredient
        if name:
            texts.append(("ingredient", str(name)))
    for name in recipe.get("missing_ingredients") or []:
        if name:
            texts.append(("missing ingredient", str(name)))
    for number, step in enumerate(recipe.get("steps") or [], start=1):
        if step:
            texts.append((f"step {number}", str(step)))
    return texts


def check_recipe(recipe: dict, restrictions: Iterable[Restriction]) -> list[Violation]:
    """All `never` violations in a recipe. An empty list means it passes.

    Only active `never` restrictions are used, whatever the caller passes in,
    so handing over a person's full list (with `avoid` rows) is safe.
    """
    rules = [r for r in restrictions if r.level == "never" and r.active]
    violations = []
    for where, text in recipe_texts(recipe):
        for rule in rules:
            for matched in find_matches(text, rule):
                violations.append(Violation(rule.id, rule.person_id, rule.term, matched, where))
    return violations


def violation_message(violations: list[Violation]) -> str:
    """One sentence naming the violations, for the "regenerate once" request."""
    if not violations:
        return ""
    parts = [f'"{v.matched}" (restricted: {v.term}) in {v.where}' for v in violations]
    return "Contains " + "; ".join(parts) + "."


# ---------- Loading ----------


def never_restrictions_for(session: Session, person_ids: Iterable[int]) -> list[Restriction]:
    """Active `never` restrictions for the people eating, in id order."""
    ids = list(person_ids)
    if not ids:
        return []
    query = (
        select(Restriction)
        .where(Restriction.person_id.in_(ids))
        .where(Restriction.level == "never")
        .where(Restriction.active == True)  # noqa: E712 (SQL needs ==, not "is")
        .order_by(Restriction.id)
    )
    return list(session.exec(query))
