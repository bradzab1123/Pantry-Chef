"""Shared web bits used by main.py and the routers: templates, tab bar, render helpers."""

from pathlib import Path

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=APP_DIR / "templates")

# Bottom tab bar, in display order: (key, label, url).
TABS = [
    ("cook", "Cook", "/"),
    ("pantry", "Pantry", "/pantry"),
    ("saved", "Saved", "/saved"),
    ("list", "List", "/list"),
    ("settings", "Settings", "/settings"),
]


def render(request: Request, template: str, active_tab: str, **context):
    """Render a full page with the tab bar, marking `active_tab` as current."""
    return templates.TemplateResponse(
        request,
        template,
        {"tabs": TABS, "active_tab": active_tab, **context},
    )


def render_partial(request: Request, template: str, status_code: int = 200, **context):
    """Render just a piece of a page (an HTMX swap target)."""
    return templates.TemplateResponse(request, template, context, status_code=status_code)


def is_htmx(request: Request) -> bool:
    """True when the request came from HTMX (which sets the HX-Request header).

    Every form also works without JavaScript: a plain form post gets a
    redirect back to the page instead of a partial.
    """
    return request.headers.get("HX-Request") == "true"


def redirect(url: str) -> RedirectResponse:
    # 303 tells the browser to follow up with a GET (the usual post/redirect/get).
    return RedirectResponse(url, status_code=303)
