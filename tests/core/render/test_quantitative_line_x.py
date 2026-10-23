"""A numeric x on a line is a linear scale: d3's ticks over the nice domain, not one tick per value."""

from __future__ import annotations

import re

from .._svg_render import render_board_to_svg


def test_a_numeric_line_x_ticks_by_d3_over_its_domain() -> None:
    svg = render_board_to_svg("""
title: T
queries:
  q:
    type: values
    rows:
      - {step: 1, value: 12}
      - {step: 2, value: 48}
      - {step: 3, value: 90}
      - {step: 4, value: 150}
charts:
  c:
    query: q
    type: line
    x: step
    y: value
rows:
  - c
""")
    texts = re.findall(r">([^<>]+)</text>", svg)
    assert texts[:7] == ["1", "1.5", "2", "2.5", "3", "3.5", "4"]
