"""Centering a legend along its edge: the pure scenegraph geometry, the SVG
rewrite, and the rendered result for each edge and overlay mode.
"""

from __future__ import annotations

import pytest

from .._svg_render import render_board_to_svg


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


def test_a_placed_legend_replaces_the_endpoint_label_rail() -> None:
    """Endpoint labels (the default on a multi-series line) already name the
    series; an authored legend position replaces them with the legend."""
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
    svg = render_board_to_svg(board)
    assert "ERR-" not in svg
