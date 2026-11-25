"""Regression: dct migrate rejects every board using an authoring shorthand.

A board authored with a shorthand (bare SQL, bare cross-file import string, or
`{ref: ...}` mapping) for `queries:`, `charts:`, or `variables:` is rejected by
BOTH migration paths whenever it also carries retired grammar — the projected
JSON Schema the migrator gates on (`schema/introspection.py` ->
`renderers/json_schema.py`) is derived from field annotations and, before this
fix, never saw the `mode="before"` coercions Pydantic itself accepts
(`normalize_query_value`, the `CrossFileRef.coerce_string` validators). A board
using *only* a shorthand, with no retired grammar, already validates against
the current schema and never reaches the migrator at all — every fixture here
pairs the shorthand under test with one retired-but-migratable field
(`style.axis_y.format`, the 0.3.1 flat spelling of what 0.4.0+ nests under
`style.axis_y.labels.format` — see test_axis_reshape_migration.py) so the
in-memory case fails before the fix instead of passing vacuously.
"""

from __future__ import annotations

import warnings

import pytest

from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    UnsupportedSchemaError,
    migrate_board_yaml_text,
    prepare_board_mapping,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.parse.parser import load_yaml_mapping

_RETIRED_AXIS_Y_CHART = """\
  revenue:
    type: bar
    query: q
    x: month
    y: amount
    style:
      axis_y:
        format: currency_whole
"""


def _assert_axis_y_migrated(mapping: dict) -> None:
    axis_y = mapping["charts"]["revenue"]["style"]["axis_y"]
    assert axis_y == {"labels": {"format": "currency_whole"}}


QUERY_SHAPES = [
    pytest.param("SELECT 1 AS n", id="bare-sql"),
    pytest.param("other.yaml.queries.a", id="bare-import"),
    pytest.param("{ref: other.yaml.queries.a}", id="ref-mapping"),
]
CHART_SHAPES = [
    pytest.param("other.yaml.charts.a", id="bare-import"),
    pytest.param("{ref: other.yaml.charts.a}", id="ref-mapping"),
]
VARIABLE_SHAPES = [
    pytest.param("other.yaml.variables.a", id="bare-import"),
    pytest.param("{ref: other.yaml.variables.a}", id="ref-mapping"),
]


def _queries_board(shape: str) -> str:
    return (
        "title: t\n"
        "queries:\n"
        f"  a: {shape}\n"
        "charts:\n" + _RETIRED_AXIS_Y_CHART + "rows:\n  - revenue\n"
    )


def _charts_board(shape: str) -> str:
    return (
        "title: t\n"
        "queries:\n"
        "  q: SELECT 1 AS n\n"
        "charts:\n"
        f"  a: {shape}\n" + _RETIRED_AXIS_Y_CHART + "rows:\n  - revenue\n"
    )


def _variables_board(shape: str) -> str:
    return (
        "title: t\n"
        "queries:\n"
        "  q: SELECT 1 AS n\n"
        "variables:\n"
        f"  a: {shape}\n"
        "charts:\n" + _RETIRED_AXIS_Y_CHART + "rows:\n  - revenue\n"
    )


@pytest.mark.parametrize("shape", QUERY_SHAPES)
def test_queries_shorthand_migrates_via_yaml_text(shape: str) -> None:
    yaml_text = _queries_board(shape)

    migrated = migrate_board_yaml_text(yaml_text).text

    doc = load_yaml_mapping(migrated)
    _assert_axis_y_migrated(doc)
    # The shorthand is never rewritten into its coerced form.
    assert doc["queries"]["a"] == load_yaml_mapping(f"a: {shape}\n")["a"]
    AuthoredBoard.model_validate(doc)


@pytest.mark.parametrize("shape", QUERY_SHAPES)
def test_queries_shorthand_migrates_in_memory(shape: str) -> None:
    mapping = load_yaml_mapping(_queries_board(shape))
    original_value = mapping["queries"]["a"]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = prepare_board_mapping(mapping)

    messages = [str(w.message) for w in caught if w.category is SchemaMigrationWarning]
    assert not any("could not finish migrating" in m for m in messages), messages
    assert any("migrated this YAML in memory" in m for m in messages), messages
    _assert_axis_y_migrated(result)
    assert result["queries"]["a"] == original_value
    AuthoredBoard.model_validate(result)


@pytest.mark.parametrize("shape", CHART_SHAPES)
def test_charts_shorthand_migrates_via_yaml_text(shape: str) -> None:
    yaml_text = _charts_board(shape)

    migrated = migrate_board_yaml_text(yaml_text).text

    doc = load_yaml_mapping(migrated)
    _assert_axis_y_migrated(doc)
    assert doc["charts"]["a"] == load_yaml_mapping(f"a: {shape}\n")["a"]
    AuthoredBoard.model_validate(doc)


@pytest.mark.parametrize("shape", CHART_SHAPES)
def test_charts_shorthand_migrates_in_memory(shape: str) -> None:
    mapping = load_yaml_mapping(_charts_board(shape))
    original_value = mapping["charts"]["a"]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = prepare_board_mapping(mapping)

    messages = [str(w.message) for w in caught if w.category is SchemaMigrationWarning]
    assert not any("could not finish migrating" in m for m in messages), messages
    assert any("migrated this YAML in memory" in m for m in messages), messages
    _assert_axis_y_migrated(result)
    assert result["charts"]["a"] == original_value
    AuthoredBoard.model_validate(result)


@pytest.mark.parametrize("shape", VARIABLE_SHAPES)
def test_variables_shorthand_migrates_via_yaml_text(shape: str) -> None:
    yaml_text = _variables_board(shape)

    migrated = migrate_board_yaml_text(yaml_text).text

    doc = load_yaml_mapping(migrated)
    _assert_axis_y_migrated(doc)
    assert doc["variables"]["a"] == load_yaml_mapping(f"a: {shape}\n")["a"]
    AuthoredBoard.model_validate(doc)


@pytest.mark.parametrize("shape", VARIABLE_SHAPES)
def test_variables_shorthand_migrates_in_memory(shape: str) -> None:
    mapping = load_yaml_mapping(_variables_board(shape))
    original_value = mapping["variables"]["a"]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = prepare_board_mapping(mapping)

    messages = [str(w.message) for w in caught if w.category is SchemaMigrationWarning]
    assert not any("could not finish migrating" in m for m in messages), messages
    assert any("migrated this YAML in memory" in m for m in messages), messages
    _assert_axis_y_migrated(result)
    assert result["variables"]["a"] == original_value
    AuthoredBoard.model_validate(result)


def test_invalid_variable_ref_still_fails_at_parse_time_with_grammar_message() -> None:
    """A malformed cross-file ref fails via refs.py's grammar `field_validator`,
    not later in normalize — the coercion moved to the annotation, but the
    grammar check still fires at the type boundary (refs.py's own docstring)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="Invalid variable reference"):
        AuthoredBoard.model_validate(
            {"title": "t", "variables": {"a": "shared.yml.variables."}}
        )


def test_invalid_chart_ref_still_fails_at_parse_time_with_grammar_message() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="Invalid chart reference"):
        AuthoredBoard.model_validate(
            {"title": "t", "charts": {"a": "shared.yml.charts."}}
        )


def test_genuine_hand_migration_still_raises_alongside_a_shorthand() -> None:
    """A shorthand paired with a genuinely unmigratable retired construct
    (categorical_orient was deleted, not renamed — test_axis_reshape_migration.py's
    test_deleted_field_is_not_silently_migrated) must still fail loudly. The fix
    widens what the schema gate *accepts*; it must not blanket-silence a real
    IncompleteMigrationError/UnsupportedSchemaError."""
    yaml_text = (
        "title: t\n"
        "queries:\n"
        "  a: SELECT 1 AS n\n"
        "charts:\n"
        "  revenue:\n"
        "    type: bar\n"
        "    query: a\n"
        "    x: month\n"
        "    y: amount\n"
        "    style:\n"
        "      axis_y:\n"
        "        categorical_orient: left\n"
        "rows:\n"
        "  - revenue\n"
    )

    with pytest.raises(UnsupportedSchemaError):
        migrate_board_yaml_text(yaml_text)
