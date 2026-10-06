"""Board-resolved JSON Schema renderer — the resolved-board artifact schema.

Emits the JSON Schema for the publishable artifact envelope
(``board_artifact.dump_resolved_board_artifact``): ``{$id, version, styles,
board}``, where ``board`` is a ``ResolvedBoard`` (the frozen render contract
produced by ``build_resolved_board`` / ``build_resolved_board_static``) with
every ``style`` occurrence replaced by a ``$style_ref`` pointer into
``styles``. Unlike the authored-side board schema (``json_schema.py``, built
from the ``AuthorableSchema`` IR), ``ResolvedBoard`` is never hand-authored by
users, so its schema is the direct Pydantic/dataclass introspection — no IR
layer needed.

The envelope shape has no Python type of its own — hoisting is a
serialization-only transform (see ``board_artifact.py``) that never touches
``ResolvedBoard``/``ResolvedStyle`` — so this renderer post-processes the
Pydantic-generated ``ResolvedBoard`` schema: it swaps the one ``style``
property in ``$defs.ResolvedBoard`` for a ``$defs.StyleRef`` pointer (the
``$defs`` entry is recursive, so this single edit covers the root board and
every nested board) and wraps the result in the envelope object.

Entry point: ``render_board_resolved_schema() -> JsonSchemaValue``
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic.json_schema import JsonSchemaValue

from dbt_charts.core.compile.board_artifact import SCHEMA_ID
from dbt_charts.core.compile.models.board.resolved import ResolvedBoard


# Schema-only: board_artifact.hoist_styles/inline_styles build and consume the
# equivalent {"$style_ref": <hash>} dict directly, never a StyleRef instance.
# This class exists so the shape below is Pydantic-introspected like every
# other $defs entry, rather than hand-authored raw JSON Schema.
class StyleRef(BaseModel):
    """A content-addressed pointer into the artifact's `styles` table,
    replacing an inline style wherever `ResolvedBoard.style` would otherwise
    appear. Resolve it by looking up `$style_ref` in the artifact's `styles`
    map."""

    model_config = ConfigDict(extra="forbid")

    style_ref: str = Field(
        alias="$style_ref",
        description="SHA-256 hash of the referenced style's canonical JSON — a key into the artifact's `styles` table.",
    )


def render_board_resolved_schema() -> JsonSchemaValue:
    """Render the resolved-board artifact JSON Schema (draft 2020-12).

    Returns:
        A self-contained JSON Schema dict describing the envelope
        ``{$id, version, styles, board}``: draft-2020-12 ``$schema``, a
        stable ``$id``, and a Pydantic-derived ``$defs`` body with
        ``ResolvedBoard.style`` rewritten to a ``StyleRef`` pointer. Not
        yet canonicalized for cross-Python-version stability; the
        generation script applies that pass before writing the committed
        artifact.
    """
    defs = TypeAdapter(ResolvedBoard).json_schema(mode="serialization")["$defs"]
    defs["StyleRef"] = TypeAdapter(StyleRef).json_schema(mode="serialization")
    defs["ResolvedBoard"]["properties"]["style"] = {"$ref": "#/$defs/StyleRef"}

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "type": "object",
        "additionalProperties": False,
        "required": ["$id", "version", "styles", "board"],
        "properties": {
            "$id": {
                "type": "string",
                "description": "URI of the schema this artifact conforms to.",
            },
            "version": {
                "type": "string",
                "description": "The dbt-charts release that produced this artifact.",
            },
            "styles": {
                "type": "object",
                "additionalProperties": {"$ref": "#/$defs/ResolvedStyle"},
                "description": (
                    "Content-addressed style table, keyed by the SHA-256 hash "
                    "of each style's canonical JSON. Every `style` field "
                    "inside `board` is a $style_ref pointer into this table "
                    "instead of an inline style."
                ),
            },
            "board": {"$ref": "#/$defs/ResolvedBoard"},
        },
        "$defs": defs,
    }
