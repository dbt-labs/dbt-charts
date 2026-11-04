"""One `color` per y axis: it paints that axis's gridlines, tick labels and title.

`auto` takes the axis's series ink, baked at resolve; the engine payload never
carries the keyword. A more specific key (`grid.color`, `labels.font.color`,
`title.font.color`) beats the axis color.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from dbt_charts.core.colors import (
    composite_over,
    ensure_readable_ink,
    opacity_for_contrast,
    parse_css_color,
    rgb01_to_hex,
    wcag_contrast,
)
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart as NBarChart,
    LineChart as NLineChart,
)
from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLineLayer
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    BarChartStylePatch,
    LineChartStylePatch,
)
from dbt_charts.core.compile.resolve import resolve

from .test_layer_axis_y import _DATA, _default_board_style

INK = "#1b6ca8"
RIGHT_AUTO = {"position": "right", "color": "auto"}


def _chart(layers: list[LineLayer], style: dict[str, Any] | None = None) -> NBarChart:
    return NBarChart(
        id="c",
        type="bar",
        x="month",
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        variable_dependencies=set(),
        layers=layers,
        style=BarChartStylePatch.model_validate(style) if style else None,
    )


def _layer(**axis_y: Any) -> LineLayer:
    return LineLayer(type="line", y="target", axis_y=axis_y)


def _resolve(layers: list[LineLayer], style: dict[str, Any] | None = None) -> Any:
    return resolve(_chart(layers, style), _DATA, _default_board_style())


def _code(excinfo: pytest.ExceptionInfo[CompilationError]) -> str:
    assert excinfo.value.code is not None
    return excinfo.value.code.code


def test_layer_axis_y_grid_authors_color_width_dash_opacity() -> None:
    layer = _layer(
        grid={"color": "#112233", "width": 2, "dash": [6, 3], "opacity": 0.5}
    )
    assert layer.axis_y is not None and layer.axis_y.grid is not None
    assert layer.axis_y.grid.dash == [6, 3]
    assert layer.axis_y.grid.opacity == 0.5


def test_axis_color_rejects_a_non_color() -> None:
    with pytest.raises(ValueError, match="color"):
        _layer(color="#12")


def test_layer_axis_color_paints_grid_labels_and_title() -> None:
    rl = _resolve([_layer(position="right", color=INK)]).layers[0]
    assert isinstance(rl, ResolvedLineLayer)
    assert rl.axis_y.grid is not None and rl.axis_y.grid.color == INK
    assert rl.axis_y.labels is not None and rl.axis_y.labels.font_color is not None
    assert (
        wcag_contrast(rl.axis_y.labels.font_color, _default_board_style().ink_canvas)
        >= 4.5
    )
    assert rl.axis_y.title_color == rl.axis_y.labels.font_color


def test_layer_axis_color_auto_bakes_the_layer_ink() -> None:
    rl = _resolve([_layer(**RIGHT_AUTO)]).layers[0]
    assert isinstance(rl, ResolvedLineLayer) and rl.ink is not None
    assert rl.axis_y.grid is not None and rl.axis_y.grid.color == rl.ink
    assert rl.axis_y.labels is not None
    canvas = _default_board_style().ink_canvas
    assert rl.axis_y.labels.font_color == ensure_readable_ink(rl.ink, canvas)
    assert "auto" not in json.dumps(rl.axis_y, default=lambda o: o.__dict__)


def test_layer_grid_color_beats_axis_color() -> None:
    rl = _resolve(
        [_layer(position="right", color=INK, grid={"color": "#aa0000"})]
    ).layers[0]
    assert rl.axis_y.grid is not None and rl.axis_y.grid.color == "#aa0000"
    assert rl.axis_y.labels is not None and rl.axis_y.labels.font_color is not None


def test_layer_color_auto_on_a_gradient_raises() -> None:
    layer = LineLayer(
        type="line",
        y="target",
        color="target",
        axis_y={"position": "right", "color": "auto"},
    )
    with pytest.raises(CompilationError, match="colors its marks by a field"):
        _resolve([layer])


def test_layer_grid_dash_reaches_the_resolved_layer() -> None:
    rl = _resolve([_layer(position="right", grid={"dash": [6, 3]})]).layers[0]
    assert rl.axis_y.grid is not None and rl.axis_y.grid.dash == [6, 3]
    assert rl.axis_y.labels is None or rl.axis_y.labels.font_color is None


def test_layer_color_auto_with_a_color_channel_raises() -> None:
    layer = LineLayer(type="line", y="target", color="month", axis_y=RIGHT_AUTO)
    with pytest.raises(CompilationError) as excinfo:
        _resolve([layer])
    assert _code(excinfo) == "ERR-AXIS-COLOR-AUTO-NO-INK"
    assert "layers[0].axis_y.color" in str(excinfo.value)


def test_base_axis_color_auto_paints_the_base_series_ink() -> None:
    resolved = _resolve([_layer(position="right")], style={"axis_y": {"color": "auto"}})
    grid, labels, title = (
        resolved.style.axis_y.grid,
        resolved.style.axis_y.labels,
        resolved.style.axis_y.title,
    )
    ink = grid.color
    assert ink is not None and ink != "auto"
    assert labels.font.color == ensure_readable_ink(
        ink, _default_board_style().ink_canvas
    )
    assert title.font.color == labels.font.color


def test_base_grid_color_beats_axis_color() -> None:
    resolved = _resolve(
        [_layer(position="right")],
        style={"axis_y": {"color": "auto", "grid": {"color": "#aa0000"}}},
    )
    assert resolved.style.axis_y.grid.color == "#aa0000"
    assert resolved.style.axis_y.labels.font.color != "#aa0000"


@pytest.mark.parametrize(
    "extra", [{"labels": {"angle": 45}}, {"title": {"visible": True}}]
)
def test_axis_color_beside_a_fontless_labels_or_title_block(
    extra: dict[str, Any],
) -> None:
    style = {"axis_y": {"color": INK, **extra}}
    resolved = _resolve([_layer(position="right")], style=style)
    assert resolved.style.axis_y.grid.color == INK


def test_an_authored_label_font_color_beats_axis_color() -> None:
    style = {"axis_y": {"color": INK, "labels": {"font": {"color": "#aa0000"}}}}
    resolved = _resolve([_layer(position="right")], style=style)
    assert resolved.style.axis_y.labels.font.color == "#aa0000"


def test_base_axis_color_auto_on_a_shared_axis_raises() -> None:
    with pytest.raises(CompilationError) as excinfo:
        _resolve([_layer()], style={"axis_y": {"color": "auto"}})
    assert _code(excinfo) == "ERR-AXIS-COLOR-AUTO-NO-INK"
    assert "style.axis_y.color" in str(excinfo.value)


def test_base_axis_color_auto_with_a_color_channel_raises() -> None:
    chart = _chart([_layer(position="right")], {"axis_y": {"color": "auto"}})
    chart = chart.model_copy(update={"color": "month"})
    with pytest.raises(CompilationError) as excinfo:
        resolve(chart, _DATA, _default_board_style())
    assert _code(excinfo) == "ERR-AXIS-COLOR-AUTO-NO-INK"


_BOARD = """
title: T
queries:
  q:
    type: values
    columns: [month, revenue, conversion_pct]
    values:
      - ["2026-01", 42000, 3.1]
      - ["2026-02", 51000, 3.4]
      - ["2026-03", 68000, 4.2]
charts:
  c:
    query: q
    type: line
    x: month
    y: revenue
    style:
      axis_y:
        color: auto
        grid:
          visible: true
    layers:
      - type: line
        y: conversion_pct
        axis_y:
          position: right
          color: auto
          grid:
            visible: true
            dash: [6, 3]
rows:
  - c
"""


def _auto_colors(node: Any) -> list[str]:
    """Every `…color` key in the payload still holding the `auto` keyword."""
    if isinstance(node, dict):
        found = [k for k, v in node.items() if k.endswith("color") and v == "auto"]
        return found + [hit for v in node.values() for hit in _auto_colors(v)]
    if isinstance(node, list):
        return [hit for v in node for hit in _auto_colors(v)]
    return []


def test_engine_payload_carries_colors_never_auto_and_dash_renders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dbt_charts import _engine

    from ._svg_render import render_board_to_svg

    seen: list[str] = []
    real = _engine.render_chart

    def spy(payload_json: str) -> Any:
        seen.append(payload_json)
        return real(payload_json)

    monkeypatch.setattr(_engine, "render_chart", spy)
    svg = render_board_to_svg(_BOARD)
    assert seen
    for payload in seen:
        assert not _auto_colors(json.loads(payload))
    assert 'stroke-dasharray="6,3"' in svg


def test_base_axis_color_auto_follows_an_authored_line_stroke() -> None:
    chart = NLineChart(
        id="c",
        type="line",
        x="month",
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        variable_dependencies=set(),
        layers=[_layer(**RIGHT_AUTO)],
        style=LineChartStylePatch.model_validate(
            {
                "axis_y": {"color": "auto"},
                "marks": {"line": {"stroke": {"color": INK}}},
            }
        ),
    )
    resolved = resolve(chart, _DATA, _default_board_style())
    assert resolved.style.axis_y.grid.color == INK


@pytest.mark.parametrize("ink", ["#1b6ca8", "#c0392b", "#4a9fd4", "#f2c14e"])
@pytest.mark.parametrize("canvas", ["#ffffff", "#161616"])
def test_grid_opacity_lands_the_ink_at_the_target_contrast(
    ink: str, canvas: str
) -> None:
    target = 1.4
    opacity = opacity_for_contrast(ink, canvas, target)
    painted = composite_over(
        (*parse_css_color(ink)[:3], opacity), parse_css_color(canvas)
    )
    assert wcag_contrast(rgb01_to_hex(*painted[:3]), canvas) == pytest.approx(
        target, abs=0.02
    )


def test_an_ink_fainter_than_the_target_stays_opaque() -> None:
    assert opacity_for_contrast("#f4f4f4", "#ffffff", 1.4) == 1.0


def test_axis_color_softens_the_grid_to_the_theme_grid_contrast() -> None:
    resolved = _resolve([_layer(position="right")], style={"axis_y": {"color": INK}})
    grid = resolved.style.axis_y.grid
    style = _default_board_style()
    assert grid.opacity == opacity_for_contrast(
        INK, style.ink_canvas, wcag_contrast(style.axis_y.grid.color, style.ink_canvas)
    )


def test_layer_axis_color_softens_its_grid_and_authored_opacity_wins() -> None:
    soft = _resolve([_layer(position="right", color=INK)]).layers[0].axis_y.grid
    assert soft is not None and soft.opacity is not None and soft.opacity < 1
    kept = _resolve(
        [_layer(position="right", color=INK, grid={"opacity": 0.8})]
    ).layers[0]
    assert kept.axis_y.grid is not None and kept.axis_y.grid.opacity == 0.8


def test_an_authored_grid_color_is_not_softened() -> None:
    own = _resolve(
        [_layer(position="right", color=INK, grid={"color": "#112233"})]
    ).layers[0]
    assert own.axis_y.grid is not None and own.axis_y.grid.opacity is None


@pytest.mark.parametrize(
    "axis_y", [{"color": INK}, {"grid": {"dash": [4, 2]}}, {"grid": {"color": INK}}]
)
def test_layer_axis_style_without_a_position_raises(axis_y: dict[str, Any]) -> None:
    with pytest.raises(CompilationError, match="no layer on this chart pins"):
        _resolve([_layer(**axis_y)])
