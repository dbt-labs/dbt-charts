"""Tests for Vega-Lite chart tooltip generation.

Split from test_vega_lite.py — tooltip-specific tests.
"""

import re

from ._engine_render import render_pie_svg


def _arc_labels(svg: str) -> list[str]:
    return re.findall(r'aria-label="([^"]+)"[^>]*aria-roledescription="arc mark"', svg)


class TestSpecialChartTypeTooltips:
    """Tests for tooltips on special chart types."""

    def test_pie_chart_has_tooltips(self, make_chart):
        chart = make_chart("pie", x="category", y="value")
        data = [{"category": "A", "value": 30}, {"category": "B", "value": 70}]
        labels = _arc_labels(render_pie_svg(chart, data))
        assert len(labels) == 2
        assert "A" in labels[0] and "30" in labels[0], labels
        assert "B" in labels[1] and "70" in labels[1], labels

    def test_pie_with_inner_radius_has_tooltips(self, make_chart):
        """Pie chart with inner_radius (donut appearance) has tooltips."""
        chart = make_chart(
            "pie", x="category", y="value", style={"pie": {"inner_radius": 0.5}}
        )
        data = [{"category": "A", "value": 30}, {"category": "B", "value": 70}]
        assert len(_arc_labels(render_pie_svg(chart, data))) == 2
