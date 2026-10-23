"""Integration tests for chart-level style.scale ConditionalMove migration.

``style.scale.continuous.zero`` on bar/line/area charts migrates to
``style.axis_y.scale.continuous.zero`` when an ``axis_y:`` block already
exists; when it doesn't, the value is dropped and a warning names the chart.
Scatter, heatmap, and histogram charts, and all non-zero sub-fields, are
deliberately excluded — they fail loud via Pydantic's extra="forbid"
validation once the model field is removed.

Theme-level ``style.charts.<family>.scale`` gets no migration either; a
Move-based approach was designed and proven broken (the engine leaves an
orphaned, invalid parent mapping behind that trips UnsupportedSchemaError
during its own post-migration check). Theme-level scale is fail-loud.

These tests use the real catalog and migration registry, mirroring the
structure of test_kpi_tone_migration.py.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.migrations import (
    IncompleteMigrationError,
    MigrationConflictError,
    SchemaMigrationWarning,
    migrate_mapping,
    migrate_yaml_text,
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


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _migrate(raw: dict[str, Any], catalog: YamlSchemaCatalog) -> dict[str, Any]:
    """Run migration suppressing in-memory-migration SchemaMigrationWarning."""
    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw, catalog=catalog, registry=registry)


def _bar_board(style: dict[str, Any]) -> dict[str, Any]:
    """Minimal board with a single bar chart."""
    return {
        "charts": {
            "revenue_bar": {
                "type": "bar",
                "query": "q1",
                "x": "month",
                "y": "revenue",
                "style": style,
            }
        },
        "rows": ["revenue_bar"],
    }


def _line_board(style: dict[str, Any]) -> dict[str, Any]:
    return {
        "charts": {
            "revenue_line": {
                "type": "line",
                "query": "q1",
                "x": "month",
                "y": "revenue",
                "style": style,
            }
        },
        "rows": ["revenue_line"],
    }


def _area_board(style: dict[str, Any]) -> dict[str, Any]:
    return {
        "charts": {
            "revenue_area": {
                "type": "area",
                "query": "q1",
                "x": "month",
                "y": "revenue",
                "style": style,
            }
        },
        "rows": ["revenue_area"],
    }


# Case (a): bar/line/area with existing axis_y block -> zero migrates
class TestChartLevelZeroMigrates:
    def test_bar_zero_false_migrates_into_existing_axis_y(
        self, catalog: YamlSchemaCatalog
    ) -> None:
        """style.scale.continuous.zero on bar with axis_y: block moves to axis_y path."""
        raw = _bar_board(
            {
                "scale": {"continuous": {"zero": False}},
                "axis_y": {"labels": {"format": ",.0f"}},
            }
        )

        migrated = _migrate(raw, catalog)

        chart = migrated["charts"]["revenue_bar"]
        # Old key fully gone (orphan-parent cleanup)
        assert "scale" not in chart.get("style", {})
        assert chart["style"]["axis_y"]["scale"]["continuous"]["zero"] is False
        # Surviving sibling key preserved
        assert chart["style"]["axis_y"]["labels"]["format"] == ",.0f"
        AuthoredBoard.model_validate(migrated)

    def test_line_zero_false_migrates_into_existing_axis_y(
        self, catalog: YamlSchemaCatalog
    ) -> None:
        raw = _line_board(
            {
                "scale": {"continuous": {"zero": False}},
                "axis_y": {},
            }
        )

        migrated = _migrate(raw, catalog)

        chart = migrated["charts"]["revenue_line"]
        assert "scale" not in chart.get("style", {})
        assert chart["style"]["axis_y"]["scale"]["continuous"]["zero"] is False
        AuthoredBoard.model_validate(migrated)

    def test_area_zero_true_migrates_into_existing_axis_y(
        self, catalog: YamlSchemaCatalog
    ) -> None:
        raw = _area_board(
            {
                "scale": {"continuous": {"zero": True}},
                "axis_y": {},
            }
        )

        migrated = _migrate(raw, catalog)

        chart = migrated["charts"]["revenue_area"]
        assert "scale" not in chart.get("style", {})
        assert chart["style"]["axis_y"]["scale"]["continuous"]["zero"] is True
        AuthoredBoard.model_validate(migrated)

    def test_orphan_parent_cleanup_removes_whole_scale_key(
        self, catalog: YamlSchemaCatalog
    ) -> None:
        """When scale only had continuous.zero, the whole style.scale key is removed.

        Pins _try_delete_tail's upward empty-parent pruning — that's what
        makes ConditionalMove viable where the theme-level Move failed.
        """
        raw = _bar_board(
            {
                "scale": {"continuous": {"zero": False}},
                "axis_y": {},
            }
        )

        migrated = _migrate(raw, catalog)

        chart = migrated["charts"]["revenue_bar"]
        assert "scale" not in chart.get("style", {})


# Case (b): conflict -> MigrationConflictError
def test_bar_zero_conflict_when_axis_y_zero_already_set(
    catalog: YamlSchemaCatalog,
) -> None:
    """style.scale.continuous.zero and axis_y.scale.continuous.zero both set raises conflict."""
    _, registry = _board_migration_context()
    raw = _bar_board(
        {
            "scale": {"continuous": {"zero": False}},
            "axis_y": {"scale": {"continuous": {"zero": True}}},
        }
    )

    with pytest.raises(MigrationConflictError):
        migrate_mapping(raw, catalog=catalog, registry=registry)


# Case (c): no axis_y block -> drop with warning
def test_bar_zero_dropped_when_no_axis_y_block(
    catalog: YamlSchemaCatalog,
) -> None:
    """style.scale.continuous.zero dropped with warning when no axis_y: block exists."""
    _, registry = _board_migration_context()
    raw = _bar_board({"scale": {"continuous": {"zero": False}}})

    with pytest.warns(SchemaMigrationWarning, match="revenue_bar"):
        migrated = migrate_mapping(raw, catalog=catalog, registry=registry)

    charts = migrated["charts"]
    assert isinstance(charts, dict)
    chart = charts["revenue_bar"]
    assert isinstance(chart, dict)
    assert "scale" not in chart.get("style", {})
    AuthoredBoard.model_validate(migrated)


def test_line_zero_dropped_when_no_axis_y_block(
    catalog: YamlSchemaCatalog,
) -> None:
    _, registry = _board_migration_context()
    raw = _line_board({"scale": {"continuous": {"zero": True}}})

    with pytest.warns(SchemaMigrationWarning, match="revenue_line"):
        migrated = migrate_mapping(raw, catalog=catalog, registry=registry)

    charts = migrated["charts"]
    assert isinstance(charts, dict)
    chart = charts["revenue_line"]
    assert isinstance(chart, dict)
    assert "scale" not in chart.get("style", {})


# Case (d): scatter chart-level scale -> fail loud
def test_scatter_chart_level_scale_fails_loud(
    catalog: YamlSchemaCatalog,
) -> None:
    """Scatter's chart-level scale is excluded from the ConditionalMove; fails loud.

    Once the model field is removed, any scatter board with style.scale gets
    extra_forbidden. The ConditionalMove chart_type list is bar/line/area only.
    """
    raw = {
        "charts": {
            "scatter_chart": {
                "type": "scatter",
                "query": "q1",
                "x": "val_x",
                "y": "val_y",
                "style": {"scale": {"continuous": {"type": "log"}}},
            }
        },
        "rows": ["scatter_chart"],
    }
    prepared = prepare_board_mapping(raw)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AuthoredBoard.model_validate(prepared)


# Case (e): theme-level bar.scale -> fail loud
def test_theme_level_bar_scale_fails_loud(
    catalog: YamlSchemaCatalog,
) -> None:
    """Theme-level style.charts.bar.scale is not migrated; extra_forbidden.

    A Move-based migration was proven broken (leaves an orphaned parent mapping
    that trips UnsupportedSchemaError). No migration entry exists for theme-level
    scale; fail-loud is the correct behavior.
    """
    raw: dict[str, Any] = {
        "style": {"charts": {"bar": {"scale": {"continuous": {"zero": True}}}}}
    }
    prepared = prepare_board_mapping(raw, model=BoardPatch)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        BoardPatch.model_validate(prepared)


# Case (f): theme-level scatter.scale -> fail loud (same treatment as bar at theme level)
def test_theme_level_scatter_scale_fails_loud(
    catalog: YamlSchemaCatalog,
) -> None:
    """Theme-level scatter.scale is not migrated, same as bar — consistent fail-loud."""
    raw: dict[str, Any] = {
        "style": {"charts": {"scatter": {"scale": {"continuous": {"zero": False}}}}}
    }
    prepared = prepare_board_mapping(raw, model=BoardPatch)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        BoardPatch.model_validate(prepared)


# Case (g): migrate_yaml_text raises for chart-level ConditionalMove
def test_migrate_yaml_text_raises_for_bar_with_chart_level_scale_zero(
    catalog: YamlSchemaCatalog,
) -> None:
    """migrate_yaml_text has no ConditionalMove support; raises IncompleteMigrationError.

    This mirrors test_migrate_yaml_text_raises_for_kpi_with_style_tone: the
    load-time and file-rewrite migration paths diverge for ConditionalMove cases.

    Not ``UnsupportedSchemaError``: the grammar was recognized and its other
    transitions did run — what failed is finishing. The two are siblings so a
    caller can tell "this was never old" from "this was old and could not be
    fully modernized"; only the second is worth announcing.
    """
    _, registry = _board_migration_context()
    yaml_text = (
        "charts:\n"
        "  revenue_bar:\n"
        "    type: bar\n"
        "    query: q1\n"
        "    x: month\n"
        "    y: revenue\n"
        "    style:\n"
        "      scale:\n"
        "        continuous:\n"
        "          zero: false\n"
        "rows: [revenue_bar]\n"
    )

    with pytest.raises(IncompleteMigrationError):
        migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)


# Case (h1): bar with zero+nice, axis_y present -> ValidationError on scale residual, no warning
def test_bar_mixed_zero_and_nonzero_field_with_axis_y_no_warning(
    catalog: YamlSchemaCatalog,
) -> None:
    """With axis_y present, ConditionalMove moves zero; residual nice stops the
    migration finishing; prepare_board_mapping returns the original — and says so.

    No *drop* warning: with the sibling present the move fires normally and
    nothing is discarded. But the board was old and could not be fully
    modernized, which is announced rather than swallowed — silence here is what
    made a stale board fail with errors naming fields nobody touched.
    """
    raw = _bar_board(
        {
            "scale": {"continuous": {"zero": False}, "nice": True},
            "axis_y": {},
        }
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        prepared = prepare_board_mapping(raw)

    migration_warns = [
        w for w in caught if issubclass(w.category, SchemaMigrationWarning)
    ]
    assert len(migration_warns) == 1
    assert "could not finish migrating" in str(migration_warns[0].message)
    assert "dropped" not in str(migration_warns[0].message)

    with pytest.raises(ValidationError, match="extra_forbidden"):
        AuthoredBoard.model_validate(prepared)


# Case (h2): bar with zero+nice, no axis_y -> both warning and ValidationError
def test_bar_mixed_zero_and_nonzero_field_without_axis_y_warns_and_fails(
    catalog: YamlSchemaCatalog,
) -> None:
    """Without axis_y the ConditionalMove would drop zero — but the migration is
    then discarded, so nothing is actually dropped and no drop warning fires.

    ``prepare_board_mapping`` returns the *original* mapping here, zero included.
    Telling the author their value was destroyed would send them hunting for
    damage that never happened, so drop warnings are held until the
    current-schema gate passes. What they get instead is the accurate notice
    that the migration could not finish.
    """
    raw = _bar_board(
        {
            "scale": {"continuous": {"zero": False}, "nice": True},
        }
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        prepared = prepare_board_mapping(raw)

    migration_warns = [
        w for w in caught if issubclass(w.category, SchemaMigrationWarning)
    ]
    assert len(migration_warns) == 1
    assert "could not finish migrating" in str(migration_warns[0].message)
    assert "dropped" not in str(migration_warns[0].message)

    with pytest.raises(ValidationError, match="extra_forbidden"):
        AuthoredBoard.model_validate(prepared)


# Case (i): chart-level zero: auto migrates via ConditionalMove (value-agnostic)
def test_bar_zero_auto_migrates_same_as_bool(
    catalog: YamlSchemaCatalog,
) -> None:
    """ConditionalMove moves zero: auto verbatim — value doesn't affect whether the move fires."""
    raw = _bar_board(
        {
            "scale": {"continuous": {"zero": "auto"}},
            "axis_y": {},
        }
    )

    migrated = _migrate(raw, catalog)

    chart = migrated["charts"]["revenue_bar"]
    assert "scale" not in chart.get("style", {})
    assert chart["style"]["axis_y"]["scale"]["continuous"]["zero"] == "auto"


# Case (k): heatmap/histogram chart-level zero -> fail loud (never functional)
def test_heatmap_chart_level_scale_fails_loud(
    catalog: YamlSchemaCatalog,
) -> None:
    """Heatmap's chart-level zero was never functional; excluded from ConditionalMove."""
    raw = {
        "charts": {
            "heat": {
                "type": "heatmap",
                "query": "q1",
                "x": "col_a",
                "y": "col_b",
                "style": {"scale": {"continuous": {"zero": True}}},
            }
        },
        "rows": ["heat"],
    }
    prepared = prepare_board_mapping(raw)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AuthoredBoard.model_validate(prepared)


def test_histogram_chart_level_scale_fails_loud(
    catalog: YamlSchemaCatalog,
) -> None:
    """Histogram's chart-level zero was never functional; excluded from ConditionalMove."""
    raw = {
        "charts": {
            "hist": {
                "type": "histogram",
                "query": "q1",
                "x": "value",
                "style": {"scale": {"continuous": {"zero": False}}},
            }
        },
        "rows": ["hist"],
    }
    prepared = prepare_board_mapping(raw)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AuthoredBoard.model_validate(prepared)


# Layers: a layers: entry is not touched by the migration
def test_bar_layer_style_scale_not_touched_by_migration(
    catalog: YamlSchemaCatalog,
) -> None:
    """A layers: [{type: bar, ...}] layer dict is left untouched by the migration.

    _apply_conditional_move_recursive fires on any dict whose type matches
    chart_type *and* whose position the source grammar declares as that
    chart family -- a layer entry qualifies on both counts (layers are
    themselves chart-shaped patches in the schema). But a layer's style has
    no scale field (it is a marks-only patch), so old_tail navigation bails
    immediately — this test pins that the migration moves the parent chart's
    zero to axis_y while leaving the embedded layer dict untouched.
    """
    raw = {
        "charts": {
            "layered": {
                "type": "line",
                "query": "q1",
                "x": "month",
                "y": "revenue",
                "layers": [{"type": "bar", "style": {}}],
                "style": {
                    "scale": {"continuous": {"zero": False}},
                    "axis_y": {},
                },
            }
        },
        "rows": ["layered"],
    }
    migrated = _migrate(raw, catalog)
    charts = migrated["charts"]
    assert isinstance(charts, dict)
    chart = charts["layered"]
    assert isinstance(chart, dict)
    # The bar layer dict is not modified
    assert chart["layers"] == [{"type": "bar", "style": {}}]
    # zero was relocated to axis_y.scale.continuous.zero
    chart_style = chart.get("style", {})
    assert isinstance(chart_style, dict)
    axis_y = chart_style.get("axis_y", {})
    assert isinstance(axis_y, dict)
    assert axis_y.get("scale", {}).get("continuous", {}).get("zero") is False
    AuthoredBoard.model_validate(migrated)


# Boundary coverage: ConditionalMove is registered at the 0.4.0 -> 0.5.0 boundary
def test_registry_covers_the_0_4_0_chart_scale_conditional_moves(
    catalog: YamlSchemaCatalog,
) -> None:
    """The three chart-scale ConditionalMoves are registered at the 0.4.0 -> 0.5.0 boundary."""
    _, registry = _board_migration_context()
    cond_moves = registry.conditional_moves_from("0.4.0")

    scale_moves = [
        cm
        for cm in cond_moves
        if cm.old_tail == ("style", "scale", "continuous", "zero")
    ]
    assert len(scale_moves) == 3
    assert all(cm.target_schema == "0.5.0" for cm in scale_moves)
    chart_types = {cm.chart_type for cm in scale_moves}
    assert chart_types == {"bar", "line", "area"}
