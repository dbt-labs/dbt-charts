"""A chart-local ``style.background`` paints the whole card on every family.

A Vega chart bakes ``frame.card_padding`` into its spec and renders at the
outer size, so its background fills the card edge to edge. The SVG families
(KPI, table) render at the padding-shrunk inner size and are translated back in
by the wrapper — their card rect must reach back out over that padding, or the
same authored color reads as a smaller, inset card beside a chart.
"""

from __future__ import annotations

import re

from ..._svg_render import _translate_walk, authored_boxes, render_board_to_svg

_FILL = "#e0c080"

_BOARD = f"""
title: Card Fill Probe
style:
  frame:
    card_padding: 16
queries:
  monthly:
    type: values
    rows:
      - {{month: Jan, revenue: 100}}
      - {{month: Feb, revenue: 150}}
  total:
    type: values
    rows:
      - {{revenue: 250}}
charts:
  trend:
    query: monthly
    type: area
    x: month
    y: revenue
    title: Trend
    style:
      background: "{_FILL}"
  total:
    query: total
    type: kpi
    value: revenue
    label: Revenue
    style:
      background: "{_FILL}"
  detail:
    query: monthly
    type: table
    title: Detail
    style:
      preferred_width: 800
      background: "{_FILL}"
rows:
  - height: 240
    cols: [trend, total, detail]
"""

_CARD_RECT = re.compile(
    rf'<rect x="(-?[\d.]+)" y="(-?[\d.]+)" width="([\d.]+)" height="([\d.]+)"'
    rf'[^>]*fill="{_FILL}"'
)


def _card_rects(svg: str) -> dict[str, tuple[float, float, float, float]]:
    rects: dict[str, tuple[float, float, float, float]] = {}
    for x, y, found, path in _translate_walk(svg, _CARD_RECT):
        if path is not None and path not in rects:
            rects[path] = (
                x + float(found.group(1)),
                y + float(found.group(2)),
                float(found.group(3)),
                float(found.group(4)),
            )
    return rects


def test_kpi_and_table_card_fill_covers_the_outer_box() -> None:
    svg = render_board_to_svg(_BOARD)
    outer = authored_boxes(svg, "dbt-box-outer")
    inner = authored_boxes(svg, "dbt-box-inner")
    rects = _card_rects(svg)
    for path in ("charts.total", "charts.detail"):
        assert outer[path] != inner[path], f"{path}: no padding to cover"
        assert rects[path] == outer[path], (
            f"{path}: card fill {rects[path]} should cover the outer box "
            f"{outer[path]}, not the inner {inner[path]}"
        )
