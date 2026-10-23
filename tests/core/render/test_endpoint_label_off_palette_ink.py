from __future__ import annotations

import re

from dbt_charts.core.compile.resolve.style.palette import mark_ink

from .._svg_render import render_board_to_svg
from .test_category_color_scale_injection import _resolve_board

_BOARD = """
queries:
  q:
    type: values
    rows:
      - {year: 2020, source: Coal, twh: 30}
      - {year: 2020, source: Solar, twh: 10}
      - {year: 2021, source: Coal, twh: 25}
      - {year: 2021, source: Solar, twh: 15}
charts:
  stream:
    query: q
    type: area
    x: year
    y: twh
    color: source
    style:
      stack: center
      endpoint_labels:
        visible: true
      color:
        categorical:
          palette: ["#2a4d8f", "#3d8f5a"]
rows:
  - stream
style:
  charts:
    category_colors:
      source:
        values:
          Coal: "#4a4a4a"
"""


def _label_ink(svg: str, series: str) -> str:
    match = re.search(
        rf'<text[^>]*\sfill="(#[0-9a-f]{{6}})"[^>]*aria-label="source: {series}"', svg
    )
    assert match, f"no endpoint label for {series}"
    return match.group(1)


def test_pin_outside_the_palette_gets_the_companion_ink_of_the_pinned_color() -> None:
    resolved_board, _ = _resolve_board(_BOARD)
    canvas = resolved_board.charts["stream"].canvas
    ink = _label_ink(render_board_to_svg(_BOARD), "Coal")
    assert ink == mark_ink("#4a4a4a", canvas)
    assert ink != "#4a4a4a"
    assert ink != mark_ink("#2a4d8f", canvas)


_NESTED_BOARD = """
queries:
  q:
    type: values
    rows:
      - {year: 2020, source: Coal, twh: 30}
      - {year: 2020, source: Solar, twh: 10}
      - {year: 2021, source: Coal, twh: 25}
      - {year: 2021, source: Solar, twh: 15}
charts:
  stream:
    query: q
    type: area
    x: year
    y: twh
    color: source
    style:
      stack: center
      endpoint_labels:
        visible: true
      color:
        categorical:
          palette: ["#2a4d8f", "#3d8f5a"]
  other:
    query: q
    type: area
    x: year
    y: twh
    color: source
{root_style}
rows:
  - stream
  - style:
      charts:
        category_colors:
          source:
            values:
              Coal: "#aa0000"
    rows:
      - other
"""


def test_pin_authored_only_on_a_nested_board_still_gets_a_companion() -> None:
    svg = render_board_to_svg(_NESTED_BOARD.replace("{root_style}", ""))
    assert _label_ink(svg, "Coal")


def test_root_and_nested_pins_for_one_field_both_get_companions() -> None:
    root_style = """style:
  charts:
    category_colors:
      source:
        values:
          Coal: "#4a4a4a"
"""
    svg = render_board_to_svg(_NESTED_BOARD.replace("{root_style}", root_style))
    assert _label_ink(svg, "Coal")


_PIE_BOARD = """
queries:
  q:
    type: values
    rows:
      - {source: Coal, twh: 30}
      - {source: Solar, twh: 10}
charts:
  wheel:
    query: q
    type: pie
    theta: twh
    color: source
rows:
  - wheel
style:
  charts:
    category_colors:
      source:
        values:
          Coal: "#4a4a4a"
"""


def test_pie_bakes_the_companion_ink_of_a_pinned_color() -> None:
    resolved_board, _ = _resolve_board(_PIE_BOARD)
    wheel = resolved_board.charts["wheel"]
    assert wheel.pin_companions == (("#4a4a4a", mark_ink("#4a4a4a", wheel.canvas)),)
