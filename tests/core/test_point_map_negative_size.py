"""Regression tests for bubble_map negative `size:` values.

Mark AREA cannot be negative — a bubble_map sizes its marks by area,
proportional to an authored `size:` measure. A negative row has no honest
area rendering: the area-proportional scale's zero-anchored domain floor
would otherwise clamp it to the smallest visible size, reading as "nearly
zero" instead of a large-magnitude negative value (silently fixing bad
input, the failure mode AGENTS.md #4 exists to prevent).

Two fixes ship together, both keyed off one shared predicate
(`row_has_negative_size` in `render/chart/emitters/geo.py`):
  1. The emitter drops negative-size rows from spec.data before Vega-Lite
     sees them — they are not drawn, not drawn misleadingly small.
  2. WARN_POINT_MAP_NEGATIVE_SIZE_VALUES fires, naming the dropped count and
     the responsible column, and recommends sizing by a magnitude (e.g.
     `abs(...)`) with a diverging `color:` for direction — never clamping.

Zero is not negative: a zero-valued row is legitimate data with a legitimate
area of nothing, and must not be dropped or counted.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.normalized import PointMapChart
from dbt_charts.core.diagnostics import WARN_POINT_MAP_NEGATIVE_SIZE_VALUES
from dbt_charts.core.render.warnings import (
    WarningContext,
    point_map_negative_size_values as detector,
)

from ._board_utils import make_test_resolved_board, make_test_resolved_chart

# Mixed sign: 3 positive/zero rows, 2 negative rows.
_MIXED_ROWS: list[dict[str, Any]] = [
    {"lat": 34.05, "lng": -118.25, "profit": 100.0},  # Los Angeles
    {"lat": 40.71, "lng": -74.01, "profit": 0.0},  # New York — zero, not negative
    {"lat": 41.88, "lng": -87.63, "profit": 50.0},  # Chicago
    {"lat": 47.61, "lng": -122.33, "profit": -25.0},  # Seattle — negative
    {"lat": 29.76, "lng": -95.37, "profit": -10.0},  # Houston — negative
]

_ALL_NEGATIVE_ROWS: list[dict[str, Any]] = [
    {"lat": 34.05, "lng": -118.25, "profit": -5.0},
    {"lat": 40.71, "lng": -74.01, "profit": -1.0},
]

_NO_NEGATIVE_ROWS: list[dict[str, Any]] = [
    {"lat": 34.05, "lng": -118.25, "profit": 100.0},
    {"lat": 40.71, "lng": -74.01, "profit": 0.0},
    {"lat": 41.88, "lng": -87.63, "profit": 50.0},
]


def _make_bubble_map() -> PointMapChart:
    return PointMapChart(
        id="c1",
        type="bubble_map",
        projection="albersUsa",
        query_name="q",
        latitude="lat",
        longitude="lng",
        size="profit",
    )


def _make_ctx(chart: PointMapChart, rows: list[dict[str, Any]]) -> WarningContext:
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
    )


# ---------------------------------------------------------------------------
# 1. Emitter drops negative-size rows from the emitted spec
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 2. WARN_POINT_MAP_NEGATIVE_SIZE_VALUES
# ---------------------------------------------------------------------------


def test_fires_on_negative_size_values() -> None:
    chart = _make_bubble_map()
    ctx = _make_ctx(chart, _MIXED_ROWS)
    warnings = detector.detect(ctx)

    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_POINT_MAP_NEGATIVE_SIZE_VALUES.code
    assert w.chart == "c1"
    assert w.field == "profit"
    assert "2" in w.message  # 2 negative rows
    assert "5" in w.message  # 5 total rows
    assert "profit" in w.message
    assert w.fix is not None
    assert "profit" in w.fix


def test_fires_when_all_rows_negative() -> None:
    chart = _make_bubble_map()
    ctx = _make_ctx(chart, _ALL_NEGATIVE_ROWS)
    warnings = detector.detect(ctx)

    assert len(warnings) == 1
    assert "2" in warnings[0].message  # 2 of 2 dropped


def test_no_warning_when_no_negative_values() -> None:
    chart = _make_bubble_map()
    ctx = _make_ctx(chart, _NO_NEGATIVE_ROWS)
    assert detector.detect(ctx) == []


def test_no_warning_for_zero_only() -> None:
    """Zero is not negative — pinned explicitly, the obvious off-by-one."""
    chart = _make_bubble_map()
    rows = [{"lat": 34.05, "lng": -118.25, "profit": 0.0}]
    ctx = _make_ctx(chart, rows)
    assert detector.detect(ctx) == []


def test_no_warning_without_size_field() -> None:
    """A plain point_map (no `size:` authored) has no size measure to check."""
    chart = PointMapChart(
        id="c1",
        type="point_map",
        projection="albersUsa",
        query_name="q",
        latitude="lat",
        longitude="lng",
    )
    ctx = _make_ctx(chart, _MIXED_ROWS)
    assert detector.detect(ctx) == []
