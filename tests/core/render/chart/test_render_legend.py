"""Tests for legend.title.visible propagation through v2 emitters.

Regression coverage for the feature added in PR #4597: legend.title.visible: false
must propagate to v2 bar/pie/line/area/scatter encoding.color.legend.title.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.normalized import PieChart
from dbt_charts.core.compile.models.style.authored import PieChartStylePatch

from ..._engine_render import render_pie_svg
from ..._svg_render import legend_label_order

# ---------------------------------------------------------------------------
# pie: legend.title.visible: false still works after refactor (regression)
# ---------------------------------------------------------------------------


def test_pie_legend_title_visible_false_still_works() -> None:
    """legend.title.visible: false drops the engine-drawn legend's title, and
    only the title."""
    rows = [{"sales": 100, "category": "A"}, {"sales": 200, "category": "B"}]

    def svg(legend: dict[str, object]) -> str:
        chart = PieChart(
            id="pie_color",
            type="pie",
            theta="sales",
            color="category",
            query_name="q",
            style=PieChartStylePatch.model_validate({"legend": legend}),
        )
        return render_pie_svg(chart, rows)

    shown = svg({"visible": True})
    hidden = svg({"visible": True, "title": {"visible": False}})
    assert ">Category</text>" in shown
    assert ">Category</text>" not in hidden
    assert legend_label_order(hidden) == ["A", "B"]
