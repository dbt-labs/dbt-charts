"""A ``y_start`` bar's zero baseline rule is hidden from screen readers."""

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
def test_zero_rule_on_y_start_bar_is_hidden_not_announced_as_nan(
    orientation: str,
) -> None:
    svg = render_board_to_svg(_BOARD.format(orientation=orientation))
    rule_groups = re.findall(r'<g [^>]*class="mark-rule role-mark[^>]*>', svg)
    assert rule_groups
    assert all('aria-hidden="true"' in group for group in rule_groups), rule_groups
    assert "NaN" not in svg
