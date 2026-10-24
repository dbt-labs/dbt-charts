"""A bar whose color authors ``style.color.gradient`` paints that ramp and
names it with one gradient legend, faceted or not."""

import pytest

from ._svg_render import render_board_to_svg

_BOARD = """
title: T
queries:
  q:
    type: values
    rows:
      - {{m: A, g: a, v: 10, w: 1.5}}
      - {{m: B, g: a, v: 4, w: 9.5}}
      - {{m: A, g: b, v: 7, w: 3.2}}
      - {{m: B, g: b, v: 30, w: 7.7}}
charts:
  c:
    query: q
    type: bar
    x: m
    y: v
    color: w
    style:
      color:
        gradient:
          palette: ["#ffffff", "#0000ff"]
{multiples}
rows:
  - c
"""


@pytest.mark.parametrize(
    "multiples",
    ["", "    multiples:\n      columns: g", "    multiples:\n      rows: g"],
    ids=["plain", "columns-facet", "rows-facet"],
)
def test_gradient_bar_draws_one_gradient_legend_and_the_authored_ramp(
    multiples: str,
) -> None:
    svg = render_board_to_svg(_BOARD.format(multiples=multiples))

    assert svg.count("<linearGradient") == 1
    assert "role-legend-symbol" not in svg
    # The ramp's own end: the highest w paints the palette's blue.
    assert 'fill="rgb(0, 0, 255)"' in svg
