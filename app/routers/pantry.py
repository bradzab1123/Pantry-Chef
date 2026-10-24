"""Pantry page: the Have and Buying lists, bulk add, delete, and Buying -> Have."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlmodel import Session, select

from app import pantry
from app.db import get_session
from app.models import PANTRY_STATUSES, PantryItem, Staple
from app.web import is_htmx, redirect, render, render_partial

router = APIRouter()


def lists_context(session: Session, note: str = "") -> dict:
    """Everything the Have/Buying lists partial needs."""
    items = session.exec(select(PantryItem).order_by(PantryItem.name)).all()
    return {
        "have": [i for i in items if i.status == "have"],
        "buying": [i for i in items if i.status == "buying"],
        "note": note,
    }


def pantry_check_context(session: Session, note: str = "", expanded: bool = False) -> dict:
    """What partials/pantry_check.html (on the Cook form) needs."""
    lists = lists_context(session, note)
    return {"have": lists["have"], "buying_count": len(lists["buying"]), "note": note, "expanded": expanded}


def lists_response(request: Request, session: Session, note: str = "", view: str = ""):
    """After a change: HTMX gets the refreshed lists, a plain form post gets a redirect.

    view="cook" means the change was made from the pantry check on the Cook
    form, so that's what gets redrawn (kept open), or redirected back to.
    """
    if view == "cook":
        if is_htmx(request):
            return render_partial(request, "partials/pantry_check.html",
                                  **pantry_check_context(session, note, expanded=True))
        return redirect("/")
    if is_htmx(request):
        return render_partial(request, "partials/pantry_lists.html", **lists_context(session, note))
    return redirect("/pantry")


@router.get("/pantry")
def pantry_page(request: Request, session: Session = Depends(get_session)):
    staples = session.exec(
        select(Staple).where(Staple.enabled == True).order_by(Staple.name)  # noqa: E712
    ).all()
    return render(
        request, "pantry.html", "pantry", title="Pantry",
        staples=staples, **lists_context(session),
    )


@router.post("/pantry/items")
def add_pantry_items(
    request: Request,
    names: str = Form(""),
    status: str = Form("have"),
    view: str = "",
    session: Session = Depends(get_session),
):
    if status not in PANTRY_STATUSES:
        raise HTTPException(400, "Unknown list")
    result = pantry.add_items(session, names, status)
    return lists_response(request, session, result.note(status), view)


@router.post("/pantry/items/{item_id}/delete")
def delete_pantry_item(item_id: int, request: Request, view: str = "",
                       session: Session = Depends(get_session)):
    # Quietly ignore an item that's already gone (e.g. a double tap).
    item = session.get(PantryItem, item_id)
    if item:
        session.delete(item)
        session.commit()
    return lists_response(request, session, view=view)


@router.post("/pantry/items/{item_id}/have")
def move_to_have(item_id: int, request: Request, session: Session = Depends(get_session)):
    item = session.get(PantryItem, item_id)
    if item:
        pantry.move_to_have(session, item)
    return lists_response(request, session)
