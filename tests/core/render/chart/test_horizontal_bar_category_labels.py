"""Regression: horizontal bar categorical y-axis must not parity-drop labels."""

from __future__ import annotations

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import render_chart

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())


def test_horizontal_bar_svg_includes_every_category_label(make_chart) -> None:
    owners = ["dave", "christoph", "sr-engineer", "akankaluga"]
    data = [{"owner": o, "count": 10 - i} for i, o in enumerate(owners)]
    chart = make_chart(
        "bar",
        x="owner",
        y="count",
        style=BarChartStylePatch.model_validate({"orientation": "horizontal"}),
    )
    svg = render_chart(
        chart,
        _BOARD_STYLE,
        _BOARD_CTX,
        data,
        format="svg",
        width=400,
        height=300,
    )
    for owner in owners:
        assert owner in svg, f"missing y-axis label for {owner!r} in rendered SVG"
