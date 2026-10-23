"""Tests for the PIE_DOMINANT_SEGMENT render-warning detector.

Detection rule: fires on pie charts where one slice is >= 95% of the total —
the chart is effectively a single value.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.normalized import BarChart, Chart, PieChart
from dbt_charts.core.diagnostics import WARN_PIE_DOMINANT_SEGMENT, Diagnostic
from dbt_charts.core.render.warnings import (
    WarningContext,
    pie_dominant_segment as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart


def _make_pie(**kwargs: object) -> Chart:
    return PieChart(**{"id": "c1", "type": "pie", "query_name": "q", **kwargs})


def _make_ctx(chart: Chart, rows: list[dict[str, Any]]) -> WarningContext:
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
    )


_DOMINANT = [
    {"category": "A", "amount": 980},
    {"category": "B", "amount": 12},
    {"category": "C", "amount": 8},
]

_BALANCED = [
    {"category": "A", "amount": 50},
    {"category": "B", "amount": 30},
    {"category": "C", "amount": 20},
]


def test_fires_when_one_segment_dominates() -> None:
    chart = _make_pie(theta="amount", color="category")
    warnings = detector.detect(_make_ctx(chart, _DOMINANT))
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, Diagnostic)
    assert w.code == WARN_PIE_DOMINANT_SEGMENT.code
    assert w.field == "amount"
    assert w.fix is not None


def test_no_fire_when_balanced() -> None:
    chart = _make_pie(theta="amount", color="category")
    assert detector.detect(_make_ctx(chart, _BALANCED)) == []


def test_no_fire_for_non_pie() -> None:
    chart = BarChart(id="c1", type="bar", query_name="q", x="category", y="amount")
    assert detector.detect(_make_ctx(chart, _DOMINANT)) == []


def test_no_fire_with_single_positive_slice() -> None:
    """One slice can't 'dominate' the others if there are no others."""
    chart = _make_pie(theta="amount", color="category")
    rows = [{"category": "A", "amount": 100}, {"category": "B", "amount": 0}]
    assert detector.detect(_make_ctx(chart, rows)) == []


def test_no_fire_when_theta_absent_from_data() -> None:
    # theta is required on a pie, but when it names a column that isn't in the
    # data the detector finds no numeric slices and must not fire.
    chart = _make_pie(theta="not_a_column", color="category")
    assert detector.detect(_make_ctx(chart, _DOMINANT)) == []
