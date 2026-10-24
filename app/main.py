"""FastAPI app: wires up the routers, plus /health and the service worker."""

import mimetypes
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlmodel import Session

from app.db import get_engine, migrate
from app.routers import cook, cooked, pantry, saved, settings, setup, shopping
from app.web import APP_DIR, TABS, redirect  # noqa: F401  (TABS is imported by tests)

STATIC_DIR = APP_DIR / "static"

# Python's mimetypes table doesn't know .webmanifest; browsers want this type.
mimetypes.add_type("application/manifest+json", ".webmanifest")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Bring the database schema up to date before serving any requests.
    migrate()
    yield


app = FastAPI(title="Pantry Chef", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(cook.router)
app.include_router(cooked.router)
app.include_router(pantry.router)
app.include_router(saved.router)
app.include_router(settings.router)
app.include_router(setup.router)
app.include_router(shopping.router)

# Paths that work before setup: the setup page itself, and what the browser
# and Docker fetch on their own.
SETUP_EXEMPT = ("/setup", "/health", "/sw.js", "/static/")


@app.middleware("http")
async def require_setup(request: Request, call_next):
    """On a new install, send every page to /setup until both people are named."""
    path = request.url.path
    if request.method == "GET" and not path.startswith(SETUP_EXEMPT):
        with Session(get_engine()) as session:
            if setup.needs_setup(session):
                return redirect("/setup")
    return await call_next(request)


# ---------- Infrastructure ----------

@app.get("/health")
def health():
    """Used by the Docker healthcheck. Checks that the database answers."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "error"}, status_code=503)
    return {"status": "ok"}


@app.get("/sw.js")
def service_worker():
    # Served from the site root (not /static/) so the worker's scope is the
    # whole app. no-cache makes the browser check for a new worker each load.
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )
