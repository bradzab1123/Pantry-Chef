"""Talking to Claude: the client, prompt templates, structured output, retries,
the cost guard and usage logging (SPEC.md §3).

What happens on one request_recipes() call:
1. Send the prompt with `output_config.format` set to the RecipeResponse JSON
   schema, so the API only returns JSON in that shape.
2. Validate the text with Pydantic. If it doesn't validate (or the model
   refused, or ran out of tokens), try once more, then give up with a
   friendly GenerationError.
3. Every API call, good or bad, gets a row in generation_log.

The `never` filter is NOT here; app/cook.py runs it on every recipe this
returns. Tests replace get_client() with a fake, so they never reach the API.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import anthropic
import pydantic
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy import func
from sqlmodel import Session, select

from app import config
from app.models import GenerationLog, utcnow
from app.schemas import RecipeResponse
from app.web import APP_DIR

log = logging.getLogger(__name__)

# Room for adaptive thinking plus up to 6 recipes. Non-streaming requests
# should stay at or below ~16K so they finish well inside HTTP timeouts.
MAX_TOKENS = 16000

# The JSON schema sent to the API. transform_schema() is the SDK's helper that
# reshapes a Pydantic schema into what structured outputs accept.
RESPONSE_SCHEMA = anthropic.transform_schema(RecipeResponse)

# StrictUndefined: a typo in a template variable fails loudly instead of
# silently printing nothing. trim_blocks/lstrip_blocks keep {% %} lines from
# leaving blank lines behind.
prompts = Environment(
    loader=FileSystemLoader(APP_DIR / "prompts"),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


class GenerationError(Exception):
    """Something went wrong; str(error) is a message fit to show in the app."""


def render_prompt(name: str, **context) -> str:
    return prompts.get_template(name).render(**context)


def system_prompt() -> str:
    return render_prompt("system.txt")


# ---------- Client ----------

_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    """The shared Anthropic client, created on first use.

    timeout: Cloudflare gives up on a request after 100 s, so don't wait
    forever. max_retries=1: the SDK retries rate limits, 5xx errors and
    dropped connections once by itself.
    """
    global _client
    if _client is None:
        _client = anthropic.Anthropic(
            api_key=config.ANTHROPIC_API_KEY, timeout=60.0, max_retries=1
        )
    return _client


# ---------- Cost guard ----------


def calls_last_hour(session: Session) -> int:
    since = utcnow() - timedelta(hours=1)
    query = select(func.count()).select_from(GenerationLog).where(GenerationLog.created_at >= since)
    return session.exec(query).one()


def check_cost_guard(session: Session) -> None:
    """Refuse to start a generation once the hourly limit is reached.

    Counts every API call (retries and regenerations too), since that's what
    costs money. Checked once per tap of Generate, so a generation that has
    already started can finish its retry.
    """
    limit = config.MAX_GENERATIONS_PER_HOUR
    if calls_last_hour(session) >= limit:
        raise GenerationError(
            f"That's the limit of {limit} recipe requests an hour (it keeps the API bill "
            "in check). Try again a bit later."
        )


# ---------- Calling the API ----------


def _log_call(session: Session, kind: str, success: bool, usage=None) -> None:
    session.add(GenerationLog(
        kind=kind,
        model=config.CLAUDE_MODEL,
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        success=success,
    ))
    # Commit right away so the call is counted even if a later step fails.
    session.commit()


def _friendly_message(error: anthropic.APIError) -> str:
    """Turn an SDK exception into something readable. Most specific first:
    APITimeoutError is a kind of APIConnectionError, for example."""
    if isinstance(error, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return "The Anthropic API key was rejected. Check ANTHROPIC_API_KEY in .env."
    if isinstance(error, anthropic.NotFoundError):
        return f"Anthropic doesn't know the model “{config.CLAUDE_MODEL}”. Check CLAUDE_MODEL in .env."
    if isinstance(error, anthropic.RateLimitError):
        return "Anthropic is rate-limiting us right now. Try again in a minute."
    if isinstance(error, anthropic.APITimeoutError):
        return "The recipe service took too long to answer. Try again."
    if isinstance(error, anthropic.APIConnectionError):
        return "Couldn't reach the recipe service. Check the server's internet connection."
    if isinstance(error, anthropic.APIStatusError) and error.status_code >= 500:
        return "The recipe service is having trouble. Try again shortly."
    return "The recipe service rejected the request. Try again, and check the server logs if it keeps happening."


def _parse(response) -> RecipeResponse | None:
    """The validated recipes, or None if this response can't be used."""
    if response.stop_reason != "end_turn":
        # "refusal" (a safety decline) or "max_tokens" (cut off mid-JSON).
        log.warning("Unusable response: stop_reason=%s", response.stop_reason)
        return None
    # Read blocks by type: the response may start with "thinking" blocks.
    text = "".join(block.text for block in response.content if block.type == "text")
    try:
        return RecipeResponse.model_validate_json(text)
    except pydantic.ValidationError as error:
        log.warning("Response didn't match the recipe schema: %s", error)
        return None


def _call_once(session: Session, kind: str, user_prompt: str) -> RecipeResponse | None:
    try:
        response = get_client().messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt(),
            messages=[{"role": "user", "content": user_prompt}],
            output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
        )
    except anthropic.APIError as error:
        log.warning("Anthropic API error: %r", error)
        _log_call(session, kind, success=False)
        raise GenerationError(_friendly_message(error)) from error

    parsed = _parse(response)
    _log_call(session, kind, success=parsed is not None, usage=response.usage)
    return parsed


def request_recipes(session: Session, kind: str, user_prompt: str) -> RecipeResponse:
    """Ask for recipes; validated, with one retry if the reply is unusable.

    `kind` is "generate" or "spinoff" (for the log). Raises GenerationError.
    """
    if not config.ANTHROPIC_API_KEY:
        raise GenerationError("ANTHROPIC_API_KEY isn't set, so recipes can't be generated. Add it to .env.")
    for _attempt in range(2):
        result = _call_once(session, kind, user_prompt)
        if result is not None:
            return result
    raise GenerationError("The recipe service sent back something unusable twice in a row. Try again.")


# ---------- Usage (Settings page) ----------


@dataclass
class Usage:
    calls: int
    failed: int
    input_tokens: int
    output_tokens: int

    @property
    def cost(self) -> float:
        """Estimated US$, from the per-million-token prices in .env."""
        return (self.input_tokens * config.PRICE_INPUT_PER_MTOK
                + self.output_tokens * config.PRICE_OUTPUT_PER_MTOK) / 1_000_000


def month_start_utc() -> datetime:
    """Midnight on the 1st of this month in local time (TZ), as UTC.

    A naive datetime's .astimezone() treats it as local time, with the right
    daylight-saving offset for that date.
    """
    now = datetime.now()
    return datetime(now.year, now.month, 1).astimezone().astimezone(timezone.utc)


def usage_this_month(session: Session) -> Usage:
    rows = session.exec(
        select(GenerationLog).where(GenerationLog.created_at >= month_start_utc())
    ).all()
    return Usage(
        calls=len(rows),
        failed=sum(1 for r in rows if not r.success),
        input_tokens=sum(r.input_tokens for r in rows),
        output_tokens=sum(r.output_tokens for r in rows),
    )
