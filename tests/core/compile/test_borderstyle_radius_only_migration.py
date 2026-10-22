"""Migration coverage for the BorderStyle -> CornerStyle narrowing.

``width``/``color``/``dash_array``/``line_cap``/``dash_offset`` were
schema-valid and inert on four ``border:`` slots (see
``compile/migrations/versions/v0_6_0.py`` for the full list). A board or
custom theme file authoring the old shape migrates clean; only ``radius``
survives.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import migrate_mapping
from dbt_charts.core.compile.migrations.migrations import _board_migration_context
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
)

# (style-patch path segments, sample legacy value dict) for each narrowed slot.
NARROWED_SLOTS: dict[str, list[str]] = {
    "variables.input.border": ["variables", "input", "border"],
    "charts.table.spark.columns.border": [
        "charts",
        "table",
        "spark",
        "columns",
        "border",
    ],
    "charts.table.spark.bar.border": ["charts", "table", "spark", "bar", "border"],
    "charts.spark_bar.border": ["charts", "spark_bar", "border"],
}


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _migrate(raw: dict[str, Any], catalog: YamlSchemaCatalog) -> dict[str, Any]:
    import warnings

    from dbt_charts.core.compile.migrations import SchemaMigrationWarning

    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw, catalog=catalog, registry=registry)


def _board_with_style(style: dict[str, Any]) -> dict[str, Any]:
    return {
        "charts": {
            "c1": {"type": "bar", "query": "q1", "x": "x", "y": "x"},
        },
        "queries": {"q1": {"sql": "SELECT 1 AS x", "source": "test"}},
        "style": style,
    }


def _set_nested(root: dict[str, Any], path: list[str], value: dict[str, Any]) -> None:
    node = root
    for part in path[:-1]:
        node = node.setdefault(part, {})
    node[path[-1]] = value


@pytest.mark.parametrize("slot_path", NARROWED_SLOTS.values(), ids=NARROWED_SLOTS)
def test_inert_border_fields_stripped_from_authored_board(
    slot_path: list[str], catalog: YamlSchemaCatalog
) -> None:
    style: dict[str, Any] = {}
    _set_nested(
        style,
        slot_path,
        {"width": 2.0, "color": "red", "dash_array": [4, 4], "radius": 6.0},
    )
    raw = _board_with_style(style)

    migrated = _migrate(raw, catalog)

    node = migrated["style"]
    for part in slot_path:
        node = node[part]
    assert node == {"radius": 6.0}
    AuthoredBoard.model_validate(migrated)


def test_registry_declares_the_0_5_0_border_deletions() -> None:
    """Declared at the 0.5.0 -> 0.6.0 boundary, now frozen -- not at
    ``catalog.latest_released.version``, which is 0.6.0 itself post-freeze."""
    _, registry = _board_migration_context()
    deletions = registry.deletions_from("0.5.0")

    declared_first_segments = {d.path[0] for d in deletions}
    assert {"input", "columns", "spark", "spark_bar"} <= declared_first_segments


def test_live_borders_survive_the_narrowed_slot_deletions(
    catalog: YamlSchemaCatalog,
) -> None:
    """Live full-BorderStyle slots must survive a board that also carries a
    deleted tail — pins that the spark qualifier does not over-match
    ``charts.marks.bar.border`` and that ``charts.table.border`` stays whole."""
    style: dict[str, Any] = {
        "charts": {
            "table": {"border": {"width": 1.5, "color": "red", "radius": 4.0}},
            "marks": {"bar": {"border": {"width": 2.0, "color": "blue"}}},
        },
    }
    _set_nested(
        style,
        NARROWED_SLOTS["charts.table.spark.bar.border"],
        {"width": 3.0, "radius": 2.0},
    )
    raw = _board_with_style(style)

    migrated = _migrate(raw, catalog)

    assert migrated["style"]["charts"]["table"]["border"] == {
        "width": 1.5,
        "color": "red",
        "radius": 4.0,
    }
    assert migrated["style"]["charts"]["marks"]["bar"]["border"] == {
        "width": 2.0,
        "color": "blue",
    }
    assert migrated["style"]["charts"]["table"]["spark"]["bar"]["border"] == {
        "radius": 2.0
    }
    AuthoredBoard.model_validate(migrated)


INERT_BORDER_FIELDS = ("width", "color", "dash_array", "line_cap", "dash_offset")


def test_chart_local_spark_bar_border_keeps_only_radius_and_kpi_keeps_its_own(
    catalog: YamlSchemaCatalog,
) -> None:
    """The chart-local slot has no ``spark_bar`` segment, so the declaration is
    scoped by ``chart_type``: a kpi's full ``style.border`` must survive."""
    legacy = {"width": 2.0, "color": "red", "radius": 3.0}
    raw: dict[str, Any] = {
        "charts": {
            "spark": {
                "type": "spark_bar",
                "query": "q1",
                "x": "x",
                "y": "x",
                "style": {"border": dict(legacy)},
            },
            "k": {
                "type": "kpi",
                "query": "q1",
                "value": "x",
                "style": {"border": dict(legacy)},
            },
        },
        "queries": {"q1": {"sql": "SELECT 1 AS x", "source": "test"}},
        "rows": ["spark", "k"],
    }

    text = yaml.safe_dump(raw)

    migrated = _migrate(raw, catalog)

    assert migrated["charts"]["spark"]["style"]["border"] == {"radius": 3.0}
    assert migrated["charts"]["k"]["style"]["border"] == legacy
    result = compile(text)
    assert result.success, [e.message for e in result.errors]


def test_registry_declares_the_chart_local_spark_bar_border_deletions() -> None:
    _, registry = _board_migration_context()

    declared = {
        d.path for d in registry.deletions_from("0.5.0") if d.chart_type == "spark_bar"
    }

    assert declared == {("style", "border", field) for field in INERT_BORDER_FIELDS}
