"""The support-table strip draws a number under every bucket the axis labels.

A table is a reference like a legend or scale: when the axis thins its labels the
strip keeps the first bucket and the labeled ones, whatever time format or unit
the axis authors, and never a column the axis does not label.
"""

from __future__ import annotations

import re

from ._svg_render import render_board_to_svg

_MONTHS = [f"{year}-{month:02d}-01" for year in (2022, 2023) for month in range(1, 13)]
_ROWS = "\n".join(
    f"      - {{month: '{month}', value: {i + 1}}}" for i, month in enumerate(_MONTHS)
)


def _strip_cells(chart_style: str = "", board_style: str = "") -> list[str]:
    """The strip's sums, in order: the short numerals after the y axis ticks."""
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
    cells = texts[texts.index("20") + 1 : texts.index("Value")]
    return [text for text in cells if text.isdigit() and len(text) <= 2]


_EVERY_QUARTER = [str(i) for i in range(1, 25, 3)]


def test_strip_follows_the_axis_labels_on_the_bare_month_vocabulary() -> None:
    """24 monthly points at the default width: the axis labels every quarter, the
    strip a column under each."""
    assert _strip_cells() == _EVERY_QUARTER


def test_an_authored_format_that_coarsens_the_axis_coarsens_the_strip_with_it() -> None:
    """ "January 2022" does not fit every quarter, so the axis coarsens to
    yearly; the strip keeps the first month of each year."""
    chart_style = (
        "    style:\n      axis_x:\n        labels:\n          format: '%B %Y'\n"
    )
    assert _strip_cells(chart_style) == ["1", "13"]


def test_ordinal_strip_follows_the_labels_of_the_chart_own_time_unit() -> None:
    """A chart's own `axis_x.time_unit` wins over the family-wide default on the
    ordinal bucketed-time path: the strip keeps a column under each label that axis
    draws (Jan, Apr, Jul, Oct), not the family's `yearquarter` cadence by itself."""
    chart_style = "    style:\n      axis_x:\n        type: ordinal\n        time_unit: yearmonth\n"
    board_style = (
        "style:\n  charts:\n    line:\n      axis_x:\n        time_unit: yearquarter"
    )
    assert _strip_cells(chart_style, board_style) == _EVERY_QUARTER
