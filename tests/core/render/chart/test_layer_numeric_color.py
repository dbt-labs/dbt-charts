"""A layer colored by a numeric field draws its own sequential scale."""

from __future__ import annotations

from typing import Any

import pytest
import vl_convert

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


def _color_encodings(node: Any, field: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        color = node.get("color")
        if isinstance(color, dict) and color.get("field") == field:
            found.append(color)
        for value in node.values():
            found.extend(_color_encodings(value, field))
    elif isinstance(node, list):
        for item in node:
            found.extend(_color_encodings(item, field))
    return found


@pytest.mark.parametrize("layer", sorted(_LAYERS))
@pytest.mark.parametrize("base", sorted(_BASES))
def test_a_numeric_layer_color_builds_its_own_scale(
    make_chart: Any, capfd: pytest.CaptureFixture[str], base: str, layer: str
) -> None:
    if layer == "bar-ranged" and base != "bar-ranged":
        pytest.skip("a ranged layer needs a ranged base")
    chart = make_chart(
        **{"x": "x", "y": "y", **_BASES[base]},
        layers=[{**_LAYERS[layer], "color": "num"}],
    )
    ctx = resolve_chart_style_context(get_theme_style("stark"))
    resolved = resolve(chart, _ROWS, chart_style_context=ctx)
    spec = render_resolved_chart(resolved, _ROWS, _STYLE, width=400).payload

    encodings = _color_encodings(spec, "num")
    assert encodings
    assert all("scale" not in enc for enc in encodings), encodings
    assert spec["resolve"]["scale"]["color"] == "independent"

    capfd.readouterr()
    vl_convert.vegalite_to_svg(spec)
    assert "TypeError" not in capfd.readouterr().err


def test_layers_on_the_shared_scale_draw_no_legend_beside_a_numeric_layer(
    make_chart: Any,
) -> None:
    chart = make_chart(
        x="x",
        y="y",
        layers=[
            {"type": "scatter", "y": "z", "color": "grp"},
            {"type": "line", "y": "z", "color": "num"},
        ],
    )
    ctx = resolve_chart_style_context(get_theme_style("stark"))
    resolved = resolve(chart, _ROWS, chart_style_context=ctx)
    spec = render_resolved_chart(resolved, _ROWS, _STYLE, width=400).payload

    def legends(node: Any) -> list[Any]:
        found: list[Any] = []
        if isinstance(node, dict):
            color = node.get("color")
            if (
                isinstance(color, dict)
                and "scale" in color
                and "domain" in color["scale"]
            ):
                found.append(color.get("legend"))
            for value in node.values():
                found.extend(legends(value))
        elif isinstance(node, list):
            for item in node:
                found.extend(legends(item))
        return found

    shared = legends(spec)
    assert len(shared) == 2
    assert sum(legend is not None for legend in shared) == 1


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


def test_a_null_among_numeric_layer_colors_keeps_the_own_scale(
    make_chart: Any,
) -> None:
    rows = [{**row, "num": None if row["x"] == "S" else row["num"]} for row in _ROWS]
    chart = make_chart(
        chart_type="bar",
        x="x",
        y="y",
        layers=[{"type": "line", "y": "z", "color": "num"}],
    )
    ctx = resolve_chart_style_context(get_theme_style("stark"))
    resolved = resolve(chart, rows, chart_style_context=ctx)
    spec = render_resolved_chart(resolved, rows, _STYLE, width=400).payload
    assert spec["resolve"]["scale"]["color"] == "independent"
