"""A ``y_start`` bar's zero baseline rule never announces ``NaN``."""

from __future__ import annotations

import re

import pytest

from ._svg_render import render_board_to_svg

_BOARD = """
queries:
  bridge:
    type: values
    rows:
      - {{step: A, open_before: 0, open_after: 31}}
      - {{step: B, open_before: 31, open_after: 52}}
      - {{step: C, open_before: 52, open_after: 23}}
      - {{step: Total, open_before: 0, open_after: 23}}
charts:
  bridge:
    query: bridge
    type: bar
    x: step
    y: open_after
    y_start: open_before
    style:
      orientation: {orientation}
rows:
  - bridge
"""


@pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
def test_zero_rule_on_y_start_bar_has_no_nan_aria_label(orientation: str) -> None:
    svg = render_board_to_svg(_BOARD.format(orientation=orientation))
    rule_marks = re.findall(r"<line [^>]*rule mark[^>]*>", svg)
    assert rule_marks
    assert not [mark for mark in rule_marks if "NaN" in mark]
