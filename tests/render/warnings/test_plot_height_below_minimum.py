"""Tests for the PLOT_HEIGHT_BELOW_MINIMUM render-warning detector.

Detection rule: fires on a ``ResolvedBarChart`` whose resolved
``style.plot_height_below_floor`` is True -- the estimated plot height falls
below the calibrated floor, a fraction
(``PLOT_HEIGHT_FLOOR_RATIO``) of the card's own height,
checked at every width. The fact is baked at
resolve time (``estimate_plot_height()``, ``plot_height_floor.py``,
called from ``bar.py``'s ``_resolve_bar``); this detector only reads it off
``ctx.layout_charts``, the placement-matched chart instances.

No chrome is removed to fix this -- it is purely informational. Covers every
composition ``_resolve_bar`` produces the fact for (the fact is stamped
before any Vega-Lite composition choice is made): flat, layered, faceted
(``multiples``), stacked, and horizontal.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.authored._base import MultiplesConfig
from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
from dbt_charts.core.compile.models.chart.normalized import BarChart, KpiChart
from dbt_charts.core.diagnostics import WARN_PLOT_HEIGHT_BELOW_MINIMUM, Diagnostic
from dbt_charts.core.render.warnings import (
    WarningContext,
    plot_height_below_minimum as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart

_TINY_WIDTH = 300.0
_WIDE_WIDTH = 640.0

_GROUPED_ROWS: list[dict[str, Any]] = [
    {"quarter": q, "region": r, "revenue": 100.0}
    for q in ("Q1", "Q2", "Q3", "Q4")
    for r in ("NA", "EMEA", "APAC", "LATAM", "Other")
]


def _grouped_bar(**kwargs: Any) -> BarChart:
    return BarChart(
        **{
            "id": "c1",
            "type": "bar",
            "query_name": "q",
            "title": "Quarterly revenue by region",
            "subtitle": "Grouped by region across four fiscal quarters",
            "x": "quarter",
            "y": "revenue",
            "color": "region",
            "x_label": "Fiscal quarter",
            "y_label": "Revenue (USD)",
            **kwargs,
        }
    )


def _ctx(chart: Any, width: float | None, rows: list[dict[str, Any]]) -> WarningContext:
    resolved = make_test_resolved_chart(chart, rows, width=width)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs={},
        layout_charts={resolved.id: resolved},
    )


def test_fires_on_a_starved_flat_grouped_bar() -> None:
    warnings = detector.detect(_ctx(_grouped_bar(), _TINY_WIDTH, _GROUPED_ROWS))
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, Diagnostic)
    assert w.code == WARN_PLOT_HEIGHT_BELOW_MINIMUM.code
    assert w.chart == "c1"
    assert "c1" in w.message
    assert w.fix is not None


def test_fires_on_a_starved_stacked_bar() -> None:
    chart = _grouped_bar(style={"stack": "zero"})
    warnings = detector.detect(_ctx(chart, _TINY_WIDTH, _GROUPED_ROWS))
    assert len(warnings) == 1


def test_fires_on_a_starved_horizontal_bar() -> None:
    chart = _grouped_bar(style={"orientation": "horizontal"})
    warnings = detector.detect(_ctx(chart, _TINY_WIDTH, _GROUPED_ROWS))
    assert len(warnings) == 1


def test_fires_on_a_starved_layered_bar() -> None:
    chart = _grouped_bar(layers=[LineLayer(type="line", y="revenue")])
    warnings = detector.detect(_ctx(chart, _TINY_WIDTH, _GROUPED_ROWS))
    assert len(warnings) == 1


def test_fires_on_a_faceted_bar() -> None:
    """multiples splits the card into panels, but _estimate_bar_plot_height
    does not account for facet rows (a known, shared limitation with the
    sibling _stack_legend_should_yield estimator) -- it measures the same
    card-height estimate as the flat case, so it fires identically here. A
    rows-facet actually gives each panel LESS height than the whole card, so
    this under-estimates the squeeze rather than over-firing on it."""
    rows = [
        {**r, "segment": "Enterprise" if i % 2 else "SMB"}
        for i, r in enumerate(_GROUPED_ROWS)
    ]
    chart = _grouped_bar(multiples=MultiplesConfig(rows="segment"))
    warnings = detector.detect(_ctx(chart, _TINY_WIDTH, rows))
    assert len(warnings) == 1


def test_silent_on_a_wide_card() -> None:
    assert detector.detect(_ctx(_grouped_bar(), _WIDE_WIDTH, _GROUPED_ROWS)) == []


def test_silent_when_no_legend_and_nothing_else_starves_the_plot() -> None:
    """Single-series bar: nothing competes with the plot for height."""
    chart = _grouped_bar(color=None)
    warnings = detector.detect(_ctx(chart, _TINY_WIDTH, _GROUPED_ROWS))
    assert warnings == []


def test_silent_on_non_bar_chart() -> None:
    chart = KpiChart(id="c1", type="kpi", query_name="q", value="revenue")
    assert detector.detect(_ctx(chart, _TINY_WIDTH, _GROUPED_ROWS)) == []


def test_silent_when_chart_absent_from_layout_charts() -> None:
    """Charts not reached by the active layout tree are absent from
    layout_charts -- the detector must skip them."""
    resolved = make_test_resolved_chart(
        _grouped_bar(), _GROUPED_ROWS, width=_TINY_WIDTH
    )
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: _GROUPED_ROWS},
        vega_specs={},
        layout_charts={},  # chart absent from active layout
    )
    assert detector.detect(ctx) == []
