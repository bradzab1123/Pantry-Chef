"""Database models (SQLModel).

These describe tables that the SQL files in app/migrations/ create; they do
NOT create tables themselves. If you add a column, add a new migration too.
tests/test_skeleton.py checks that the two stay in sync.

Allowed values for the "enum-like" text columns are enforced by CHECK
constraints in the SQL; the constants below are for use in Python code.
"""

from datetime import datetime, timezone

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

LEVELS = ("never", "avoid")
PANTRY_STATUSES = ("have", "buying")
SHOPPING_SOURCES = ("plan", "manual", "buying")
GENERATION_KINDS = ("generate", "spinoff")


def utcnow() -> datetime:
    # SQLModel requires timezone-aware datetimes. On SQLite it saves them as
    # UTC text without an offset (like datetime('now')) and hands them back
    # as aware UTC datetimes when read.
    return datetime.now(timezone.utc)


class Person(SQLModel, table=True):
    __tablename__ = "person"

    id: int | None = Field(default=None, primary_key=True)
    name: str


class Restriction(SQLModel, table=True):
    __tablename__ = "restriction"

    id: int | None = Field(default=None, primary_key=True)
    person_id: int = Field(foreign_key="person.id")
    term: str
    # JSON lists of strings, e.g. ["prawn", "shrimp paste"]
    aliases: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    exceptions: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    level: str  # one of LEVELS
    notes: str | None = None
    active: bool = True


class PantryItem(SQLModel, table=True):
    __tablename__ = "pantry_item"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    normalized_name: str
    status: str  # one of PANTRY_STATUSES
    created_at: datetime = Field(default_factory=utcnow)


class Staple(SQLModel, table=True):
    __tablename__ = "staple"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    enabled: bool = True


class SavedRecipe(SQLModel, table=True):
    __tablename__ = "saved_recipe"

    id: int | None = Field(default=None, primary_key=True)
    title: str
    recipe_json: dict = Field(sa_column=Column(JSON, nullable=False))
    rating: int = 0  # -1 thumbs down, 0 none, 1 thumbs up
    parent_recipe_id: int | None = Field(default=None, foreign_key="saved_recipe.id")
    created_at: datetime = Field(default_factory=utcnow)


class PlanItem(SQLModel, table=True):
    __tablename__ = "plan_item"

    id: int | None = Field(default=None, primary_key=True)
    # Either points at a saved recipe, or holds an unsaved recipe's JSON.
    saved_recipe_id: int | None = Field(default=None, foreign_key="saved_recipe.id")
    recipe_json: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    added_at: datetime = Field(default_factory=utcnow)


class ShoppingItem(SQLModel, table=True):
    __tablename__ = "shopping_item"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    normalized_name: str
    checked: bool = False
    source: str  # one of SHOPPING_SOURCES
    # The pantry row that checking this item created or moved into Have
    # (so unchecking can undo it). None if it was already in Have.
    pantry_item_id: int | None = Field(default=None, foreign_key="pantry_item.id")


class Suggestion(SQLModel, table=True):
    """One recipe from the latest batch (each new batch replaces the old one).

    There's one Cook batch (parent_recipe_id is None) and, per saved recipe,
    one batch of spin-offs (parent_recipe_id is that recipe's id).
    """

    __tablename__ = "suggestion"

    id: int | None = Field(default=None, primary_key=True)
    recipe_json: dict = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)
    parent_recipe_id: int | None = Field(default=None, foreign_key="saved_recipe.id")
    # The saved recipe this card became, once saved or rated.
    saved_recipe_id: int | None = Field(default=None, foreign_key="saved_recipe.id")


class GenerationLog(SQLModel, table=True):
    __tablename__ = "generation_log"

    id: int | None = Field(default=None, primary_key=True)
    kind: str  # one of GENERATION_KINDS
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    success: bool
    created_at: datetime = Field(default_factory=utcnow)
