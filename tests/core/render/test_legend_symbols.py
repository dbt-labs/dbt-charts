"""`legend.symbol_shape` and `legend.symbol_fill` reach the cartesian legend."""

from __future__ import annotations

import re

from .._svg_render import render_board_to_svg


def _legend_symbols(legend_style: str, chart_type: str = "bar") -> list[str]:
    svg = render_board_to_svg(f"""
title: T
queries:
  q:
    type: values
    rows:
      - {{m: Jan, s: a, v: 1}}
      - {{m: Jan, s: b, v: 2}}
      - {{m: Feb, s: a, v: 3}}
      - {{m: Feb, s: b, v: 4}}
charts:
  c:
    query: q
    type: {chart_type}
    x: m
    y: v
    color: s
    style:
      legend:
{legend_style}
rows:
  - c
""")
    assert "ERR-" not in svg
    return re.findall(r"role-legend-symbol[^>]*>(<[^>]*>)", svg)


def test_a_named_symbol_shape_replaces_the_marks_legend_glyph() -> None:
    plain = _legend_symbols("        visible: true")
    diamond = _legend_symbols("        symbol_shape: diamond")
    assert all(symbol.startswith("<rect") for symbol in plain)
    assert len(diamond) == 2
    assert all('d="M-5,0L0,-5L5,0L0,5Z"' in symbol for symbol in diamond)


def test_symbol_fill_false_hollows_the_legend_glyph_on_every_family() -> None:
    for family in ("bar", "line", "area"):
        hollow = _legend_symbols(
            "        visible: true\n        symbol_fill: false", family
        )
        assert len(hollow) == 2, family
        assert all('fill="transparent"' in symbol for symbol in hollow), family
