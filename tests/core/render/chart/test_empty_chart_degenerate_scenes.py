"""A chart over no rows paints no scene Vega computed from nothing."""

from __future__ import annotations

import re

from ..._svg_render import render_board_to_svg

_BOARD = """
title: t
queries:
  q:
    type: values
    columns: [category, amount, grp, cost]
    values: []
charts:
  c:
    query: q
    type: {chart_type}
    x: category
    y: {y}
{extra}rows: [c]
"""


def _render(chart_type: str, y: str, extra: str) -> str:
    return render_board_to_svg(_BOARD.format(chart_type=chart_type, y=y, extra=extra))


def _chart_height(svg: str) -> float:
    match = re.search(r'data-chart-height="([\d.]+)"', svg)
    assert match is not None, svg[:400]
    return float(match.group(1))


def test_a_support_table_over_no_rows_paints_no_phantom_header() -> None:
    plain = _render("bar", "amount", "")
    with_table = _render(
        "bar",
        "amount",
        "    support_table:\n    - source: amount\n      aggregate: sum\n",
    )
    assert "NaN" not in with_table
    assert _chart_height(with_table) == _chart_height(plain)


def test_a_wide_measure_split_by_color_over_no_rows_paints_no_infinite_offset() -> None:
    extra = (
        "    color: grp\n"
        "    style:\n"
        "      legend:\n"
        "        position:\n"
        "          edge: right\n"
        "          align: start\n"
        "          overlay: false\n"
    )
    for chart_type in ("bar", "line"):
        svg = _render(chart_type, "[amount, cost]", extra)
        assert "e+308" not in svg and "ERR-CONCAT-OVERSHOOT" not in svg, chart_type


def test_an_empty_base_with_a_layer_that_has_rows_keeps_the_layer_legend() -> None:
    board = """
title: t
queries:
  q:
    type: values
    columns: [month, revenue]
    values: []
  segs:
    type: values
    columns: [month, segment, cost]
    values:
    - ['2024-01-01', a, 40]
    - ['2024-01-01', b, 20]
    - ['2024-02-01', a, 50]
    - ['2024-02-01', b, 25]
charts:
  c:
    query: q
    type: line
    x: month
    y: revenue
    layers:
    - type: line
      query: segs
      y: cost
      color: segment
    style:
      legend:
        position:
          edge: top
          align: start
          overlay: false
rows: [c]
"""
    svg = render_board_to_svg(board)
    assert "role-legend" in svg
    assert "Infinity" not in svg and "e+308" not in svg
