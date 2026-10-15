"""Tests for Vega-Lite arc/pie chart behavior.

Split from test_vega_lite.py — arc chart aggregation tests.
"""

import re

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import PieChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

from ._engine_render import render_pie_svg

_BOARD_STYLE = resolve_style(get_theme_style())


def _wedges(svg: str) -> list[str]:
    return re.findall(r'<path d="([^"]+)"[^>]*aria-roledescription="arc mark"', svg)


class TestArcChartNoHiddenAggregation:
    """Arc/pie charts should NOT silently aggregate data."""

    def test_donut_draws_one_wedge_per_row(self, make_chart):
        """A donut draws every row as its own wedge; a repeated category is
        never summed."""
        chart = make_chart(
            "pie", x="category", y="value", style={"pie": {"inner_radius": 0.5}}
        )
        data = [
            {"category": "A", "value": 30},
            {"category": "A", "value": 20},
            {"category": "B", "value": 70},
        ]
        assert len(_wedges(render_pie_svg(chart, data))) == 3

    def test_pie_draws_one_wedge_per_row(self, make_chart):
        chart = make_chart("pie", x="category", y="value")
        data = [
            {"category": "A", "value": 30},
            {"category": "A", "value": 20},
            {"category": "B", "value": 70},
        ]
        assert len(_wedges(render_pie_svg(chart, data))) == 3

    def test_arc_type_rejected_by_generate_vega_lite_spec(self):
        """arc is not an authorable chart type (not in the Chart union). A chart
        forced into that state (e.g. by a bug elsewhere) fails fast in resolve()
        rather than silently rendering."""
        chart = PieChart(
            id="t",
            type="pie",
            theta="value",
            color="category",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
        )
        object.__setattr__(chart, "type", "arc")
        data = [{"category": "A", "value": 30}]
        with pytest.raises(ValueError, match="Unknown normalized chart type"):
            generate_vega_lite_spec(chart, data)


class TestArcChartSpacing:
    """The wheel's radius fits the plot's shorter side."""

    @pytest.mark.parametrize(("width", "height"), [(300, 300), (600, 200), (200, 600)])
    def test_pie_radius_fits_the_short_axis(self, make_chart, width, height):
        chart = make_chart("pie", x="category", y="value")
        data = [{"category": "A", "value": 30}, {"category": "B", "value": 70}]
        svg = render_pie_svg(chart, data, width=width, height=height)
        outer = max(float(r) for r in re.findall(r"A([\d.]+),\1,", svg))
        assert outer <= min(width, height) / 2

    def test_donut_hole_scales_with_the_wheel(self, make_chart):
        chart = make_chart(
            "pie", x="category", y="value", style={"pie": {"inner_radius": 0.5}}
        )
        data = [{"category": "A", "value": 30}, {"category": "B", "value": 70}]
        for size in (300, 500):
            wedge = _wedges(render_pie_svg(chart, data, width=size, height=size))[0]
            radii = sorted({float(r) for r in re.findall(r"A([\d.]+),\1,", wedge)})
            assert radii[-2] / radii[-1] == pytest.approx(0.5, abs=1e-3)
