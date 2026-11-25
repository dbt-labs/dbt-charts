"""A layer must follow the base chart's orientation, not assume a vertical one.

`render_cartesian_overlay` hoists the base spec's category encoding as the outer
shared channel and builds every layer's measure on the opposite one. Which VL
channel each of those is depends entirely on the base chart's orientation: a
vertical bar puts the category on `x` and the measure on `y`, a horizontal bar
swaps them. Reading the pair as a literal `("x", "y")` shared the *measure* with
every layer on a horizontal base and bound the layer's own field to a second,
unmerged y scale — a chart that validated clean and plotted every layer point at
the base's value.
"""

from __future__ import annotations

import re

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.authored import (
    LineLayer,
)
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError

from ._engine_render import render_chart_svg

_DATA = [
    {"month": "Jan", "revenue": 100.0, "target": 4000.0},
    {"month": "Feb", "revenue": 200.0, "target": 5000.0},
    {"month": "Mar", "revenue": 150.0, "target": 4500.0},
]


def test_axis_mirror_is_refused_on_a_horizontal_layered_chart() -> None:
    """A horizontal base measures along x, so there is no y measure to mirror.

    A horizontal base leaves its CATEGORY on the outer y, so reading "no outer y"
    as the refusal's proxy once lapsed into mirroring the category axis instead.
    """
    orientation = "horizontal"
    board_style, board_ctx = resolve_style_and_context(get_theme_style())
    chart = BarChart(
        id="c",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="month",
        y="revenue",
        layers=[LineLayer(type="line", y="target")],
        style=BarChartStylePatch.model_validate(
            {"orientation": orientation, "axis_y": {"mirror": True}}
        ),
    )
    with pytest.raises(ChartDataError, match="mirror"):
        render_chart_svg(
            chart, _DATA, board_style=board_style, chart_style_context=board_ctx
        )


_SPLIT_DATA = [
    {"region": "North", "channel": "store", "revenue": 100, "profit": 30, "cost": 40},
    {"region": "North", "channel": "web", "revenue": 60, "profit": 20, "cost": 30},
    {"region": "South", "channel": "store", "revenue": 80, "profit": 25, "cost": 50},
    {"region": "South", "channel": "web", "revenue": 90, "profit": 35, "cost": 20},
]


@pytest.mark.parametrize(
    ("y", "color", "stack"),
    [
        ("revenue", "channel", "none"),
        ("revenue", "channel", "zero"),
        (["revenue", "profit"], None, None),
    ],
    ids=["color-split", "stacked", "multi-y"],
)
def test_an_area_layer_over_a_split_horizontal_bar_fills_from_zero_along_x(
    y: str | list[str], color: str | None, stack: str | None
) -> None:
    """Each area-layer fill runs from the zero rule to its row's value along x.

    Area layers never stack, so a horizontal base split by color, stacked, or
    folded from a y list draws the layer exactly as its vertical twin does,
    transposed.
    """
    style = {"orientation": "horizontal"} | ({"stack": stack} if stack else {})
    chart = BarChart(
        id="c",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="region",
        y=y,
        color=color,
        layers=[{"type": "area", "y": "cost"}],
        style=BarChartStylePatch.model_validate(style),
    )
    data = _SPLIT_DATA if color else _SPLIT_DATA[::2]
    svg = render_chart_svg(chart, data, width=400)
    (fill,) = re.findall(r'<path d="([^"]*)" fill="[^"]*" fill-opacity=', svg)
    (outline,) = re.findall(r'<path d="([^"]*)" fill="none" [^>]*stroke-linecap=', svg)
    fill_xs = _xs(fill)
    outline_xs = _xs(outline)
    assert fill_xs[: len(outline_xs)] == outline_xs
    assert set(fill_xs[len(outline_xs) :]) == {0.0}
    px_per_unit = {x / row["cost"] for x, row in zip(outline_xs, data, strict=True)}
    assert max(px_per_unit) - min(px_per_unit) < 0.01


def _xs(d: str) -> list[float]:
    return [float(x) for x in re.findall(r"[ML](-?[\d.]+),", d)]
