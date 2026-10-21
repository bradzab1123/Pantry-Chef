"""Saved tab (list, search, detail, rating, delete, spin-offs), plus the
Save and 👍/👎 buttons on recipe cards (Cook results and spin-offs)."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlmodel import Session

from app import cook, llm, saved
from app.db import get_session
from app.models import SavedRecipe, Suggestion
from app.web import is_htmx, redirect, render, render_partial

router = APIRouter()


def _saved_or_404(session: Session, saved_id: int) -> SavedRecipe:
    try:
        return saved.get_saved(session, saved_id)
    except saved.NotFound:
        raise HTTPException(404, "That recipe isn't saved any more.")


def _suggestion_or_404(session: Session, suggestion_id: int) -> Suggestion:
    try:
        return saved.get_suggestion(session, suggestion_id)
    except saved.NotFound:
        raise HTTPException(404, "That suggestion has been replaced. Generate again.")


def _rating(text: str) -> int:
    try:
        rating = int(text)
    except ValueError:
        rating = 0
    if rating not in saved.RATINGS:
        raise HTTPException(400, "Unknown rating")
    return rating


# ---------- The Saved list ----------


def list_context(session: Session, q: str, fav: bool) -> dict:
    recipes = saved.list_saved(session, q, fav)
    return {"recipes": recipes, "q": q, "fav": fav, "heads_up": saved.warnings(session, recipes)}


@router.get("/saved")
def saved_page(request: Request, q: str = "", fav: str = "", session: Session = Depends(get_session)):
    context = list_context(session, q.strip(), fav == "1")
    if is_htmx(request):
        # The search box swaps in just the list.
        return render_partial(request, "partials/saved_list.html", **context)
    return render(request, "saved.html", "saved", title="Saved", **context)


# ---------- One saved recipe ----------


def spinoff_context(session: Session, recipe: SavedRecipe, notes=(), error="") -> dict:
    """What partials/spinoff_results.html needs."""
    suggestions = cook.latest_suggestions(session, parent_recipe_id=recipe.id)
    return {
        "suggestions": suggestions,
        "saved": saved.saved_for(session, suggestions),
        "notes": list(notes),
        "error": error,
    }


@router.get("/saved/{saved_id}")
def saved_detail(saved_id: int, request: Request, session: Session = Depends(get_session)):
    recipe = _saved_or_404(session, saved_id)
    parent = session.get(SavedRecipe, recipe.parent_recipe_id) if recipe.parent_recipe_id else None
    return render(
        request, "saved_detail.html", "saved", title="Saved",
        r=recipe,
        recipe=recipe.recipe_json,
        parent=parent,
        heads_up=saved.warnings(session, [recipe]).get(recipe.id, []),
        children=saved.spinoffs_of(session, recipe),
        **spinoff_context(session, recipe),
    )


@router.post("/saved/{saved_id}/rate")
def rate_saved(saved_id: int, request: Request, rating: str = Form(""),
               session: Session = Depends(get_session)):
    recipe = _saved_or_404(session, saved_id)
    saved.rate(session, recipe, _rating(rating))
    if is_htmx(request):
        return render_partial(request, "partials/rating.html", rating=recipe.rating,
                              rate_url=f"/saved/{recipe.id}/rate", rate_target="closest .rating")
    return redirect(f"/saved/{recipe.id}")


@router.post("/saved/{saved_id}/delete")
def delete_saved(saved_id: int, session: Session = Depends(get_session)):
    # A plain form post (with a confirm() first), so no HTMX partial here.
    recipe = session.get(SavedRecipe, saved_id)
    if recipe is not None:  # already gone (double tap) is fine
        saved.delete_saved(session, recipe)
    return redirect("/saved")


@router.post("/saved/{saved_id}/spinoff")
def spin_off(saved_id: int, request: Request, session: Session = Depends(get_session)):
    recipe = _saved_or_404(session, saved_id)
    try:
        result = saved.spin_off(session, recipe)
    except llm.GenerationError as error:
        if not is_htmx(request):
            return redirect(f"/saved/{recipe.id}")
        # 200, not an error status: HTMX doesn't swap 4xx/5xx responses.
        return render_partial(request, "partials/spinoff_results.html",
                              **spinoff_context(session, recipe, error=str(error)))

    if not is_htmx(request):
        return redirect(f"/saved/{recipe.id}")
    return render_partial(request, "partials/spinoff_results.html",
                          **spinoff_context(session, recipe, notes=result.notes))


# ---------- Buttons on recipe cards ----------


def card_actions_response(request: Request, session: Session, suggestion: Suggestion):
    """HTMX gets the refreshed buttons; a plain post goes back to the page the card is on."""
    if is_htmx(request):
        return render_partial(request, "partials/card_actions.html", s=suggestion,
                              saved_recipe=saved.saved_for(session, [suggestion]).get(suggestion.saved_recipe_id))
    if suggestion.parent_recipe_id:
        return redirect(f"/saved/{suggestion.parent_recipe_id}")
    return redirect("/")


@router.post("/suggestions/{suggestion_id}/save")
def save_suggestion(suggestion_id: int, request: Request, session: Session = Depends(get_session)):
    suggestion = _suggestion_or_404(session, suggestion_id)
    saved.save_suggestion(session, suggestion)
    return card_actions_response(request, session, suggestion)


@router.post("/suggestions/{suggestion_id}/rate")
def rate_suggestion(suggestion_id: int, request: Request, rating: str = Form(""),
                    session: Session = Depends(get_session)):
    """Rating a card saves it first (ratings live on saved recipes)."""
    value = _rating(rating)
    suggestion = _suggestion_or_404(session, suggestion_id)
    saved.rate(session, saved.save_suggestion(session, suggestion), value)
    return card_actions_response(request, session, suggestion)
