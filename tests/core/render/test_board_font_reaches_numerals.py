"""A board's ``style.font.family`` paints its digits too: KPI values, axis ticks,
table cells."""

from __future__ import annotations

import re

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_AXIS_COLUMN_REQUIRES_TABULAR_FONT,
)
from dbt_charts.core.fonts import registry_family

from .._svg_render import render_board_svg_result, render_board_to_svg

_THEMES = ["clarity", "paper", "stark", "vivid", "neon"]
_TABULAR = registry_family(get_theme_style().numeral_family())

_BOARD = """
theme: {theme}
{style}
queries:
  total:
    type: values
    rows:
      - {{revenue: 1234567}}
  monthly:
    type: values
    rows:
      - {{month: Jan, revenue: 1234567}}
      - {{month: Feb, revenue: 2345678}}
charts:
  kpi:
    query: total
    type: kpi
    value: revenue
    label: Revenue
    good: up
  bars:
    query: monthly
    type: bar
    x: month
    y: revenue
  grid:
    query: monthly
    type: table
rows:
  - kpi
  - bars
  - grid
"""

_ROBOTO = "style:\n  font:\n    family: Roboto"


def _family(attrs: str) -> str | None:
    match = re.search(r'font-family="([^"]*)"', attrs)
    return match.group(1) if match else None


def _runs(svg: str) -> list[tuple[str, str]]:
    """The (text, font-family) of every painted run; a ``<tspan>`` paints in its own
    family, else in its ``<text>``'s."""
    painted = []
    for attrs, body in re.findall(r"<text\b([^>]*)>(.*?)</text>", svg, re.S):
        spans = re.findall(r"<tspan\b([^>]*)>([^<]*)<", body)
        runs = (
            [(text, _family(span_attrs)) for span_attrs, text in spans]
            if spans
            else [(re.sub(r"<[^>]+>", "", body), None)]
        )
        for text, family in runs:
            family = family or _family(attrs)
            if family and text.strip():
                painted.append((text, family))
    return painted


def _render(style: str = "", theme: str = "clarity") -> list[tuple[str, str]]:
    return _runs(render_board_to_svg(_BOARD.format(style=style, theme=theme)))


def _digit_runs(runs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(text, family) for text, family in runs if re.search(r"\d", text)]


@pytest.mark.parametrize("theme", _THEMES)
def test_a_board_family_paints_every_digit_in_that_family(theme: str) -> None:
    digits = _digit_runs(_render(_ROBOTO, theme))
    assert {"1.2", "1,234,567", "2,345,678", "1m", "2m"} <= {t for t, _ in digits}
    assert [(t, f) for t, f in digits if not f.startswith("Roboto")] == []


@pytest.mark.parametrize("theme", _THEMES)
def test_a_board_family_paints_every_kpi_run_in_that_family(theme: str) -> None:
    """Value, affix and glyph: a slot that pins its own family would leave one
    headline in two typefaces."""
    runs = _render(_ROBOTO, theme)
    texts = {text.strip() for text, _ in runs}
    assert {"1.2", "m", "▲"} <= texts
    assert [(t, f) for t, f in runs if not f.startswith("Roboto")] == []


def test_a_board_that_sets_no_family_keeps_the_tabular_digits() -> None:
    tabular = {t for t, f in _digit_runs(_render()) if _TABULAR in f}
    assert {"1,234,567", "2,345,678"} <= tabular


def test_an_authored_numeral_family_beats_the_board_family() -> None:
    style = _ROBOTO + "\n  fonts:\n    numeral:\n      family: Georgia"
    cells = {f for t, f in _render(style) if t == "1,234,567"}
    assert cells
    assert all(family.startswith("Georgia") for family in cells)


_COLUMN_AXIS = """
{style}
queries:
  q:
    type: values
    rows:
      - {{month: Jan, revenue: 0}}
      - {{month: Feb, revenue: 120000}}
      - {{month: Mar, revenue: 500000}}
charts:
  c:
    query: q
    type: line
    x: month
    y: revenue
    style:
      axis_y:
        labels:
          align: {align}
rows:
  - c
"""


def _column_axis_errors(style: str, align: str) -> list[str]:
    result = render_board_svg_result(_COLUMN_AXIS.format(style=style, align=align))
    return [error.code for error in result.chart_errors]


def test_a_proportional_family_refuses_an_authored_right_aligned_compacting_axis() -> (
    None
):
    """A column of compacted ticks only lines up in a tabular face; the board family
    reaches the axis numerals, so the author must pick a tabular one."""
    assert _column_axis_errors(_ROBOTO, "right") == [
        ERR_AXIS_COLUMN_REQUIRES_TABULAR_FONT.code
    ]


def test_the_same_axis_renders_with_no_board_family_or_with_a_tabular_numeral() -> None:
    assert _column_axis_errors("", "right") == []
    tabular = _ROBOTO + "\n  fonts:\n    numeral:\n      family: " + _TABULAR
    assert _column_axis_errors(tabular, "right") == []


def test_a_proportional_family_renders_an_unaligned_compacting_axis() -> None:
    assert _column_axis_errors(_ROBOTO, "left") == []


_NESTED = """
theme: vivid
{parent}
queries:
  total:
    type: values
    rows:
      - {{revenue: 1234567}}
charts:
  kpi:
    query: total
    type: kpi
    value: revenue
    good: up
rows:
  - style:
      font:
        family: Roboto
    rows:
      - kpi
"""


@pytest.mark.parametrize(
    "parent", ["", "style:\n  font:\n    family: Georgia"], ids=["alone", "over_parent"]
)
def test_a_nested_board_family_paints_its_kpi_runs_in_that_family(parent: str) -> None:
    runs = _runs(render_board_to_svg(_NESTED.format(parent=parent)))
    kpi = [(t, f) for t, f in runs if t.strip() in {"1.2", "m", "▲"}]
    assert {t.strip() for t, _ in kpi} == {"1.2", "m", "▲"}
    assert [(t, f) for t, f in kpi if not f.startswith("Roboto")] == []
