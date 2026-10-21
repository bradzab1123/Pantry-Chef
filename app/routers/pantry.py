"""Pantry page: the Have and Buying lists, bulk add, delete, and Buying -> Have."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlmodel import Session, select

from app.db import get_session
from app.models import PANTRY_STATUSES, PantryItem, Staple
from app.pantry import add_items
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


def lists_response(request: Request, session: Session, note: str = ""):
    """After a change: HTMX gets the refreshed lists, a plain form post gets a redirect."""
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
    session: Session = Depends(get_session),
):
    if status not in PANTRY_STATUSES:
        raise HTTPException(400, "Unknown list")
    result = add_items(session, names, status)
    return lists_response(request, session, result.note(status))


@router.post("/pantry/items/{item_id}/delete")
def delete_pantry_item(item_id: int, request: Request, session: Session = Depends(get_session)):
    # Quietly ignore an item that's already gone (e.g. a double tap).
    item = session.get(PantryItem, item_id)
    if item:
        session.delete(item)
        session.commit()
    return lists_response(request, session)


@router.post("/pantry/items/{item_id}/have")
def move_to_have(item_id: int, request: Request, session: Session = Depends(get_session)):
    item = session.get(PantryItem, item_id)
    if item and item.status == "buying":
        duplicate = session.exec(
            select(PantryItem).where(
                PantryItem.status == "have",
                PantryItem.normalized_name == item.normalized_name,
            )
        ).first()
        if duplicate:
            # Already in Have, so the Buying entry is simply done.
            session.delete(item)
        else:
            item.status = "have"
            session.add(item)
        session.commit()
    return lists_response(request, session)
