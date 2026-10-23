"""End-to-end pipeline tests for chart.support_table.

Goes compile → resolve → render and asserts the engine's output.
Confirms the support-table primitive threads through the public renderer.
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.chart.vega_lite import render_chart

_BOARD_CONTEXT = resolve_chart_style_context(get_theme_style())


def _compiled_bar_with_support_table(entries):
    """Build a vertical-bar Chart with a support_table block, bypassing YAML parsing.

    Orientation is pinned to vertical so the strip renders as rows above/below
    the plot (see _compiled_bar_horizontal_with_support_table for the
    column-block counterpart); "month" is a categorical string x that would
    otherwise auto-resolve to horizontal.
    """
    flat = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": entries},
            "style": {"orientation": "vertical"},
        }
    )
    return flat


def _compiled_bar_horizontal_with_support_table(entries, style=None):
    """Build a horizontal-bar Chart with a support_table block.

    Horizontal bars swap the category axis onto y — support_table then
    renders as value columns beside the plot instead of rows above/below it.
    """
    chart_style = {"orientation": "horizontal"}
    if style:
        chart_style.update(style)
    flat = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "region",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": entries},
            "style": chart_style,
        }
    )
    return flat


_HBAR_DATA = [
    {"region": "East", "revenue": 100.0},
    {"region": "West", "revenue": 200.0},
    {"region": "North", "revenue": 150.0},
]


_SAMPLE_DATA = [
    {"month": "Jan", "revenue": 100.0},
    {"month": "Feb", "revenue": 200.0},
    {"month": "Mar", "revenue": 150.0},
]

_TEMPORAL_DATA = [
    {"date": "2024-01-01", "revenue": 100.0},
    {"date": "2024-02-01", "revenue": 200.0},
    {"date": "2024-03-01", "revenue": 150.0},
]


def _compiled_line_temporal_with_support_table(entries):
    flat = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "line",
            "x": "date",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": entries},
        }
    )
    return flat


def _compiled_area_temporal_with_support_table(entries):
    flat = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "area",
            "x": "date",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": entries},
        }
    )
    return flat


def test_resolve_bakes_a_concrete_support_table_position():
    # position is resolved once, at compile time, onto
    # effective_support_table_style — render never patches it in afterward
    # (see compile.resolve.chart._kwargs._support_table_geometry).
    vertical = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue"}]},
            "style": {"orientation": "vertical"},
        }
    )
    resolved_vertical = resolve(
        vertical, _SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT
    )
    assert resolved_vertical.effective_support_table_style is not None
    assert resolved_vertical.effective_support_table_style.position == "top"

    horizontal_default = _compiled_bar_horizontal_with_support_table(
        [{"source": "revenue"}]
    )
    resolved_horizontal_default = resolve(
        horizontal_default, _HBAR_DATA, chart_style_context=_BOARD_CONTEXT
    )
    assert resolved_horizontal_default.effective_support_table_style is not None
    assert resolved_horizontal_default.effective_support_table_style.position == "left"

    horizontal_right = _compiled_bar_horizontal_with_support_table(
        [{"source": "revenue"}], style={"support_table": {"position": "right"}}
    )
    resolved_horizontal_right = resolve(
        horizontal_right, _HBAR_DATA, chart_style_context=_BOARD_CONTEXT
    )
    assert resolved_horizontal_right.effective_support_table_style is not None
    assert resolved_horizontal_right.effective_support_table_style.position == "right"


def test_render_bar_horizontal_support_table_rejects_top_bottom_position():
    # Schema: top/bottom are invalid on a vertical category axis — never
    # silently remapped. Raises at resolve time (a compile-time error): the
    # value is baked onto effective_support_table_style before any render
    # branch runs, so a bad board fails dct validate, not just a render.
    chart = _compiled_bar_horizontal_with_support_table(
        [{"source": "revenue"}], style={"support_table": {"position": "top"}}
    )
    with pytest.raises(CompilationError, match=r"(?i)position"):
        resolve(chart, _HBAR_DATA, chart_style_context=_BOARD_CONTEXT)


def test_render_bar_vertical_support_table_rejects_left_right_position():
    # Mirrored: left/right are invalid on a horizontal category axis.
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue"}]},
            "style": {
                "orientation": "vertical",
                "support_table": {"position": "left"},
            },
        }
    )
    with pytest.raises(CompilationError, match=r"(?i)position"):
        resolve(chart, _SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT)


def test_render_bar_with_support_table_produces_svg_with_row_labels():
    # HIGH 2: pipeline test must go all the way to SVG and assert the
    # strip's text actually renders. Skipped when vl_convert is not
    # installed.
    pytest.importorskip("vl_convert")

    chart = _compiled_bar_with_support_table(
        [{"source": "revenue", "format": "$.0f", "label": "REV"}]
    )
    svg = render_chart(
        chart,
        resolve_style(get_theme_style()),
        resolve_chart_style_context(get_theme_style()),
        _SAMPLE_DATA,
        format="svg",
        width=600,
        height=320,
    )
    # Label text must appear in the SVG output (left-stub by default).
    assert "REV" in svg
    # At least one per-x formatted cell value renders (raw value or format
    # output — both match the format-string contract).
    assert "$100" in svg or ">100<" in svg


def test_render_bar_support_table_missing_cell_and_default_label_in_svg():
    pytest.importorskip("vl_convert")

    chart = _compiled_bar_with_support_table(
        [{"source": "sample_size", "format": ",d"}]
    )
    data = [
        {"month": "Jan", "revenue": 100.0, "sample_size": 10},
        {"month": "Feb", "revenue": 120.0, "sample_size": None},
    ]
    svg = render_chart(
        chart,
        resolve_style(get_theme_style()),
        resolve_chart_style_context(get_theme_style()),
        data,
        format="svg",
        width=600,
        height=320,
    )
    assert "Sample size" in svg
    assert ">-</text>" in svg
    assert ">NaN<" not in svg
    assert ">null<" not in svg


# =============================================================================
# REGRESSION: line/area + temporal x — strip must inherit parent x type
# =============================================================================


def test_line_temporal_x_support_table_no_crash_through_vl_convert():
    pytest.importorskip("vl_convert")

    chart = _compiled_line_temporal_with_support_table(
        [{"source": "revenue", "format": "$.0f"}]
    )
    # Must not raise — previously crashed with "Cannot read properties of null (reading 'type')"
    svg = render_chart(
        chart,
        resolve_style(get_theme_style()),
        resolve_chart_style_context(get_theme_style()),
        _TEMPORAL_DATA,
        format="svg",
        width=600,
        height=320,
    )
    assert svg  # non-empty SVG means vl-convert succeeded


def test_area_temporal_x_support_table_no_crash_through_vl_convert():
    pytest.importorskip("vl_convert")

    chart = _compiled_area_temporal_with_support_table(
        [{"source": "revenue", "format": "$.0f"}]
    )
    svg = render_chart(
        chart,
        resolve_style(get_theme_style()),
        resolve_chart_style_context(get_theme_style()),
        _TEMPORAL_DATA,
        format="svg",
        width=600,
        height=320,
    )
    assert svg


# =============================================================================
# TEMPORAL/DATE-LIKE ORDINAL SAMPLING — over-40-row bypass
# =============================================================================


# ── per_series row order regression tests ────────────────────────────────
#
# These tests drive the engine's stack-vs-non-stack support-table branch
# end-to-end (compile → render).


# ── per_series COLUMN order regression tests ──
#
# Column order reads left to right in the chart's own stack order, on both
# placements. _column_edges lists every column in authored/series_order
# order, left to right on screen, regardless of whether the block sits at
# position "left" or "right" -- a reader maps visual column N to the chart's
# own segment N by screen position, the same way the source/aggregate columns
# already read left to right in authored order. Series order is never
# reversed to chase which screen edge is "closest to the plot".


# =============================================================================
# CADENCE DOWNSAMPLING — band cadence finer than label cadence
# =============================================================================


# ---------------------------------------------------------------------------
# Grouped-by-default bars: strip rows must be alphabetical (not reverse).
# Regression for the is_stacked_bar predicate that was not guarded against
# the new stack=None + color → grouped default.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Period filter must NOT fire when bands are wide enough to show all cells.
# Regression: for weekly ISO-date data over a short range, the ordinal
# bucketed-time path set axis.values to 2 monthly openers and the period
# filter restricted strip cells to those 2 positions — making the data
# table look like scattered numbers instead of a proper cell-per-band table.
# ---------------------------------------------------------------------------


# =============================================================================
# by_measure multi-y support_table pipeline tests
# =============================================================================


# =============================================================================
# Continuous-axis tick density: period_filter supersedes sampling
# =============================================================================
#
# When a chart has a finer-grained x-axis than its label cadence (e.g. 365
# daily rows displayed with monthly labels), the engine applies a
# label_period_filter_expr to keep one cell per label period. A sampling
# transform must NOT also be injected: sampling is computed from raw data row
# count (365 > 40 → step=10), but with the period filter already restricting
# output to 12 cells, sampling would further thin to 2 cells — an incorrect
# result. When period_filter is in effect, sampling must be suppressed.


# ---------------------------------------------------------------------------
# V2 renderer parity: support_table strip thinning
# ---------------------------------------------------------------------------


# =============================================================================
# BOARD RENDER PATH — render_resolved_chart is called directly by
# rendering.py's board pipeline (no chart.support_table available, only
# resolved.support_table). generate_vega_lite_spec / render_chart apply the
# post-pass themselves and don't exercise this path.
# =============================================================================


# =============================================================================
# GROUPED VS STACKED: per_series LAYS OUT THE WAY THE CHART LAYS OUT ITS SERIES
# =============================================================================


# ---------------------------------------------------------------------------
# A bottom strip clears whatever tilt the x labels were drawn at
# ---------------------------------------------------------------------------
