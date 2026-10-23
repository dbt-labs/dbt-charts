"""The support-table strip thins at the cadence the axis paints.

The strip's cells and the axis labels share one rhythm, and the authored time
format decides it: a format too wide for every quarter coarsens the axis, and
the strip follows.
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


def test_strip_thins_to_quarterly_on_the_bare_month_vocabulary() -> None:
    """24 monthly points at the default width: "Jan" fits every quarter."""
    assert _strip_cells() == ["1", "4", "7", "10", "13", "16", "19", "22"]


def test_an_authored_format_coarsens_the_strip_with_the_axis() -> None:
    """ "January 2022" does not fit every quarter, so the axis coarsens to
    yearly and the strip follows it there."""
    chart_style = (
        "    style:\n      axis_x:\n        labels:\n          format: '%B %Y'\n"
    )
    assert _strip_cells(chart_style) == ["1", "13"]


def test_ordinal_strip_honors_the_chart_own_time_unit_over_the_family_default() -> None:
    """A chart's own `axis_x.time_unit` wins over the family-wide default on the
    ordinal bucketed-time path; the strip keeps the quarter-opening months."""
    chart_style = "    style:\n      axis_x:\n        type: ordinal\n        time_unit: yearmonth\n"
    board_style = (
        "style:\n  charts:\n    line:\n      axis_x:\n        time_unit: yearquarter"
    )
    assert _strip_cells(chart_style, board_style) == [
        "1",
        "4",
        "7",
        "10",
        "13",
        "16",
        "19",
        "22",
    ]
