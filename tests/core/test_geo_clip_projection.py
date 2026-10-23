"""Regression tests for geo clip + out-of-projection warning.

Bug: albersUsa point_map with a Pacific coordinate (e.g. Kwajalein lat=9, lon=168)
renders a stray dot at the chart's top-left corner instead of being suppressed.

Three fixes ship together:
  1. clip: true on the circle mark — VL clips geo-positioned marks to the
     projection's viewport so out-of-bounds points never bleed into unrelated
     chart regions.
  2. Data filtering in the emitter — out-of-projection rows are dropped from
     spec.data before Vega-Lite sees them, because clip:true cannot hide a mark
     whose CENTER is exactly at (0,0) (the corner quarter still paints).
  3. POINT_MAP_OUT_OF_PROJECTION warning — tells the author N point(s) fall
     outside the projection bounds so they can fix the data or switch projection.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.normalized import PointMapChart
from dbt_charts.core.diagnostics import WARN_POINT_MAP_OUT_OF_PROJECTION
from dbt_charts.core.render.warnings import (
    WarningContext,
    point_map_out_of_projection as detector,
)

from ._board_utils import make_test_resolved_board, make_test_resolved_chart

# US points clearly within albersUsa (continental US)
_US_ROWS: list[dict[str, Any]] = [
    {"lat": 34.05, "lng": -118.25},  # Los Angeles
    {"lat": 40.71, "lng": -74.01},  # New York
    {"lat": 41.88, "lng": -87.63},  # Chicago
]

# Mix: 3 US + 1 Pacific (Kwajalein Atoll, Marshall Islands)
_MIXED_ROWS: list[dict[str, Any]] = [
    *_US_ROWS,
    {"lat": 9.0, "lng": 168.0},  # Kwajalein — outside albersUsa projection
]


def _make_point_map(projection: str = "albersUsa") -> PointMapChart:
    return PointMapChart(
        id="c1",
        type="point_map",
        query_name="q",
        latitude="lat",
        longitude="lng",
        projection=projection,
    )


def _make_ctx(chart: PointMapChart, rows: list[dict[str, Any]]) -> WarningContext:
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        # point_map not in vega_specs; detector reads chart_results
    )


# ---------------------------------------------------------------------------
# 1. clip: true on the point mark
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 2. POINT_MAP_OUT_OF_PROJECTION warning
# ---------------------------------------------------------------------------


def test_fires_on_out_of_projection_point() -> None:
    """albersUsa + Pacific coordinate → POINT_MAP_OUT_OF_PROJECTION fires."""
    chart = _make_point_map("albersUsa")
    ctx = _make_ctx(chart, _MIXED_ROWS)
    warnings = detector.detect(ctx)

    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_POINT_MAP_OUT_OF_PROJECTION.code
    assert w.chart == "c1"
    assert "1" in w.message  # 1 out-of-projection point
    assert "albersUsa" in w.message
    assert w.fix is not None


def test_no_warning_for_all_us_points() -> None:
    """All points within albersUsa bounds → no warning."""
    chart = _make_point_map("albersUsa")
    ctx = _make_ctx(chart, _US_ROWS)
    assert detector.detect(ctx) == []


def test_no_warning_for_unbounded_projection() -> None:
    """mercator covers the whole world — no out-of-projection check applied."""
    chart = _make_point_map("mercator")
    ctx = _make_ctx(chart, _MIXED_ROWS)
    assert detector.detect(ctx) == []


def test_warning_count_reflects_multiple_out_of_projection_points() -> None:
    """Warning message reports the correct count of out-of-projection points."""
    pacific_rows = [
        {"lat": 9.0, "lng": 168.0},  # Kwajalein
        {"lat": -17.7, "lng": 168.3},  # Vanuatu
        {"lat": 34.05, "lng": -118.25},  # LA — in bounds
    ]
    chart = _make_point_map("albersUsa")
    ctx = _make_ctx(chart, pacific_rows)
    warnings = detector.detect(ctx)

    assert len(warnings) == 1
    assert "2" in warnings[0].message


def test_no_warning_when_lat_lon_fields_missing_from_data() -> None:
    """If rows don't contain lat/lon keys, detector skips gracefully (no crash)."""
    chart = _make_point_map("albersUsa")
    rows = [{"something_else": 1}]
    ctx = _make_ctx(chart, rows)
    # Should not raise; rows with missing lat/lon columns are skipped
    warnings = detector.detect(ctx)
    assert warnings == []


# ---------------------------------------------------------------------------
# 3. Emitter drops out-of-projection rows from spec.data
# ---------------------------------------------------------------------------
