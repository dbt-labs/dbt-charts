"""Tests for the WIDE_MEASURE_LABEL_COLLISION render-warning detector.

Detection rule: fires on a wide `y: [...]` bar/area/line chart whose
measure list contains two or more column names that `field_title`
humanizes to the same text. Known from `y:` alone, no data needed. The
resolve-time fallback (colliding measures keep their raw names as
distinct series) lives in `wide_measure_labels_for`
(compile/resolve/chart/_wide_fields.py) and is exercised in
`tests/core/test_legend_values_resolution.py`; this file covers only the
warning.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    HeatmapChart,
    ScatterChart,
)
from dbt_charts.core.diagnostics import WARN_WIDE_MEASURE_LABEL_COLLISION, Diagnostic
from dbt_charts.core.render.warnings import (
    WarningContext,
    wide_measure_label_collision as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart


def _bar(**kwargs: Any) -> BarChart:
    return BarChart(
        **{
            "id": "c1",
            "type": "bar",
            "query_name": "q",
            "x": "month",
            **kwargs,
        }
    )


def _ctx(chart: Any, rows: list[dict[str, Any]]) -> WarningContext:
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
    )


def test_fires_when_two_measures_humanize_identically() -> None:
    chart = _bar(y=["churn_pct", "churn_percent"])
    rows = [{"month": "Jan", "churn_pct": 1, "churn_percent": 2}]
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, Diagnostic)
    assert w.code == WARN_WIDE_MEASURE_LABEL_COLLISION.code
    assert w.chart == "c1"
    assert "churn_pct" in w.message and "churn_percent" in w.message


def test_silent_when_measures_humanize_distinctly() -> None:
    chart = _bar(y=["revenue_usd", "cost_usd"])
    rows = [{"month": "Jan", "revenue_usd": 1, "cost_usd": 2}]
    assert detector.detect(_ctx(chart, rows)) == []


def test_silent_on_a_non_wide_chart() -> None:
    chart = _bar(y="revenue_usd", color="series")
    rows = [{"month": "Jan", "revenue_usd": 1, "series": "A"}]
    assert detector.detect(_ctx(chart, rows)) == []


def test_fires_when_two_scatter_measures_humanize_identically() -> None:
    """Same as ``test_fires_when_two_measures_humanize_identically``, for
    scatter's own wide fold -- scatter joined the wide-measures shape this
    detector already covers for bar/area/line."""
    chart = ScatterChart(
        id="c1",
        type="scatter",
        query_name="q",
        x="month",
        y=["churn_pct", "churn_percent"],
    )
    rows = [{"month": "Jan", "churn_pct": 1, "churn_percent": 2}]
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    assert "churn_pct" in warnings[0].message and "churn_percent" in warnings[0].message


def test_fires_when_two_heatmap_measures_humanize_identically() -> None:
    chart = HeatmapChart(
        id="c1",
        type="heatmap",
        query_name="q",
        x="month",
        y=["churn_pct", "churn_percent"],
    )
    rows = [{"month": "Jan", "churn_pct": 1, "churn_percent": 2}]
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    assert "churn_pct" in warnings[0].message and "churn_percent" in warnings[0].message


def test_silent_on_an_exact_duplicate_y_entry() -> None:
    """The same raw column authored twice in y: is not a humanization
    collision -- it's one measure, twice. Grouping must dedupe by
    distinct raw name, or a literal duplicate collapses to a
    same-measure "collision" and gets named twice in the message."""
    chart = _bar(y=["revenue", "revenue"])
    rows = [{"month": "Jan", "revenue": 1}]
    assert detector.detect(_ctx(chart, rows)) == []
