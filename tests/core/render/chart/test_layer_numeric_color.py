"""A layer colored by a numeric field draws its own sequential scale."""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_STYLE, _CTX = resolve_style_and_context(get_theme_style("stark"))

_ROWS: list[dict[str, Any]] = [
    {"x": "N", "y": 100, "z": 40, "lo": 20, "xn": 1, "num": 1, "grp": "a"},
    {"x": "S", "y": 90, "z": 50, "lo": 30, "xn": 2, "num": 2, "grp": "b"},
    {"x": "E", "y": 110, "z": 60, "lo": 10, "xn": 3, "num": 3, "grp": "a"},
]

_BASES: dict[str, dict[str, Any]] = {
    "bar": {"chart_type": "bar"},
    "bar-stacked": {"chart_type": "bar", "color": "grp"},
    "bar-ranged": {"chart_type": "bar", "y_start": "lo"},
    "line": {"chart_type": "line"},
    "area": {"chart_type": "area"},
    "scatter": {"chart_type": "scatter", "x": "xn"},
}

_LAYERS: dict[str, dict[str, Any]] = {
    "bar": {"type": "bar", "y": "z"},
    "line": {"type": "line", "y": "z"},
    "area": {"type": "area", "y": "z"},
    "scatter": {"type": "scatter", "y": "z"},
    "bar-ranged": {"type": "bar", "y": "y", "y_start": "lo"},
}


def test_an_all_null_layer_color_is_refused_by_name(make_chart: Any) -> None:
    rows = [{**row, "num": None} for row in _ROWS]
    chart = make_chart(
        chart_type="bar",
        x="x",
        y="y",
        layers=[{"type": "line", "y": "z", "color": "num"}],
    )
    ctx = resolve_chart_style_context(get_theme_style("stark"))
    resolved = resolve(chart, rows, chart_style_context=ctx)
    with pytest.raises(ChartDataError) as err:
        render_resolved_chart(resolved, rows, _STYLE, width=400)
    assert err.value.code is not None
    assert err.value.code.code == "ERR-LAYER-COLOR-ALL-NULL"
