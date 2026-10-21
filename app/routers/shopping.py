"""List tab: this week's plan and the shopping list.

The "Add to plan" buttons live in routers/saved.py with the other card buttons.
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlmodel import Session

from app import shopping
from app.db import get_session
from app.models import PantryItem, ShoppingItem
from app.web import is_htmx, redirect, render, render_partial

router = APIRouter()


def list_response(request: Request, session: Session, note: str = ""):
    """After a change: HTMX gets the refreshed page body, a plain form post gets a redirect."""
    if is_htmx(request):
        return render_partial(request, "partials/list_page.html", **shopping.list_context(session, note))
    return redirect("/list")


def _item_or_404(session: Session, item_id: int) -> ShoppingItem:
    item = session.get(ShoppingItem, item_id)
    if item is None:
        raise HTTPException(404, "That item isn't on the list any more.")
    return item


@router.get("/list")
def list_page(request: Request, session: Session = Depends(get_session)):
    # Catch up with pantry and staple changes made on other tabs.
    shopping.sync(session)
    return render(request, "list.html", "list", title="This week", **shopping.list_context(session))


# ---------- The plan ----------


@router.post("/plan/{plan_id}/delete")
def remove_from_plan(plan_id: int, request: Request, session: Session = Depends(get_session)):
    shopping.remove_from_plan(session, plan_id)
    return list_response(request, session)


@router.post("/plan/clear")
def clear_plan(request: Request, session: Session = Depends(get_session)):
    shopping.clear_plan(session)
    return list_response(request, session)


# ---------- The shopping list ----------


@router.post("/list/items")
def add_items(request: Request, names: str = Form(""), session: Session = Depends(get_session)):
    note = shopping.add_manual(session, names)
    return list_response(request, session, note)


@router.post("/list/items/{item_id}/check")
def check_item(item_id: int, request: Request, session: Session = Depends(get_session)):
    shopping.check(session, _item_or_404(session, item_id))
    return list_response(request, session)


@router.post("/list/items/{item_id}/uncheck")
def uncheck_item(item_id: int, request: Request, session: Session = Depends(get_session)):
    shopping.uncheck(session, _item_or_404(session, item_id))
    return list_response(request, session)


@router.post("/list/items/{item_id}/delete")
def delete_item(item_id: int, request: Request, session: Session = Depends(get_session)):
    item = session.get(ShoppingItem, item_id)
    if item is not None:  # already gone (double tap) is fine
        shopping.delete_manual(session, item)
    return list_response(request, session)


@router.post("/list/buying/{pantry_id}/check")
def check_buying(pantry_id: int, request: Request, session: Session = Depends(get_session)):
    item = session.get(PantryItem, pantry_id)
    if item is None:
        raise HTTPException(404, "That item isn't on the Buying list any more.")
    shopping.check_buying(session, item)
    return list_response(request, session)


@router.post("/list/clear-checked")
def clear_checked(request: Request, session: Session = Depends(get_session)):
    shopping.clear_checked(session)
    return list_response(request, session)
