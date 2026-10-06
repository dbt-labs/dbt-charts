"""0.9.0 -> 0.10.0: style.title.sizes moves to style.fonts.heading.sizes.

Chart-level ramps (``style.charts.title.sizes``, chart-local
``style.title.sizes``) have no successor and are deleted.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_mapping,
    migrate_yaml_text,
)
from dbt_charts.core.compile.migrations.migrations import (
    MigrationError,
    _board_migration_context,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    load_yaml_schema_catalog,
)

RAMP = [30, 22, 16, 14, 12, 11]


def test_title_sizes_migrates_to_the_heading_font() -> None:
    _, registry = _board_migration_context()
    raw = {
        "title": "Revenue",
        "style": {"title": {"sizes": RAMP}},
        "rows": ["revenue"],
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        migrated = migrate_mapping(
            raw, catalog=load_yaml_schema_catalog(), registry=registry
        )
    assert migrated["style"]["fonts"] == {"heading": {"sizes": RAMP}}
    assert "sizes" not in migrated["style"]["title"]
    AuthoredBoard.model_validate(migrated)


def test_title_sizes_yaml_text_refuses_loudly() -> None:
    """The text writer moves scalars only; a list-valued move is refused by name
    rather than half-applied."""
    _, registry = _board_migration_context()
    text = (
        "title: Revenue\nstyle:\n  title:\n    sizes: [30, 22, 16, 14, 12, 11]\n"
        "rows: [revenue]\n"
    )
    with pytest.raises(MigrationError, match="style.title.sizes"):
        migrate_yaml_text(text, catalog=load_yaml_schema_catalog(), registry=registry)


def _migrate(raw: dict[str, Any]) -> dict[str, Any]:
    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(
            raw, catalog=load_yaml_schema_catalog(), registry=registry
        )


_CHART = {"type": "bar", "query": "q1", "x": "a", "y": "b"}
_QUERIES = {"q1": {"sql": "select 1 as a, 2 as b", "source": "test"}}


@pytest.mark.parametrize(
    "raw",
    [
        {"style": {"charts": {"title": {"sizes": RAMP}}}, "rows": ["c1"]},
        {
            "style": {"charts": {"title": {"sizes": RAMP}}},
            "rows": [{"style": {"charts": {"title": {"sizes": RAMP}}}, "rows": ["c1"]}],
        },
        {
            "rows": ["c1"],
            "charts": {"c1": {**_CHART, "style": {"title": {"sizes": RAMP}}}},
        },
        {
            "style": {"charts": {"bar": {"title": {"sizes": RAMP}}}},
            "rows": ["c1"],
        },
    ],
    ids=["charts-root", "charts-nested-rows", "chart-local", "charts-family"],
)
def test_chart_level_ramps_are_deleted(raw: dict[str, Any]) -> None:
    raw = {"title": "T", "queries": _QUERIES, "charts": {"c1": _CHART}, **raw}
    migrated = _migrate(raw)
    assert "sizes" not in str(migrated)
    AuthoredBoard.model_validate(migrated)


def test_chart_level_ramp_deletion_names_the_successor() -> None:
    raw = {
        "title": "T",
        "queries": _QUERIES,
        "charts": {"c1": _CHART},
        "style": {"charts": {"title": {"sizes": RAMP}}},
        "rows": ["c1"],
    }
    _, registry = _board_migration_context()
    with pytest.warns(SchemaMigrationWarning, match="style.fonts.heading.sizes"):
        migrate_mapping(raw, catalog=load_yaml_schema_catalog(), registry=registry)


def test_root_ramp_still_moves_beside_a_chart_level_ramp() -> None:
    migrated = _migrate(
        {
            "title": "T",
            "queries": _QUERIES,
            "charts": {"c1": _CHART},
            "style": {"title": {"sizes": RAMP}, "charts": {"title": {"sizes": RAMP}}},
            "rows": ["c1"],
        }
    )
    assert migrated["style"]["fonts"] == {"heading": {"sizes": RAMP}}
    assert "charts" not in migrated["style"]


def test_old_ramps_compile_clean() -> None:
    result = compile(
        """title: T
queries:
  q1: {sql: "select 1 as a, 2 as b", source: test}
charts:
  c1:
    type: bar
    query: q1
    x: a
    y: b
    style:
      title:
        sizes: [30, 22, 16, 14, 12, 11]
style:
  title:
    sizes: [30, 22, 16, 14, 12, 11]
  charts:
    title:
      sizes: [20, 20, 20, 20, 20, 20]
rows: [c1]
"""
    )
    assert result.success, [f"{e.code}: {e.message}" for e in result.errors]
    assert result.board is not None


_INNER = {"style": {"title": {"sizes": [50, 40, 30, 20, 15, 10]}}, "rows": ["c1"]}
_DEEP = {
    "rows": {"rows": [{"rows": [_INNER]}]},
    "tabs": {
        "tabs": {
            "items": [{"title": "A", "rows": [{"rows": [_INNER]}]}],
        }
    },
}


@pytest.mark.parametrize("shape", ["rows", "tabs"])
def test_board_ramp_moves_at_depth_two(shape: str) -> None:
    raw = {"title": "T", "queries": _QUERIES, "charts": {"c1": _CHART}, **_DEEP[shape]}
    _, registry = _board_migration_context()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        migrated = migrate_mapping(
            raw, catalog=load_yaml_schema_catalog(), registry=registry
        )
    assert "[50, 40, 30, 20, 15, 10]" in str(migrated)
    assert "'fonts': {'heading': {'sizes': [50, 40, 30, 20, 15, 10]}}" in str(migrated)
    assert not any(
        "chart-level" in str(w.message) or "dropped" in str(w.message) for w in caught
    )
    AuthoredBoard.model_validate(migrated)


def test_board_ramp_past_the_nesting_depth_fails_loudly() -> None:
    board: dict[str, Any] = _INNER
    for _ in range(4):
        board = {"rows": [board]}
    raw = {"title": "T", "queries": _QUERIES, "charts": {"c1": _CHART}, **board}
    with pytest.raises(MigrationError):
        _migrate(raw)


def test_ramp_deletion_scopes_are_derived_from_the_frozen_schema() -> None:
    """The scopes come from 0.9.0's own grammar, not the live models."""
    from dbt_charts.core.compile.migrations.versions.v0_10_0 import (
        _chart_families_with_title_ramp,
        _chart_types_with_title_ramp,
    )

    source = load_yaml_schema_catalog().schema_for("0.9.0")
    assert _chart_types_with_title_ramp(source) == (
        "area",
        "bar",
        "bubble_map",
        "donut",
        "geoshape",
        "heatmap",
        "histogram",
        "kpi",
        "line",
        "map",
        "pie",
        "point_map",
        "scatter",
        "table",
    )
    assert _chart_families_with_title_ramp(source) == (
        "area",
        "bar",
        "geoshape",
        "heatmap",
        "histogram",
        "kpi",
        "line",
        "pie",
        "point_map",
        "scatter",
        "table",
    )
