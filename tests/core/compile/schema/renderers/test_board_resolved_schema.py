"""Shape tests for the board-artifact JSON Schema (render_board_resolved_schema)."""

from __future__ import annotations

from pydantic.json_schema import JsonSchemaValue

from dbt_charts.core.compile.schema.renderers.board_resolved import (
    render_board_resolved_schema,
)


def _schema() -> JsonSchemaValue:
    return render_board_resolved_schema()


def test_board_resolved_schema_has_expected_metadata() -> None:
    schema = _schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "https://dbtcharts.com/schemas/board-resolved.schema.json"
    assert "version" not in schema
    assert "$defs" in schema
    assert sorted(schema["required"]) == ["$id", "board", "styles", "version"]
    assert schema["properties"]["board"] == {"$ref": "#/$defs/ResolvedBoard"}


def test_board_resolved_schema_hoists_style_into_a_ref() -> None:
    """ResolvedBoard.style is a $style_ref pointer, not an inline ResolvedStyle —
    dbt-charts/AGENTS.md's dedupe rationale: one style per artifact, not one per
    board. The $defs entry is recursive, so this single edit covers nested
    boards too, without walking the tree by hand."""
    schema = _schema()
    assert schema["$defs"]["ResolvedBoard"]["properties"]["style"] == {
        "$ref": "#/$defs/StyleRef"
    }
    assert "$style_ref" in schema["$defs"]["StyleRef"]["properties"]
    assert schema["properties"]["styles"]["additionalProperties"] == {
        "$ref": "#/$defs/ResolvedStyle"
    }


def test_board_resolved_schema_query_defs_are_discriminated() -> None:
    """AnyQuery's discriminator must survive into the published schema as a
    query_type-tagged shape, not a bare untagged oneOf/anyOf."""
    defs = _schema()["$defs"]
    for name in ("SqlQuery", "HttpQuery", "ValuesQuery", "SchemaQuery"):
        assert name in defs, f"{name} missing from $defs"
        assert "query_type" in defs[name]["properties"]
