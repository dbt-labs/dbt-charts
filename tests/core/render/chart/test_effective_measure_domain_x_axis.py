"""Pins ``effective_measure_domain``'s reduction on an unauthored x-axis.

Unlike y, resolve never bakes ``domain_min``/``domain_max`` for x, and bakes a
zero anchor only for a percent-formatted x (``test_scatter_x_zero_anchor.py``).
So on a non-percent x the function reduces
to "authored domain, else the data extent" — exactly what Vega-Lite auto-fits
an unpinned axis to. This is the fact the quantitative-x zero rule
(``x_threshold_fires`` in ``libs/chart-svg``) depends on; a future x-side headroom bake
that silently starts populating ``domain_min``/``domain_max`` for x would
change this reduction, and this test would catch it because it resolves a
real scatter chart through ``resolve()`` -> ``plan_cartesian()`` ->
``build_cartesian_axes()`` -- the same path a chart takes at render time --
rather than constructing the axis by hand.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import ScatterChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.chart.emitters._cartesian import effective_measure_domain
from dbt_charts.core.render.chart.type_inference import is_zero_anchored

_ROWS: list[dict[str, Any]] = [
    {"x": 10.0, "y": 1.0},
    {"x": 480.0, "y": 2.0},
]


def _resolved_axis_x():
    reset_config()
    ctx = resolve_chart_style_context(get_theme_style())
    chart = ScatterChart(id="t", type="scatter", x="x", y="y")
    resolved = resolve(chart, _ROWS, chart_style_context=ctx)
    return resolved.style.axis_x


def test_unauthored_x_axis_bakes_no_domain_edges() -> None:
    """resolve never bakes domain_min/domain_max for x -- both None."""
    ax = _resolved_axis_x()
    assert ax.domain_min is None
    assert ax.domain_max is None


def test_unauthored_x_axis_is_never_zero_anchored() -> None:
    """The smart-zero heuristic only runs against y -- an unauthored x-axis's
    scale is never anchored, so is_zero_anchored is always False there."""
    ax = _resolved_axis_x()
    assert is_zero_anchored(ax.scale) is False


def test_effective_measure_domain_on_x_reduces_to_data_extent() -> None:
    """With no authored domain and no baked edges, the reduction is exactly
    the raw data extent -- what Vega-Lite auto-fits an unpinned x-axis to."""
    ax = _resolved_axis_x()
    assert effective_measure_domain(ax, (10.0, 480.0)) == (10.0, 480.0)


def test_effective_measure_domain_on_x_with_no_data_is_none() -> None:
    ax = _resolved_axis_x()
    assert effective_measure_domain(ax, None) is None
