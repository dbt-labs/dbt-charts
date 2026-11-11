"""A support table under a year-step bar chart shows the real years only."""

from __future__ import annotations

import re

from .._svg_render import render_board_to_svg

_BOARD = """
title: Year-step bars
width: 480
queries:
  q:
    columns: [yr, amount]
    values:
      - [1899, 19]
      - [1904, 34]
      - [1909, 52]
      - [1914, 71]
      - [1919, 25]
      - [1924, 40]
charts:
  q:
    query: q
    type: bar
    x: yr
    y: amount
    height: 260
    support_table:
      - source: amount
        aggregate: sum
        label: Total
rows:
  - q
"""


def _strip_texts(board: str) -> list[str]:
    svg = render_board_to_svg(board)
    start = svg.index('class="mark-text role-mark strip"')
    group = svg[start : svg.index("</g>", start)]
    return re.findall(r"<text[^>]*>(?:<tspan[^>]*>)?([^<]*)", group)


def test_year_step_strip_prints_every_real_year_and_no_filler() -> None:
    strip = _strip_texts(_BOARD)
    assert strip == ["19", "34", "52", "71", "25", "40"]


def test_gap_filled_monthly_strip_opens_each_label_period_on_a_bucket_with_data() -> (
    None
):
    months = [
        (2023 + i // 12, i % 12 + 1, 10 + i)
        for i in range(36)
        if i not in (0, 12)  # Jan 2023 and Jan 2024 are missing
    ]
    values = "\n".join(f'      - ["{y}-{m:02d}-01", {v}]' for y, m, v in months)
    board = f"""
title: Monthly bars
width: 560
queries:
  q:
    columns: [month, amount]
    values:
{values}
charts:
  q:
    query: q
    type: bar
    x: month
    y: amount
    height: 260
    support_table:
      - source: amount
        aggregate: sum
        label: Total
rows:
  - q
"""
    # Quarter labels: each period opens on its first month with data, so a
    # missing January shows February under the same label.
    assert _strip_texts(board) == [
        "11",
        "13",
        "16",
        "19",
        "23",
        "25",
        "28",
        "31",
        "34",
        "37",
        "40",
        "43",
    ]
