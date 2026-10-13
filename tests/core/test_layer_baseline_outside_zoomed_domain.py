"""Regression: a bar/area layer on a zoomed (non-zero-anchored) shared y-scale
must not collapse the plot.

A bar or area mark paints from the value-0 baseline to its value. When the
base line's y-scale is zoomed (the data sits far from zero, so the axis does
not start at 0), that baseline lies far below the pinned domain. Under this
repo's ``autosize: fit`` Vega shrinks the whole plot to fit every mark's
paint, so an unclipped layer collapses the plot to a zero-height strip. This
is the "recession band" shape: a constant-valued area layer over a date
sub-range, drawn on a line of large values.
"""

from __future__ import annotations

import datetime
import json
import re
import xml.etree.ElementTree as element_tree
from typing import Any

import pytest
import vl_convert
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.authored._layer import CartesianLayer
from dbt_charts.core.compile.models.chart.normalized import LineChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedLineChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.chart.emitters.line import LineEmitter
from dbt_charts.core.render.chart.spec import RenderBox
from dbt_charts.core.render.chart.translate import translate_to_vl

_ROWS: list[dict[str, Any]] = [
    {
        "month": datetime.date(2006 + i // 12, i % 12 + 1, 1),
        "total": 130000 + 100 * i,
    }
    for i in range(48)
]
_BAND: list[dict[str, Any]] = [
    {"month": row["month"], "total": 145000} for row in _ROWS[12:30]
]


def _plot_height(layer_type: str, theme: str) -> float:
    chart = LineChart(
        id="trend",
        type="line",
        x="month",
        y="total",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        variable_dependencies=frozenset(),
        layers=[
            TypeAdapter(CartesianLayer).validate_python(
                {"type": layer_type, "x": "month", "y": "total", "query": "band"}
            )
        ],
    )
    datasets: dict[str, list[dict[str, Any]]] = {"q": _ROWS, "band": _BAND}
    context = resolve_chart_style_context(get_theme_style(theme))
    resolved = resolve(chart, _ROWS, context, datasets=datasets)
    assert isinstance(resolved, ResolvedLineChart)
    spec: dict[str, Any] = translate_to_vl(
        LineEmitter().emit(
            resolved,
            RenderBox(width=600.0, height=300.0),
            regroup((), _ROWS),
            datasets=dict(datasets.items()),
        )
    )
    root = element_tree.fromstring(
        vl_convert.vegalite_to_svg(json.loads(json.dumps(spec, default=str)))
    )
    ns = "{http://www.w3.org/2000/svg}"
    background = next(
        path.attrib["d"]
        for path in root.iter(f"{ns}path")
        if path.attrib.get("class") == "background"
    )
    match = re.match(r"M[\d.]+,[\d.]+h[\d.]+v([\d.]+)h", background)
    assert match is not None, f"unrecognized plot background path: {background!r}"
    return float(match.group(1))


# clarity bakes domain bounds on a zoomed axis; stark leaves ticks.count unset
# and bakes none, so Vega-Lite fits the data alone and the layer's 0 baseline
# is not a data value.
@pytest.mark.parametrize("theme", ["clarity", "stark"])
@pytest.mark.parametrize("layer_type", ["area", "bar"])
def test_baseline_anchored_layer_on_zoomed_scale_keeps_the_plot(
    layer_type: str, theme: str
) -> None:
    assert _plot_height(layer_type, theme) > 100.0


@pytest.mark.parametrize("theme", ["clarity", "stark"])
def test_line_layer_on_zoomed_scale_is_unchanged(theme: str) -> None:
    assert _plot_height("line", theme) > 100.0
