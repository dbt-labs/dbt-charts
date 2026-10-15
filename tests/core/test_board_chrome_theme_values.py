"""Board-authored chrome values reach the render: each is set off its default."""

import html
import json
import re
from xml.etree import ElementTree

from ._svg_render import render_board_to_svg

_BOARD = """
title: T
queries:
  q: {type: values, rows: [{cat: A, val: 10}, {cat: B, val: 4}]}
charts:
  c: {query: q, type: bar, x: cat, y: val}
rows: [c]
"""
_SVG = "{http://www.w3.org/2000/svg}"
_FOOTER_STAMP = "  timestamp:\n    position: footer\n    align: right\n"


def _render(style: str) -> str:
    return render_board_to_svg(_BOARD + "style:\n" + style)


def _view_box_height(svg: str) -> float:
    return float(ElementTree.fromstring(svg).attrib["viewBox"].split()[3])


def _stamp_x(svg: str) -> float:
    root = ElementTree.fromstring(svg)
    (stamp,) = (
        el
        for el in root.iter(f"{_SVG}text")
        if el.attrib.get("data-role") == "render-timestamp"
    )
    return float(stamp.attrib["x"])


def test_authored_timestamp_gap_is_the_painted_gap() -> None:
    default = _render(_FOOTER_STAMP)
    wide = _render(_FOOTER_STAMP + "    gap: 30\n")

    assert _stamp_x(default) - _stamp_x(wide) == 30 - 12


def test_authored_footer_divider_gap_is_reserved_and_painted() -> None:
    divider = "  footer:\n    divider:\n      color: '#123456'\n"
    default = _render(divider)
    wide = _render(divider + "      gap: 10\n")

    def gap_below_rule(svg: str) -> float:
        root = ElementTree.fromstring(svg)
        (rule,) = (
            el for el in root.iter(f"{_SVG}line") if el.attrib["stroke"] == "#123456"
        )
        runs = [el for el in root.iter(f"{_SVG}text") if el.text and "y" in el.attrib]
        footer_y = max(float(el.attrib["y"]) for el in runs)
        size = float(
            next(el for el in runs if float(el.attrib["y"]) == footer_y).attrib[
                "font-size"
            ]
        )
        return footer_y - size - float(rule.attrib["y1"])

    assert gap_below_rule(default) == 2
    assert gap_below_rule(wide) == 10
    assert _view_box_height(wide) - _view_box_height(default) == 10 - 2


def test_authored_footer_brand_weight_is_painted() -> None:
    assert 'font-weight="500"' in _render("  footer:\n    brand_weight: 500\n")
    assert 'font-weight="500"' not in _render("  footer:\n    brand_weight: 600\n")


def test_authored_dimmed_opacity_reaches_the_runtime() -> None:
    svg = _render("  charts:\n    hover_emphasis:\n      dimmed_opacity: 0.4\n")
    match = re.search(r'data-dbt-hover-emphasis="([^"]*)"', svg)
    assert match
    assert json.loads(html.unescape(match.group(1)))["opacity"] == 0.4
