"""Shared web bits used by main.py and the routers: templates, tab bar, render helpers."""

import hashlib
from pathlib import Path

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.schemas import strip_step_number

APP_DIR = Path(__file__).resolve().parent


def static_version() -> str:
    """A short fingerprint of the CSS and JS, added to their URLs (?v=...).

    The service worker serves /static/ files from its cache first, so after
    an update the browser would show the OLD stylesheet once. A new URL is a
    cache miss, so a changed file is fetched straight away.
    """
    digest = hashlib.sha256()
    for name in ("style.css", "htmx.min.js"):
        digest.update((APP_DIR / "static" / name).read_bytes())
    return digest.hexdigest()[:10]


templates = Jinja2Templates(directory=APP_DIR / "templates")
templates.env.filters["strip_step_number"] = strip_step_number
# Worked out once at startup: the files only change with a new image.
templates.env.globals["static_version"] = static_version()

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
