"""Migration test for the 0.6.0 -> 0.7.0 axis grid ``zero`` -> ``threshold``
rename.

The axis grid's threshold-rule sub-block, previously spelled ``zero``,
renamed to ``threshold`` (and widened onto the shared base grid class, so it
is now reachable at every axis slot, not just ``axis_y``). Declared via
``suffix_rename_moves`` in
``compile/migrations/versions/v0_7_0.py``'s ``THRESHOLD_RENAMES``, anchored
at the parent ``grid`` key
(``("grid", "threshold") -> ("grid", "zero")``) for self-documenting hygiene,
matching ``AXIS_Y_LABEL_RENAMES``'s own two-segment anchor in
``versions/v0_6_0.py`` — not
because a bare one-segment tail would incorrectly match the unrelated
``scale.continuous.zero`` boolean (it structurally can't; see that module's
docstring for why).
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_mapping,
)
from dbt_charts.core.compile.migrations.migrations import _board_migration_context
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
)


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _migrate(raw: dict[str, Any], catalog: YamlSchemaCatalog) -> dict[str, Any]:
    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw, catalog=catalog, registry=registry)


def _board_with_chart_local_grid_zero() -> dict[str, Any]:
    return {
        "charts": {
            "rev": {
                "type": "bar",
                "query": "q",
                "x": "month",
                "y": "revenue",
                "style": {
                    "axis_y": {
                        "grid": {"zero": {"color": "#ff0000", "width": 3.0}},
                        # Unrelated field, same "zero" tail one level over --
                        # must not be touched by this rename.
                        "scale": {"continuous": {"zero": False}},
                    }
                },
            }
        },
        "rows": ["rev"],
    }


def test_axis_y_grid_zero_migrates_to_threshold_at_chart_local_position(
    catalog: YamlSchemaCatalog,
) -> None:
    """A chart-local ``style.axis_y.grid.zero`` migrates to ``grid.threshold``
    and still compiles against the current schema."""
    migrated = _migrate(_board_with_chart_local_grid_zero(), catalog)

    grid = migrated["charts"]["rev"]["style"]["axis_y"]["grid"]
    assert grid["threshold"] == {"color": "#ff0000", "width": 3.0}
    assert "zero" not in grid
    AuthoredBoard.model_validate(migrated)


def test_scale_continuous_zero_boolean_is_untouched(catalog: YamlSchemaCatalog) -> None:
    """``scale.continuous.zero`` (an unrelated boolean one level over, same
    short tail) must survive the rename unrenamed and unmoved -- the
    load-bearing reason ``THRESHOLD_RENAMES`` anchors at the parent ``grid``
    key instead of a bare one-segment tail."""
    migrated = _migrate(_board_with_chart_local_grid_zero(), catalog)

    scale = migrated["charts"]["rev"]["style"]["axis_y"]["scale"]
    assert scale["continuous"]["zero"] is False
    assert "threshold" not in scale["continuous"]


def _board_with_board_level_grid_zero() -> dict[str, Any]:
    return {
        "charts": {"rev": {"type": "bar", "query": "q", "x": "month", "y": "revenue"}},
        "style": {
            "charts": {"axis_y": {"grid": {"zero": {"color": "#00ff00", "width": 1.5}}}}
        },
        "rows": ["rev"],
    }


def test_axis_y_grid_zero_migrates_to_threshold_at_board_level_position(
    catalog: YamlSchemaCatalog,
) -> None:
    """The board-level (channel-tier) ``style.charts.axis_y.grid.zero`` migrates
    to ``grid.threshold`` too -- a separate resolved position from the
    chart-local one above, both declared by the same two-segment-anchored
    move."""
    migrated = _migrate(_board_with_board_level_grid_zero(), catalog)

    grid = migrated["style"]["charts"]["axis_y"]["grid"]
    assert grid["threshold"] == {"color": "#00ff00", "width": 1.5}
    assert "zero" not in grid
    AuthoredBoard.model_validate(migrated)


#: Every ``grid.zero`` position ``suffix_rename_moves`` resolves at the
#: 0.6.0 boundary. Written out rather than re-derived so a change to the
#: resolver's traversal -- or a bad anchor silently resolving to zero moves --
#: shows up here as a diff instead of passing silently (``suffix_rename_moves``
#: has no other way to fail loud on a mistyped anchor/tail). Includes both
#: ``GridItem.item`` open-map arm positions (``grid.items.*.item.*...`` and
#: its ``tabs.items.*`` nested form) that only exist in the 0.6.0 schema.
EXPECTED_THRESHOLD_MOVES = (
    "charts.*.style.axis_y.grid.zero",
    "cols.*.*.style.axis_y.grid.zero",
    "cols.*.style.axis_y.grid.zero",
    "grid.items.*.item.*.style.axis_y.grid.zero",
    "grid.items.*.item.style.axis_y.grid.zero",
    "rows.*.*.style.axis_y.grid.zero",
    "rows.*.style.axis_y.grid.zero",
    "style.charts.area.axis_y.grid.zero",
    "style.charts.axis_y.grid.zero",
    "style.charts.bar.axis_y.grid.zero",
    "style.charts.heatmap.axis_y.grid.zero",
    "style.charts.histogram.axis_y.grid.zero",
    "style.charts.line.axis_y.grid.zero",
    "style.charts.scatter.axis_y.grid.zero",
    "tabs.items.*.cols.*.*.style.axis_y.grid.zero",
    "tabs.items.*.cols.*.style.axis_y.grid.zero",
    "tabs.items.*.grid.items.*.item.*.style.axis_y.grid.zero",
    "tabs.items.*.grid.items.*.item.style.axis_y.grid.zero",
    "tabs.items.*.rows.*.*.style.axis_y.grid.zero",
    "tabs.items.*.rows.*.style.axis_y.grid.zero",
    "tabs.items.*.style.charts.area.axis_y.grid.zero",
    "tabs.items.*.style.charts.axis_y.grid.zero",
    "tabs.items.*.style.charts.bar.axis_y.grid.zero",
    "tabs.items.*.style.charts.heatmap.axis_y.grid.zero",
    "tabs.items.*.style.charts.histogram.axis_y.grid.zero",
    "tabs.items.*.style.charts.line.axis_y.grid.zero",
    "tabs.items.*.style.charts.scatter.axis_y.grid.zero",
)


def test_resolved_move_set_is_exactly_the_documented_positions(
    catalog: YamlSchemaCatalog,
) -> None:
    """The ``grid.zero`` tail resolves to these 27 positions and no others.

    A bad anchor (e.g. a typo in the parent-key qualifier) either silently
    resolves to zero moves or picks up an unintended position such as
    ``scale.continuous.zero`` -- both change this set, so this is the test
    that actually fails on a broken declaration rather than just checking
    "no exception was raised".
    """
    from dbt_charts.core.compile.migrations.versions.v0_7_0 import moves

    threshold_moves = [
        move
        for move in moves("0.6.0", "0.7.0", catalog=catalog)
        if move.old_path[-2:] == ("grid", "zero")
    ]
    resolved = tuple(sorted(".".join(move.old_path) for move in threshold_moves))
    assert resolved == EXPECTED_THRESHOLD_MOVES
    for move in threshold_moves:
        assert move.new_path == (*move.old_path[:-1], "threshold")


def test_axis_y_grid_zero_compiles_clean_end_to_end() -> None:
    """compile()-level: an old-grammar board authoring a chart-local
    ``style.axis_y.grid.zero`` compiles clean through the automatic
    ``prepare_board_mapping`` path -- the only path a Cloud user's board
    takes; nothing runs ``dct migrate`` for one. ``migrate_mapping`` alone
    (the tests above) skips the currency check, the fallbacks, and the
    whole-document gate -- see ``compile/migrations/AGENTS.md``'s "prove it
    with a test" rule. Before this test existed, an ``UnsupportedSchemaError``
    from a broken guard would have gone unnoticed here: ``prepare_board_mapping``
    silently returns the mapping un-migrated on that error, and the
    un-migrated ``grid.zero`` spelling then dies on ``extra_forbidden``
    against the current schema.
    """
    yaml_content = """title: T
queries:
  q1: {sql: "select 1 as month, 2 as revenue", source: test}
charts:
  rev: {type: bar, query: q1, x: month, y: revenue, style: {axis_y: {grid: {zero: {color: "#ff0000", width: 3.0}}}}}
rows: [rev]
"""
    result = compile(yaml_content)
    assert any(
        "migrated this YAML" in d.message
        for d in result.warnings
        if d.code == "WARN-SCHEMA-MIGRATED"
    ), result.warnings

    assert result.success, [f"{e.code}: {e.message}" for e in result.errors]
    assert result.board is not None
    threshold = result.board.charts["rev"].style.axis_y.grid.threshold
    assert threshold.color == "#ff0000"
    assert threshold.width == 3.0
