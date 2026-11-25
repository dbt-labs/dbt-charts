"""`legend.symbol`, the background behind it and `label.halo` reach the cartesian legend."""

from __future__ import annotations

import re

from .._svg_render import render_board_to_svg


def _legend_svg(legend_style: str, chart_type: str = "bar") -> str:
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
      endpoint_labels:
        visible: false
      legend:
{legend_style}
rows:
  - c
""")
    assert "ERR-" not in svg
    return svg


def _legend_symbols(legend_style: str, chart_type: str = "bar") -> list[str]:
    return re.findall(
        r"role-legend-symbol[^>]*>(<[^>]*>)", _legend_svg(legend_style, chart_type)
    )


def test_a_named_shape_replaces_the_marks_legend_glyph() -> None:
    plain = _legend_symbols("        visible: true")
    diamond = _legend_symbols("        symbol:\n          shape: diamond")
    assert all(symbol.startswith("<rect") for symbol in plain)
    assert len(diamond) == 2
    assert all('d="M-5,0L0,-5L5,0L0,5Z"' in symbol for symbol in diamond)


def test_fill_false_hollows_the_legend_glyph_on_every_family() -> None:
    for family in ("bar", "line", "area"):
        hollow = _legend_symbols(
            "        visible: true\n        symbol:\n          fill: false", family
        )
        assert len(hollow) == 2, family
        assert all('fill="transparent"' in symbol for symbol in hollow), family


def test_symbol_size_sizes_the_legend_glyph() -> None:
    default = _legend_symbols("        visible: true")
    big = _legend_symbols("        symbol:\n          size: 16")
    assert all('width="10"' in symbol for symbol in default)
    assert len(big) == 2
    assert all('width="16"' in symbol for symbol in big)


def test_a_background_is_drawn_for_a_floating_legend_or_a_fill_that_is_not_the_canvas() -> (
    None
):
    floating = "        position:\n          edge: top\n          overlay: true"
    assert "role-legend-box" not in _legend_svg("        visible: true", "line")
    assert "role-legend-box" in _legend_svg(floating, "line")
    assert "role-legend-box" in _legend_svg("        background: '#ffd966'", "line")


def test_a_label_halo_outlines_each_label_in_the_legend_background() -> None:
    halo = "        background: '#ffd966'\n        label:\n          halo: 3"
    outlined = re.findall(r"<text[^>]*stroke-width=\"3\"[^>]*>", _legend_svg(halo))
    assert len(outlined) == 2
    assert all(
        'stroke="#ffd966"' in tag and 'fill="#ffd966"' in tag for tag in outlined
    )
    assert 'stroke-width="3"' not in _legend_svg("        visible: true")
