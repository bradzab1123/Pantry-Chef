"""The "Cooked it" button on recipe cards, saved recipes and planned recipes.

Tapping it swaps in a small form (GET); submitting the form applies the
pantry changes (POST). Without JavaScript the GET is a page of its own and
the POST redirects back to where the recipe is shown.
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlmodel import Session

from app import cooked, shopping
from app.db import get_session
from app.web import is_htmx, redirect, render, render_partial

router = APIRouter()


def _source_or_404(session: Session, kind: str, recipe_id: int) -> cooked.Source:
    if kind not in cooked.KINDS:
        raise HTTPException(404, "Unknown recipe")
    try:
        return cooked.find_recipe(session, kind, recipe_id)
    except cooked.NotFound:
        raise HTTPException(404, "That recipe isn't here any more. Reload the page.")


def _tab(source: cooked.Source) -> str:
    """Which tab to highlight on the no-JavaScript page."""
    if source.kind == "plan":
        return "list"
    return "cook" if source.back_url == "/" else "saved"


def _ids(values: list[str]) -> list[int]:
    """Posted pantry ids; anything that isn't a number is ignored."""
    return [int(v) for v in values if v.strip().isdigit()]


@router.get("/cooked/{kind}/{recipe_id}")
def cooked_form(kind: str, recipe_id: int, request: Request, session: Session = Depends(get_session)):
    source = _source_or_404(session, kind, recipe_id)
    context = {"source": source, "items": cooked.used_up_candidates(session, source.recipe_json)}
    if is_htmx(request):
        return render_partial(request, "partials/cooked_form.html", **context)
    return render(request, "cooked.html", _tab(source), title="Cooked it",
                  recipe=source.recipe_json, **context)


@router.get("/cooked/{kind}/{recipe_id}/cancel")
def cooked_cancel(kind: str, recipe_id: int, request: Request, session: Session = Depends(get_session)):
    source = _source_or_404(session, kind, recipe_id)
    if not is_htmx(request):
        return redirect(source.back_url)
    return render_partial(request, "partials/cooked_button.html", cooked_url=source.url)


@router.post("/cooked/{kind}/{recipe_id}")
def cooked_it(
    kind: str,
    recipe_id: int,
    request: Request,
    used: list[str] = Form([]),
    to_list: str = Form(""),  # "1" when "Put used-up items on the shopping list" is ticked
    session: Session = Depends(get_session),
):
    # The recipe comes from the database by id, never from the form.
    source = _source_or_404(session, kind, recipe_id)
    result = cooked.cook_it(session, source.recipe_json, _ids(used), to_list == "1")

    if not is_htmx(request):
        return redirect(source.back_url)
    if source.kind == "plan":
        # The plan row is gone now, so the whole List is redrawn with the
        # message at the top (the used-up items show up in it too).
        return render_partial(request, "partials/list_page.html",
                              **shopping.list_context(session, result.message()))
    response = render_partial(request, "partials/cooked_done.html", message=result.message())
    # Lets the Cook page's pantry check refresh its count.
    response.headers["HX-Trigger"] = "pantry-changed"
    return response
