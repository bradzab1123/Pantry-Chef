"""App settings, read from environment variables.

Everything has a sensible default so the app runs locally without a .env file.
In Docker, docker-compose loads these from .env (see .env.example).
"""

import os
from pathlib import Path

# The project root is the folder that contains the `app/` package.
# In the container that's /app, so the default DB lands at /app/data/pantry.db.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def db_path() -> Path:
    """Where the SQLite file lives. A function (not a constant) so tests can
    point DB_PATH at a temp folder before the engine is created."""
    return Path(os.environ.get("DB_PATH", PROJECT_ROOT / "data" / "pantry.db"))


ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")
MAX_GENERATIONS_PER_HOUR = int(os.environ.get("MAX_GENERATIONS_PER_HOUR", "30"))

# US dollars per million tokens, used for the cost estimate in Settings.
PRICE_INPUT_PER_MTOK = float(os.environ.get("PRICE_INPUT_PER_MTOK", "0.10"))
PRICE_OUTPUT_PER_MTOK = float(os.environ.get("PRICE_OUTPUT_PER_MTOK", "0.50"))
