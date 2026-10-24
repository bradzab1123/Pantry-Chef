"""First-run setup: name the two people.

A new install seeds two people with empty names (0002_seed.sql). Until both
have a name, main.py sends every page to /setup. After that this page is never
shown again, and names are changed in Settings.
"""

from fastapi import APIRouter, Depends, Form, Request
from sqlmodel import Session, select

from app.db import get_session
from app.models import Person
from app.routers.settings import MAX_NAME_LENGTH, clean_text, name_error
from app.web import redirect, render

router = APIRouter()


def needs_setup(session: Session) -> bool:
    """True while anyone is still unnamed."""
    return any(not p.name for p in session.exec(select(Person)).all())


def setup_page_response(request: Request, people: list[Person], typed: dict | None = None,
                        error: str = ""):
    return render(
        request, "setup.html", "", title="Welcome",
        tabs=[],  # no tab bar until setup is done
        people=people, typed=typed or {}, error=error, max_length=MAX_NAME_LENGTH,
    )


@router.get("/setup")
def setup_page(request: Request, session: Session = Depends(get_session)):
    if not needs_setup(session):
        return redirect("/")
    people = session.exec(select(Person).order_by(Person.id)).all()
    return setup_page_response(request, people)


@router.post("/setup")
async def save_setup(request: Request, session: Session = Depends(get_session)):
    if not needs_setup(session):
        return redirect("/")
    people = session.exec(select(Person).order_by(Person.id)).all()

    # The inputs are named name-<person id>, one per person.
    form = await request.form()
    typed = {p.id: clean_text(str(form.get(f"name-{p.id}", ""))) for p in people}

    error = ""
    for p in people:
        others = [name for pid, name in typed.items() if pid != p.id]
        error = name_error(typed[p.id], others)
        if error:
            break
    if error:
        # A 200 with the error shown, keeping what they typed.
        return setup_page_response(request, people, typed, error)

    for p in people:
        p.name = typed[p.id]
        session.add(p)
    session.commit()
    # Next stop: adding each person's restrictions.
    return redirect("/settings")
