"""Tests for the WARN_SCATTER_STACKED_IN_COLUMNS render-warning detector.

Detection rule: a numeric scatter channel with at most 5 distinct values and an
average of at least 4 points per value draws columns of dots, not a relationship.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.normalized import ScatterChart
from dbt_charts.core.diagnostics import WARN_SCATTER_STACKED_IN_COLUMNS
from dbt_charts.core.render.warnings import (
    WarningContext,
    scatter_stacked_in_columns as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart


def _ctx(rows: list[dict[str, Any]]) -> WarningContext:
    chart = ScatterChart(id="c1", type="scatter", query_name="q", x="x", y="y")
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(board_spec=board, chart_results={resolved.id: rows})


def test_two_columns_of_sixty_points_warn() -> None:
    rows = [{"x": i % 2, "y": i * 1.5} for i in range(60)]
    (w,) = detector.detect(_ctx(rows))
    assert w.code == WARN_SCATTER_STACKED_IN_COLUMNS.code
    assert w.field == "x"
    assert w.path == "charts.c1.x"
    assert w.message == (
        "x has 2 distinct values across 60 points, so the dots stack in columns. "
        "Treat x as a category and use a strip plot or beeswarm to show each "
        "group's spread."
    )


def test_two_hundred_rows_over_four_tiers_warn() -> None:
    rows = [{"x": i, "y": (i % 4) * 100} for i in range(200)]
    (w,) = detector.detect(_ctx(rows))
    assert w.field == "y"
    assert "y has 4 distinct values across 200 points" in w.message


def test_few_deliberate_points_stay_silent() -> None:
    rows = [{"x": i % 3, "y": i * 1.5} for i in range(9)]
    assert detector.detect(_ctx(rows)) == []


def test_continuous_scatter_stays_silent() -> None:
    rows = [{"x": i * 0.37, "y": i * 1.5} for i in range(60)]
    assert detector.detect(_ctx(rows)) == []


def test_string_channel_stays_silent() -> None:
    rows = [{"x": "ab"[i % 2], "y": i * 1.5} for i in range(60)]
    assert detector.detect(_ctx(rows)) == []


def test_bool_channel_stays_silent() -> None:
    rows = [{"x": bool(i % 2), "y": i * 1.5} for i in range(60)]
    assert detector.detect(_ctx(rows)) == []


def test_six_distinct_values_stay_silent() -> None:
    rows = [{"x": i % 6, "y": i * 1.5} for i in range(120)]
    assert detector.detect(_ctx(rows)) == []
