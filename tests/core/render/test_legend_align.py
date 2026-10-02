"""Centering a legend along its edge: the pure scenegraph geometry, the SVG
rewrite, and the rendered result for each edge and overlay mode.
"""

from __future__ import annotations

import re

import pytest

from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.converters.legend_align import (
    apply_legend_shifts,
    legend_shifts,
)

from .._svg_render import render_board_to_svg


def _scenegraph(*legends: dict[str, float], width: float = 400, height: float = 200):
    return {
        "scenegraph": {
            "role": "frame",
            "items": [
                {
                    "width": width,
                    "height": height,
                    "items": [
                        {"role": "axis", "items": [{"x": 0, "y": 0}]},
                        {"role": "legend", "items": list(legends)},
                    ],
                }
            ],
        }
    }


def test_a_top_legend_centers_horizontally_in_the_plot() -> None:
    graph = _scenegraph({"x": 0, "y": -40, "width": 100, "height": 30})
    assert legend_shifts(graph, "top", "center", "c") == [150.0]


def test_an_end_aligned_legend_slides_to_the_far_end_of_the_plot() -> None:
    graph = _scenegraph({"x": 0, "y": -40, "width": 100, "height": 30})
    assert legend_shifts(graph, "top", "end", "c") == [300.0]
    side = _scenegraph({"x": 410, "y": 0, "width": 60, "height": 80})
    assert legend_shifts(side, "right", "end", "c") == [120.0]


def test_a_side_legend_centers_vertically_in_the_plot() -> None:
    graph = _scenegraph({"x": 410, "y": 0, "width": 60, "height": 80})
    assert legend_shifts(graph, "right", "center", "c") == [60.0]


def test_stacked_legends_slide_together_and_keep_their_gap() -> None:
    graph = _scenegraph(
        {"x": 0, "y": 0, "width": 60, "height": 40},
        {"x": 0, "y": 50, "width": 60, "height": 40},
    )
    assert legend_shifts(graph, "left", "center", "c") == [55.0, 55.0]


def test_a_legend_wider_than_the_plot_cannot_be_centered() -> None:
    graph = _scenegraph({"x": 0, "y": -40, "width": 500, "height": 30})
    with pytest.raises(ChartDataError, match="larger than its plot"):
        legend_shifts(graph, "top", "center", "c")


def test_a_view_with_no_plot_size_cannot_hold_a_centered_legend() -> None:
    graph = _scenegraph({"x": 0, "y": 0, "width": 50, "height": 20}, width=0, height=0)
    with pytest.raises(ChartDataError, match="no size to align in"):
        legend_shifts(graph, "top", "center", "c")


def test_a_scenegraph_without_a_legend_is_an_error() -> None:
    with pytest.raises(ChartDataError, match="holds no legend"):
        legend_shifts(
            {"scenegraph": {"role": "frame", "items": []}}, "top", "center", "c"
        )


def test_the_svg_translate_moves_along_the_edge_only() -> None:
    svg = (
        '<g class="mark-group role-legend" role="graphics-symbol"><g transform='
        '"translate(0,-47)"><path/></g></g>'
    )
    top = apply_legend_shifts(svg, [150.0], "top", "c")
    assert "translate(150,-47)" in top
    left = apply_legend_shifts(svg, [20.0], "left", "c")
    assert "translate(0,-27)" in left


def test_a_legend_count_mismatch_is_an_error() -> None:
    with pytest.raises(ChartDataError, match="SVG holds 0 legends"):
        apply_legend_shifts("<svg/>", [1.0], "top", "c")


_BOARD = """
title: t
queries:
  q:
    type: values
    rows:
      - {{month: Jan, segment: Alpha, sessions: 10}}
      - {{month: Jan, segment: Beta, sessions: 20}}
      - {{month: Feb, segment: Alpha, sessions: 12}}
      - {{month: Feb, segment: Beta, sessions: 22}}
charts:
  c:
    query: q
    type: line
    x: month
    y: sessions
    color: segment
    style:
      endpoint_labels:
        visible: false
      legend:
        position:
          edge: {edge}
          align: {align}
          overlay: {overlay}
rows:
  - c
"""


def _legend_origin(svg: str) -> tuple[float, float]:
    match = re.search(
        r'role-legend"[^>]*><g transform="translate\((-?[\d.]+),(-?[\d.]+)\)"', svg
    )
    assert match, "no legend group in the rendered SVG"
    return float(match.group(1)), float(match.group(2))


def _render(edge: str, align: str, overlay: bool) -> str:
    return render_board_to_svg(
        _BOARD.format(edge=edge, align=align, overlay=str(overlay).lower())
    )


@pytest.mark.parametrize("overlay", [False, True])
@pytest.mark.parametrize("edge", ["top", "bottom"])
def test_centered_top_and_bottom_legends_move_right_of_the_start_aligned_one(
    edge: str, overlay: bool
) -> None:
    start_x, start_y = _legend_origin(_render(edge, "start", overlay))
    center_x, center_y = _legend_origin(_render(edge, "center", overlay))
    assert center_x > start_x
    assert center_y == start_y


@pytest.mark.parametrize("edge", ["left", "right"])
def test_centered_side_legends_move_down_the_edge_from_the_start_aligned_one(
    edge: str,
) -> None:
    start_x, start_y = _legend_origin(_render(edge, "start", False))
    center_x, center_y = _legend_origin(_render(edge, "center", False))
    assert center_y > start_y
    assert center_x == start_x


@pytest.mark.parametrize("edge", ["left", "right"])
def test_a_centered_side_overlay_sits_mid_height_inside_the_plot(edge: str) -> None:
    _x, reserved_y = _legend_origin(_render(edge, "center", False))
    _x, overlay_y = _legend_origin(_render(edge, "center", True))
    assert overlay_y == reserved_y


@pytest.mark.parametrize("edge", ["top", "left"])
def test_a_chart_that_draws_no_legend_renders_when_centering_is_authored(
    edge: str,
) -> None:
    board = """
title: t
queries:
  q:
    type: values
    rows:
      - {month: Jan, sessions: 10}
      - {month: Feb, sessions: 12}
charts:
  c:
    query: q
    type: bar
    x: month
    y: sessions
    style:
      legend:
        visible: true
        position:
          edge: EDGE
          align: center
rows:
  - c
""".replace("EDGE", edge)
    assert "ERR-" not in render_board_to_svg(board)


def test_small_multiples_reject_a_centered_legend(
    caplog: pytest.LogCaptureFixture,
) -> None:
    board = """
title: t
queries:
  q:
    type: values
    rows:
      - {month: Jan, segment: A, region: East, sessions: 10}
      - {month: Jan, segment: B, region: East, sessions: 12}
      - {month: Feb, segment: A, region: East, sessions: 11}
      - {month: Feb, segment: B, region: East, sessions: 13}
      - {month: Jan, segment: A, region: West, sessions: 10}
      - {month: Jan, segment: B, region: West, sessions: 12}
      - {month: Feb, segment: A, region: West, sessions: 11}
      - {month: Feb, segment: B, region: West, sessions: 13}
charts:
  c:
    query: q
    type: line
    x: month
    y: sessions
    color: segment
    multiples:
      columns: region
    style:
      legend:
        position:
          edge: top
          align: center
rows:
  - c
"""
    render_board_to_svg(board)
    assert "no single plot to align in" in caplog.text


def test_a_centered_legend_beside_an_endpoint_label_rail_is_rejected(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Endpoint labels (the default on a multi-series line) put the chart in a
    concat spec, which has no single plot to center the legend in."""
    board = """
title: t
queries:
  q:
    type: values
    rows:
      - {month: Jan, segment: A, sessions: 10}
      - {month: Jan, segment: B, sessions: 12}
      - {month: Feb, segment: A, sessions: 11}
      - {month: Feb, segment: B, sessions: 13}
charts:
  c:
    query: q
    type: line
    x: month
    y: sessions
    color: segment
    style:
      legend:
        position:
          edge: top
          align: center
rows:
  - c
"""
    render_board_to_svg(board)
    assert "no single plot to align in" in caplog.text
