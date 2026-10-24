"""Settings page: people's names, restrictions, staples, and API usage.

Safety rules for restrictions (the `never` level feeds the recipe filter):
- A `never` restriction is always active. Only `avoid` ones can be switched off.
- Downgrading `never` -> `avoid` needs a second, confirming Save.
- Deleting asks for confirmation in the browser (hx-confirm).

Note on HTMX: it doesn't swap in 4xx/5xx responses by default, so validation
errors come back as a normal 200 response with the error shown in the form.
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from app import config, llm
from app.db import get_session
from app.models import LEVELS, Person, Restriction, Staple
from app.pantry import normalize_name, split_items, split_terms
from app.web import is_htmx, redirect, render, render_partial

router = APIRouter()

MAX_NAME_LENGTH = 40


# ---------- Helpers ----------


def restrictions_for(session: Session, person_id: int) -> list[Restriction]:
    """A person's restrictions: `never` first, then alphabetical."""
    rows = session.exec(select(Restriction).where(Restriction.person_id == person_id)).all()
    return sorted(rows, key=lambda r: (r.level != "never", r.term.lower()))


def all_staples(session: Session) -> list[Staple]:
    return session.exec(select(Staple).order_by(Staple.name)).all()


def get_or_404(session: Session, model, item_id: int):
    item = session.get(model, item_id)
    if item is None:
        raise HTTPException(404, "Not found")
    return item


def clean_text(text: str) -> str:
    return " ".join(text.split())


def name_error(name: str, other_names: list[str]) -> str:
    """What's wrong with a person's (cleaned) name, or "" if it's fine.
    Names must differ so "Who's eating" is never ambiguous."""
    if not name:
        return "Name can't be empty."
    if len(name) > MAX_NAME_LENGTH:
        return f"Keep it under {MAX_NAME_LENGTH} characters."
    if name.lower() in (n.lower() for n in other_names):
        return "The two names need to be different."
    return ""


def restriction_error(session: Session, person_id: int, term: str, level: str,
                      exclude_id: int | None = None) -> str:
    """Return a message describing what's wrong, or "" if the values are fine."""
    if not term:
        return "Enter an ingredient."
    if level not in LEVELS:
        return "Pick a level."
    for other in restrictions_for(session, person_id):
        if other.id != exclude_id and other.term.lower() == term.lower():
            return f"There's already a restriction for “{other.term}”."
    return ""


def form_values(term="", level="never", aliases="", exceptions="", notes="") -> dict:
    """What to put back into a restriction form (e.g. after an error)."""
    return {"term": term, "level": level, "aliases": aliases,
            "exceptions": exceptions, "notes": notes}


def values_from(r: Restriction) -> dict:
    return form_values(r.term, r.level, ", ".join(r.aliases), ", ".join(r.exceptions), r.notes or "")


def restriction_list_partial(request: Request, session: Session, person: Person,
                             form: dict | None = None, error: str = ""):
    return render_partial(
        request, "partials/restrictions.html",
        person=person, restrictions=restrictions_for(session, person.id),
        form=form or form_values(), error=error,
    )


def edit_form_partial(request: Request, r: Restriction, person: Person, form: dict,
                      error: str = "", confirm_downgrade: bool = False):
    return render_partial(
        request, "partials/restriction_form.html",
        r=r, person=person, form=form, error=error, confirm_downgrade=confirm_downgrade,
    )


# ---------- Page ----------


@router.get("/settings")
def settings_page(request: Request, session: Session = Depends(get_session)):
    people = session.exec(select(Person).order_by(Person.id)).all()
    return render(
        request, "settings.html", "settings", title="Settings",
        people=[(p, restrictions_for(session, p.id)) for p in people],
        staples=all_staples(session), form=form_values(),
        usage=llm.usage_this_month(session), model=config.CLAUDE_MODEL,
        hourly_limit=config.MAX_GENERATIONS_PER_HOUR,
    )


# ---------- People ----------


@router.post("/settings/people/{person_id}")
def rename_person(person_id: int, request: Request, name: str = Form(""),
                  session: Session = Depends(get_session)):
    person = get_or_404(session, Person, person_id)
    name = clean_text(name)
    others = session.exec(select(Person.name).where(Person.id != person.id)).all()
    error = name_error(name, others)
    if not error:
        person.name = name
        session.add(person)
        session.commit()
    if not is_htmx(request):
        return redirect("/settings")
    return render_partial(request, "partials/person_name.html",
                          person=person, error=error, saved=not error, typed=name)


# ---------- Restrictions ----------


@router.post("/settings/restrictions")
def add_restriction(
    request: Request,
    person_id: int = Form(...),
    term: str = Form(""),
    level: str = Form("never"),
    aliases: str = Form(""),
    exceptions: str = Form(""),
    notes: str = Form(""),
    session: Session = Depends(get_session),
):
    person = get_or_404(session, Person, person_id)
    term = clean_text(term)
    error = restriction_error(session, person.id, term, level)
    if not error:
        session.add(Restriction(
            person_id=person.id,
            term=term,
            level=level,
            aliases=split_terms(aliases),
            exceptions=split_terms(exceptions),
            notes=notes.strip() or None,
            active=True,
        ))
        session.commit()
    if not is_htmx(request):
        return redirect("/settings")
    if error:
        # Keep what they typed so they can fix it.
        return restriction_list_partial(
            request, session, person,
            form=form_values(term, level, aliases, exceptions, notes), error=error,
        )
    return restriction_list_partial(request, session, person)


@router.get("/settings/restrictions/{restriction_id}")
def restriction_card(restriction_id: int, request: Request, session: Session = Depends(get_session)):
    """The read-only card (used by the edit form's Cancel button)."""
    r = get_or_404(session, Restriction, restriction_id)
    return render_partial(request, "partials/restriction.html", r=r)


@router.get("/settings/restrictions/{restriction_id}/edit")
def edit_restriction_form(restriction_id: int, request: Request, session: Session = Depends(get_session)):
    r = get_or_404(session, Restriction, restriction_id)
    person = session.get(Person, r.person_id)
    return edit_form_partial(request, r, person, values_from(r))


@router.post("/settings/restrictions/{restriction_id}")
def save_restriction(
    restriction_id: int,
    request: Request,
    term: str = Form(""),
    level: str = Form("never"),
    aliases: str = Form(""),
    exceptions: str = Form(""),
    notes: str = Form(""),
    confirm_downgrade: bool = Form(False),
    session: Session = Depends(get_session),
):
    r = get_or_404(session, Restriction, restriction_id)
    person = session.get(Person, r.person_id)
    term = clean_text(term)
    typed = form_values(term, level, aliases, exceptions, notes)

    error = restriction_error(session, r.person_id, term, level, exclude_id=r.id)
    if error:
        if not is_htmx(request):
            return redirect("/settings")
        return edit_form_partial(request, r, person, typed, error=error)

    # Safety: turning a `never` into an `avoid` stops the recipe filter from
    # blocking it, so make them press Save a second time to confirm.
    if r.level == "never" and level == "avoid" and not confirm_downgrade:
        if not is_htmx(request):
            return redirect("/settings")
        return edit_form_partial(request, r, person, typed, confirm_downgrade=True)

    r.term = term
    r.level = level
    r.aliases = split_terms(aliases)
    r.exceptions = split_terms(exceptions)
    r.notes = notes.strip() or None
    if level == "never":
        r.active = True  # `never` restrictions can't be switched off
    session.add(r)
    session.commit()
    if not is_htmx(request):
        return redirect("/settings")
    return render_partial(request, "partials/restriction.html", r=r)


@router.post("/settings/restrictions/{restriction_id}/toggle")
def toggle_restriction(restriction_id: int, request: Request, session: Session = Depends(get_session)):
    r = get_or_404(session, Restriction, restriction_id)
    if r.level == "never":
        raise HTTPException(400, "Never restrictions are always on.")
    r.active = not r.active
    session.add(r)
    session.commit()
    if not is_htmx(request):
        return redirect("/settings")
    return render_partial(request, "partials/restriction.html", r=r)


@router.post("/settings/restrictions/{restriction_id}/delete")
def delete_restriction(restriction_id: int, request: Request, session: Session = Depends(get_session)):
    r = get_or_404(session, Restriction, restriction_id)
    session.delete(r)
    session.commit()
    if not is_htmx(request):
        return redirect("/settings")
    # An empty response swapped over the card removes it from the page.
    return HTMLResponse("")


# ---------- Staples ----------


def staples_response(request: Request, session: Session, note: str = ""):
    if not is_htmx(request):
        return redirect("/settings")
    return render_partial(request, "partials/staples.html", staples=all_staples(session), note=note)


@router.post("/settings/staples")
def add_staples(request: Request, names: str = Form(""), session: Session = Depends(get_session)):
    existing = {normalize_name(s.name) for s in all_staples(session)}
    skipped = []
    for name in split_items(names):
        key = normalize_name(name)
        if key in existing:
            skipped.append(name)
            continue
        session.add(Staple(name=name, enabled=True))
        existing.add(key)
    session.commit()
    note = ""
    if skipped:
        already = "are already staples" if len(skipped) > 1 else "is already a staple"
        note = f"{', '.join(skipped)} {already}."
    return staples_response(request, session, note)


@router.post("/settings/staples/{staple_id}/toggle")
def toggle_staple(staple_id: int, request: Request, session: Session = Depends(get_session)):
    staple = get_or_404(session, Staple, staple_id)
    staple.enabled = not staple.enabled
    session.add(staple)
    session.commit()
    return staples_response(request, session)


@router.post("/settings/staples/{staple_id}/delete")
def delete_staple(staple_id: int, request: Request, session: Session = Depends(get_session)):
    staple = session.get(Staple, staple_id)
    if staple:  # ignore double taps
        session.delete(staple)
        session.commit()
    return staples_response(request, session)
