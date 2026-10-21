"""Pydantic models for what the LLM returns (SPEC.md §3 "Recipe schema").

The JSON schema sent to the API is built from these classes, so the field
descriptions double as instructions to the model.

Notes:
- No field has a default, so every field is "required" in the schema and the
  model always fills it in (with null where None is allowed).
- extra="forbid" becomes `additionalProperties: false`, which structured
  outputs require.
- "At most 2 missing ingredients" is deliberately NOT a Pydantic rule. A
  recipe with 3 would fail validation for the whole batch; instead cook.py
  checks it per recipe and asks for a replacement (like a `never` violation).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_MISSING = 2


class Ingredient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description="Just the ingredient, e.g. 'potatoes' or 'chicken thighs'. No quantity, "
                    "prep or alternatives: 'cut into 1-inch pieces' belongs in the steps."
    )
    quantity: str | None = Field(description="e.g. '2', '1/2', 'a pinch'; null if not needed.")
    unit: str | None = Field(description="US unit, e.g. 'cup', 'tbsp', 'lb'; null if none.")
    source: Literal["pantry", "staple", "buy"] = Field(
        description="'pantry' if on the pantry list, 'staple' if a staple, 'buy' if it must be bought."
    )


class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    summary: str = Field(description="1-2 sentences.")
    cuisine: str | None
    servings: int
    total_time_minutes: int
    difficulty: Literal["easy", "medium", "hard"]
    ingredients: list[Ingredient]
    steps: list[str]
    missing_ingredients: list[str] = Field(
        description=f"The names of the ingredients with source 'buy', exactly as written there. "
                    f"At most {MAX_MISSING}."
    )
    diet_notes: str = Field(description="One line on how the recipe respects the restrictions.")
    variation_note: str | None = Field(
        description="Spin-offs only: what changed vs. the original. Otherwise null."
    )


class RecipeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipes: list[Recipe]
