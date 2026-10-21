"""Cook page: the generate form, restriction chips, and recipe results."""

from fastapi import APIRouter, Depends, Form, Request
from sqlmodel import Session

from app import cook, llm, saved, shopping
from app.db import get_session
from app.web import is_htmx, redirect, render, render_partial

router = APIRouter()


def page_context(session: Session, req: cook.CookRequest) -> dict:
    """Everything cook.html needs (form, chips and the latest results)."""
    suggestions = cook.latest_suggestions(session)
    return {
        "people": cook.all_people(session),
        "form": req,
        "meal_types": cook.MEAL_TYPES,
        "max_times": cook.MAX_TIMES,
        "max_count": cook.MAX_COUNT,
        "max_servings": cook.MAX_SERVINGS,
        "suggestions": suggestions,
        "saved": saved.saved_for(session, suggestions),
        "in_plan": shopping.in_plan(session, suggestions),
        "notes": [],
        "error": "",
        **cook.chips(session, cook.eating(session, req.eaters)),
    }


@router.get("/")
def cook_page(request: Request, session: Session = Depends(get_session)):
    return render(request, "cook.html", "cook", title="Cook",
                  **page_context(session, cook.CookRequest()))


@router.get("/cook/chips")
def restriction_chips(request: Request, eaters: str = "both", session: Session = Depends(get_session)):
    """Swapped in when "who's eating" changes. All avoid chips start switched on."""
    return render_partial(request, "partials/cook_chips.html",
                          avoid_on=None, **cook.chips(session, cook.eating(session, eaters)))


@router.post("/cook/generate")
def generate(
    request: Request,
    eaters: str = Form("both"),
    avoid: list[str] = Form([]),
    skip: str = Form(""),
    meal_type: str = Form("any"),
    max_time: str = Form("any"),
    servings: str = Form(""),
    cuisine: str = Form(""),
    count: str = Form(""),
    session: Session = Depends(get_session),
):
    req = cook.parse_form(eaters, avoid, skip, meal_type, max_time, servings, cuisine, count)
    try:
        result = cook.generate(session, req)
    except llm.GenerationError as error:
        # Shown in place of the results; the previous batch stays below it.
        context = page_context(session, req)
        context["error"] = str(error)
        if is_htmx(request):
            return render_partial(request, "partials/cook_results.html", **context)
        return render(request, "cook.html", "cook", title="Cook", **context)

    if not is_htmx(request):
        return redirect("/")
    return render_partial(request, "partials/cook_results.html",
                          suggestions=result.suggestions, saved={},
                          in_plan=shopping.in_plan(session, result.suggestions),
                          notes=result.notes, error="")
