"""A nested section's translucent fill composites onto the page exactly once.

Every coat over the same pixels darkens a translucent fill, so the section's
color must come from one rect covering its full bounds, title included.
"""

from __future__ import annotations

import re

from .._svg_render import render_board_to_svg

_FILL = "rgba(0,0,0,0.5)"

_BOARD = """
title: Root
queries:
  q1:
    type: values
    rows:
      - {{month: Jan, revenue: 100}}
      - {{month: Feb, revenue: 150}}
charts:
  c1:
    query: q1
    type: bar
    x: month
    y: revenue
rows:
  - {section_title}
    style:
      background: "{fill}"
{section_style}
    rows:
      - c1
"""


def _board(
    *, fill: str = _FILL, section_title: str = "title: Section", section_style: str = ""
) -> str:
    return _BOARD.format(
        fill=fill, section_title=section_title, section_style=section_style
    )


def _fill_rects(svg: str, fill: str) -> list[str]:
    return re.findall(rf'<rect[^>]*fill="{re.escape(fill)}"[^>]*/?>', svg)


def _attr(tag: str, name: str) -> str:
    match = re.search(rf'\b{name}="([^"]+)"', tag)
    assert match
    return match.group(1)


def test_translucent_section_fill_is_painted_once_over_its_full_bounds() -> None:
    svg = render_board_to_svg(_board())
    (rect,) = _fill_rects(svg, _FILL)
    section_open = svg[: svg.index(rect)].rsplit("<svg", 1)[1].split(">", 1)[0]
    assert _attr(section_open, "width") == _attr(rect, "width")
    assert _attr(section_open, "height") == _attr(rect, "height")
    assert (_attr(rect, "x"), _attr(rect, "y")) == ("0", "0")


def test_untitled_translucent_section_fill_is_painted_once() -> None:
    svg = render_board_to_svg(_board(section_title="id: section"))
    assert len(_fill_rects(svg, _FILL)) == 1


def test_authored_chart_card_fill_still_paints_over_a_translucent_section() -> None:
    card = "rgba(255,255,255,0.25)"
    svg = render_board_to_svg(
        _board(section_style=f'      charts:\n        background: "{card}"')
    )
    assert len(_fill_rects(svg, _FILL)) == 1
    assert len(_fill_rects(svg, card)) == 1


def test_translucent_root_fill_is_not_repainted_by_its_chart_card() -> None:
    svg = render_board_to_svg(
        _board(section_title="id: section")
        .replace("queries:", f'style:\n  background: "{_FILL}"\nqueries:', 1)
        .replace(f'      background: "{_FILL}"\n', "")
    )
    assert len(_fill_rects(svg, _FILL)) == 2
