"""A pivot's measure cells print as the same measure prints in a flat table."""

from __future__ import annotations

from .._svg_render import render_board_to_svg


def _board(
    *,
    formats: str = "",
    style: str = "",
    months: tuple[str, str] = ("2026-06", "2026-07"),
) -> str:
    return f"""
title: T
queries:
  q:
    type: values
    rows:
      - {{team: A, month: {months[0]}, minutes: 3140, share: 0.55}}
      - {{team: A, month: {months[1]}, minutes: 388400, share: 1.04}}
      - {{team: B, month: {months[0]}, minutes: 18250, share: 0.62}}
      - {{team: B, month: {months[1]}, minutes: 120300, share: 0.93}}
{formats}
charts:
  c:
    query: q
    type: table
    rows: [team]
    columns: [month]
    values: [{{value}}]
{style}
rows:
  - c
"""


def _pivot(value: str, **kwargs: str) -> str:
    return render_board_to_svg(_board(**kwargs).replace("{value}", value))


def test_an_all_integer_pivot_measure_prints_exact_digits_not_compact_si() -> None:
    svg = _pivot("minutes")
    assert ">3,140<" in svg and ">388,400<" in svg
    assert "3.14" not in svg


def test_a_query_format_reaches_pivot_cells() -> None:
    svg = _pivot("share", formats="    formats:\n      share: percent\n")
    assert ">55.0<" in svg and ">104.0<" in svg
    assert svg.count(">%</tspan>") == 4


def test_an_authored_measure_format_still_wins_over_the_query_format() -> None:
    style = "    style:\n      columns:\n        share:\n          format: integer\n"
    svg = _pivot("share", formats="    formats:\n      share: percent\n", style=style)
    assert ">%</tspan>" not in svg


def test_a_date_dimension_header_prints_its_format_else_the_flat_default() -> None:
    dates = ("2026-06-01", "2026-07-01")
    assert ">1 Jun 2026<" in _pivot("minutes", months=dates)
    style = "    style:\n      columns:\n        month:\n          format: '%b %Y'\n"
    styled = _pivot("minutes", months=dates, style=style)
    assert ">Jun 2026<" in styled
