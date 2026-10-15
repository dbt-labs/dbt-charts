"""``style.number_format`` / ``style.time_format`` retire onto the axis slots.

``number_format`` fed the measure axis (``axis_y``, whatever the orientation) and
moves there. Both old keys applied only on a numeric or date channel, which the
YAML cannot say, so the migration moves them unconditionally and a format that
does not fit the axis raises at render. A chart-local axis format the move would
override is refused.
``style.charts.<family>.*_format`` validated but never applied, so it is deleted.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.compile.migrations import (
    MigrationConflictError,
    SchemaMigrationWarning,
    migrate_mapping,
)
from dbt_charts.core.compile.migrations.migrations import (
    _board_migration_context,
    prepare_board_mapping,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.board.patch import BoardPatch
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
)

_MEASURE_FAMILIES = ["bar", "line", "area", "histogram"]
_TIME_FAMILIES = ["bar", "line", "area", "histogram", "scatter"]


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _board(chart_type: str, style: dict[str, Any]) -> dict[str, Any]:
    return {
        "charts": {
            "c1": {
                "type": chart_type,
                "query": "q1",
                "x": "month",
                "y": "revenue",
                "style": style,
            }
        },
        "rows": ["c1"],
    }


def _migrate(raw: dict[str, Any], catalog: YamlSchemaCatalog) -> dict[str, Any]:
    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw, catalog=catalog, registry=registry)


@pytest.mark.parametrize("family", _MEASURE_FAMILIES)
def test_number_format_moves_onto_the_measure_axis(
    family: str, catalog: YamlSchemaCatalog
) -> None:
    migrated = _migrate(_board(family, {"number_format": "$,.0f"}), catalog)

    style = migrated["charts"]["c1"]["style"]
    assert "number_format" not in style
    assert style["axis_y"]["labels"]["format"] == "$,.0f"
    AuthoredBoard.model_validate(migrated)


@pytest.mark.parametrize("family", _TIME_FAMILIES)
def test_time_format_moves_onto_the_x_axis(
    family: str, catalog: YamlSchemaCatalog
) -> None:
    """A time pattern only means something on a date axis, so the intent is clear;
    a non-date x is caught at render."""
    migrated = _migrate(_board(family, {"time_format": "%b %Y"}), catalog)

    style = migrated["charts"]["c1"]["style"]
    assert "time_format" not in style
    assert style["axis_x"]["labels"]["format"] == "%b %Y"
    AuthoredBoard.model_validate(migrated)


def test_scatter_number_format_moves_onto_y(catalog: YamlSchemaCatalog) -> None:
    migrated = _migrate(_board("scatter", {"number_format": "currency"}), catalog)

    style = migrated["charts"]["c1"]["style"]
    assert "number_format" not in style
    assert style["axis_y"]["labels"]["format"] == "currency"
    AuthoredBoard.model_validate(migrated)


@pytest.mark.parametrize("competitor", ["axis", "axis_quantitative"])
def test_a_chart_local_axis_format_conflicts_with_number_format(
    competitor: str, catalog: YamlSchemaCatalog
) -> None:
    """Those layers beat the old key; moving it above them would override them."""
    _, registry = _board_migration_context()
    raw = _board(
        "bar", {"number_format": "currency", competitor: {"labels": {"format": "%"}}}
    )

    with pytest.raises(MigrationConflictError) as raised:
        migrate_mapping(raw, catalog=catalog, registry=registry)

    assert "number_format" in str(raised.value)
    assert competitor in str(raised.value)


def test_a_format_block_moves_whole(catalog: YamlSchemaCatalog) -> None:
    block = {"spec": "integer", "prefix": "$"}

    migrated = _migrate(_board("bar", {"number_format": block}), catalog)

    assert migrated["charts"]["c1"]["style"]["axis_y"]["labels"]["format"] == block


def test_sibling_axis_keys_survive(catalog: YamlSchemaCatalog) -> None:
    style = {
        "number_format": "$,.0f",
        "axis_y": {"labels": {"visible": False}},
        "axis_x": {"title": {"visible": False}},
    }

    migrated = _migrate(_board("line", style), catalog)

    moved = migrated["charts"]["c1"]["style"]
    assert moved["axis_y"]["labels"] == {"visible": False, "format": "$,.0f"}
    assert moved["axis_x"] == {"title": {"visible": False}}


def test_nested_chart_in_a_row_migrates(catalog: YamlSchemaCatalog) -> None:
    raw = _board("bar", {"number_format": "percent"})
    raw["rows"] = [{"cols": ["c1"]}]

    migrated = _migrate(raw, catalog)

    assert migrated["charts"]["c1"]["style"]["axis_y"]["labels"]["format"] == "percent"


@pytest.mark.parametrize("key", ["number_format", "time_format"])
def test_heatmap_format_key_is_deleted_with_a_warning(
    key: str, catalog: YamlSchemaCatalog
) -> None:
    """A heatmap's axes are both grid dimensions, so neither key ever applied."""
    _, registry = _board_migration_context()
    raw = _board("heatmap", {key: "$,.0f"})

    with pytest.warns(SchemaMigrationWarning, match=key):
        migrated = migrate_mapping(raw, catalog=catalog, registry=registry)

    assert not migrated["charts"]["c1"].get("style")
    AuthoredBoard.model_validate(migrated)


@pytest.mark.parametrize(
    ("key", "slot"),
    [("number_format", "axis_y"), ("time_format", "axis_x")],
)
def test_a_slot_that_already_has_a_format_conflicts(
    key: str, slot: str, catalog: YamlSchemaCatalog
) -> None:
    _, registry = _board_migration_context()
    raw = _board("bar", {key: "%b", slot: {"labels": {"format": ",.0f"}}})

    with pytest.raises(MigrationConflictError) as raised:
        migrate_mapping(raw, catalog=catalog, registry=registry)

    message = str(raised.value)
    assert key in message
    assert f"{slot}.labels.format" in message


@pytest.mark.parametrize("key", ["number_format", "time_format"])
def test_theme_family_key_is_deleted_with_a_warning(
    key: str, catalog: YamlSchemaCatalog
) -> None:
    """`style.charts.bar.number_format` validated but changed nothing."""
    raw: dict[str, Any] = {"style": {"charts": {"bar": {key: "$,.0f"}}}}

    with pytest.warns(SchemaMigrationWarning, match=key):
        prepared = prepare_board_mapping(raw, model=BoardPatch)

    BoardPatch.model_validate(prepared)
    assert "number_format" not in str(prepared)
    assert "time_format" not in str(prepared)


def test_a_migrated_board_compiles_with_the_format_on_the_axis() -> None:
    """The in-memory migration keeps a board written with the key rendering."""
    yaml_text = (
        "queries:\n"
        "  q1:\n"
        "    type: values\n"
        "    columns: [month, revenue]\n"
        "    values:\n"
        "      - [a, 3]\n"
        "charts:\n"
        "  c1:\n"
        "    type: bar\n"
        "    query: q1\n"
        "    x: month\n"
        "    y: revenue\n"
        "    style:\n"
        "      number_format: currency\n"
        "rows: [c1]\n"
    )

    result = compile_board(yaml_text)

    assert result.success, result.errors
    assert [d for d in result.warnings if d.code == "WARN-SCHEMA-MIGRATED"]
    chart = result.board.charts["c1"]
    assert chart.style is not None
    assert chart.style.axis_y.labels.format == "currency"


def test_current_grammar_rejects_the_retired_keys() -> None:
    with pytest.raises(ValidationError, match="number_format"):
        AuthoredBoard.model_validate(_board("bar", {"number_format": "$,.0f"}))
