"""A funnel's value labels follow the callout format cascade, never the axis format."""

from __future__ import annotations

import re

from ._svg_render import render_board_to_svg

_BOARD = """
title: T
queries:
  q:
    type: values
    rows:
      - {{stage: Visit, crates: 30255}}
      - {{stage: Signup, crates: 21500}}
      - {{stage: Buy, crates: 9000}}
{formats}charts:
  c:
    query: q
    type: {chart_type}
    x: stage
    y: crates
{extra}
rows:
  - c
"""


def _texts(chart_type: str = "funnel", formats: str = "", extra: str = "") -> list[str]:
    svg = render_board_to_svg(
        _BOARD.format(chart_type=chart_type, formats=formats, extra=extra)
    )
    return re.findall(r">([^<>]+)</text>", svg)


def test_an_integer_column_prints_whole_on_a_funnel() -> None:
    assert {"30,255", "21,500", "9,000"} <= set(_texts())


def test_a_declared_column_format_wins_over_the_type_default() -> None:
    declared = "    formats:\n      crates: currency\n"
    texts = _texts(formats=declared)
    assert {"$30.3k", "$21.5k", "$9k"} <= set(texts)


def test_an_authored_label_format_wins() -> None:
    extra = "    style:\n      marks:\n        bar:\n          labels:\n            format: '.2s'"
    assert "30k" in _texts(extra=extra)


def test_a_plain_bar_still_takes_the_axis_format() -> None:
    extra = (
        "    style:\n      orientation: horizontal\n"
        "      marks:\n        bar:\n          labels:\n            visible: true"
    )
    assert {"30.3k", "21.5k", "9k"} <= set(_texts("bar", extra=extra))
