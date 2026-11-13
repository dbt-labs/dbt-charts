"""Bar ribbons: `style.marks.bar.ribbons.visible` reaches the engine and draws."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.chart.resolved import ResolvedBarChart

from ._board_utils import make_test_resolved_chart
from ._svg_render import render_board_to_svg

_BOARD = """
title: Ribbons
queries:
  q1:
    type: values
    rows:
      - {{stage: Visit, kind: web, n: 100}}
      - {{stage: Visit, kind: app, n: 60}}
      - {{stage: Signup, kind: web, n: 70}}
      - {{stage: Signup, kind: app, n: 30}}
      - {{stage: Buy, kind: web, n: 20}}
      - {{stage: Buy, kind: app, n: 10}}
charts:
  c1:
    query: q1
    type: bar
    x: stage
    y: n
    color: kind
    style:
      stack: zero
{marks}
rows:
  - c1
"""

_ROWS = [
    {"stage": "Visit", "kind": "web", "n": 100},
    {"stage": "Visit", "kind": "app", "n": 60},
    {"stage": "Signup", "kind": "web", "n": 70},
    {"stage": "Signup", "kind": "app", "n": 30},
]

_RIBBONS_ON = """      marks:
        bar:
          ribbons:
            visible: true"""


def test_ribbons_visible_reaches_the_resolved_bar_style() -> None:
    result = compile(_BOARD.format(marks=_RIBBONS_ON))
    assert result.success
    chart = make_test_resolved_chart(result.board.charts["c1"], _ROWS)
    assert isinstance(chart, ResolvedBarChart)
    assert chart.style.mark.ribbons.visible is True


def test_ribbons_draw_when_visible() -> None:
    svg = render_board_to_svg(_BOARD.format(marks=_RIBBONS_ON))
    assert 'data-dct-role="ribbon"' in svg


def test_no_ribbons_by_default() -> None:
    svg = render_board_to_svg(_BOARD.format(marks=""))
    assert 'data-dct-role="ribbon"' not in svg


def test_ribbons_visible_must_be_boolean() -> None:
    result = compile(_BOARD.format(marks=_RIBBONS_ON.replace("true", "soft")))
    assert not result.success


@pytest.mark.parametrize("bad", ["strength: 0.5", "opacity: 0.5"])
def test_ribbons_have_no_other_authored_keys(bad: str) -> None:
    result = compile(_BOARD.format(marks=_RIBBONS_ON + f"\n            {bad}"))
    assert not result.success
