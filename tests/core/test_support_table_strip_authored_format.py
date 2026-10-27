"""The support-table strip draws a number under every bucket.

The axis may thin its labels, but the strip never follows it: a table carries
every x, whatever time format or unit the axis authors.
"""

from __future__ import annotations

import re

from ._svg_render import render_board_to_svg

_MONTHS = [f"{year}-{month:02d}-01" for year in (2022, 2023) for month in range(1, 13)]
_ROWS = "\n".join(
    f"      - {{month: '{month}', value: {i + 1}}}" for i, month in enumerate(_MONTHS)
)


def _strip_cells(chart_style: str = "", board_style: str = "") -> list[str]:
    """The strip's sums, in order: the numerals after the y axis ticks."""
    svg = render_board_to_svg(f"""
title: T
{board_style}
queries:
  q:
    type: values
    rows:
{_ROWS}
charts:
  c:
    query: q
    type: line
    x: month
    y: value
{chart_style}
    support_table:
      entries:
        - aggregate: sum
          source: value
rows:
  - c
""")
    texts = re.findall(r">([^<>]+)</text>", svg)
    return texts[texts.index("20") + 1 : texts.index("Value")]


_EVERY_MONTH = [str(i) for i in range(1, 25)]


def test_strip_draws_every_month_on_the_bare_month_vocabulary() -> None:
    """24 monthly points at the default width: the axis labels every quarter, the
    strip every month."""
    assert _strip_cells() == _EVERY_MONTH


def test_an_authored_format_that_coarsens_the_axis_leaves_the_strip_whole() -> None:
    """ "January 2022" does not fit every quarter, so the axis coarsens to
    yearly; the strip still draws every month."""
    chart_style = (
        "    style:\n      axis_x:\n        labels:\n          format: '%B %Y'\n"
    )
    assert _strip_cells(chart_style) == _EVERY_MONTH


def test_ordinal_strip_draws_every_month_under_the_chart_own_time_unit() -> None:
    """A chart's own `axis_x.time_unit` wins over the family-wide default on the
    ordinal bucketed-time path: the family's `yearquarter` never reduces the
    strip to quarter-opening months."""
    chart_style = "    style:\n      axis_x:\n        type: ordinal\n        time_unit: yearmonth\n"
    board_style = (
        "style:\n  charts:\n    line:\n      axis_x:\n        time_unit: yearquarter"
    )
    assert _strip_cells(chart_style, board_style) == _EVERY_MONTH
