"""A funnel's value axis follows the funnel recipe, which hides it."""

from __future__ import annotations

import re

from ._svg_render import render_board_to_svg

_BOARD = """
title: T
queries:
  q:
    type: values
    rows:
      - {{stage: Visit, n: 30255}}
      - {{stage: Signup, n: 21500}}
      - {{stage: Buy, n: 9000}}
charts:
  c:
    query: q
    type: funnel
    x: stage
    y: n
{extra}
rows:
  - c
"""

_TICKS = {"10,000", "20,000"}


def _texts(extra: str = "") -> set[str]:
    svg = render_board_to_svg(_BOARD.format(extra=extra))
    return set(re.findall(r">([^<>]+)</text>", svg))


def test_a_funnel_draws_no_value_axis_labels() -> None:
    assert not _TICKS & _texts()


def test_the_charts_own_axis_style_brings_them_back() -> None:
    extra = (
        "    style:\n      axis_quantitative:\n        labels:\n          visible: true"
    )
    assert _texts(extra) >= _TICKS
