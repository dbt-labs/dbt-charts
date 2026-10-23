"""Table columns are sized to their content, as CSS auto table layout does."""

from __future__ import annotations

import re

from mdsvg import parse, render
from mdsvg.renderer import SVGRenderer
from mdsvg.types import Table

LONG = "This cell holds a great deal of explanatory text that must wrap " * 2
TABLE = f"| Region | Units | Notes |\n|---|---|---|\n| West | 812 | {LONG} |\n"
SHORT = "| Region | Units |\n|---|---|\n| West | 812 |\n"


def _column_edges(svg: str) -> list[float]:
    """x of the table's left edge, every column rule, and its right edge."""
    outer = re.search(
        r'<rect x="([\d.]+)" y="[\d.]+" width="([\d.]+)"[^>]*fill="none"', svg
    )
    assert outer
    rules = [
        float(m.group(1))
        for m in re.finditer(r'<line x1="([\d.]+)" y1="[\d.]+" x2="\1"', svg)
    ]
    left, width = float(outer.group(1)), float(outer.group(2))
    return [left, *rules, left + width]


def _widths(svg: str) -> list[float]:
    edges = _column_edges(svg)
    return [b - a for a, b in zip(edges, edges[1:])]


def test_long_cell_column_absorbs_the_wrapping() -> None:
    widths = _widths(render(TABLE, width=400, padding=0))
    assert widths[2] > widths[0] and widths[2] > widths[1]
    assert abs(sum(widths) - 400) < 0.5


def test_short_columns_do_not_wrap() -> None:
    svg = render(TABLE, width=400, padding=0)
    assert svg.count(">West</text>") == 1 and svg.count(">812</text>") == 1
    assert svg.count(">Region</tspan>") == 1


def test_table_is_only_as_wide_as_its_content() -> None:
    svg = render(SHORT, width=600, padding=0)
    assert sum(_widths(svg)) < 200
    expected = SVGRenderer().max_content_width(list(parse(SHORT)))
    assert abs(sum(_widths(svg)) - expected) < 0.1


def test_grid_rules_are_all_drawn() -> None:
    svg = render(TABLE, width=400, padding=0)
    assert len(_column_edges(svg)) == 4
    assert (
        len(re.findall(r'<line x1="[\d.]+" y1="([\d.]+)" x2="[\d.]+" y2="\1"', svg))
        == 1
    )


def test_alignment_markers_position_text() -> None:
    md = "| L | C | R |\n|:--|:-:|--:|\n| a | b | c |\n"
    svg = render(md, width=400, padding=0)
    anchors = re.findall(r'text-anchor="(\w+)"[^>]*>([abc])<', svg)
    assert {t: a for a, t in anchors} == {"a": "start", "b": "middle", "c": "end"}


def test_unbreakable_overflow_keeps_equal_columns() -> None:
    md = "| a | b |\n|---|---|\n| Supercalifragilistic | Pneumonoultramicroscopic |\n"
    widths = _widths(render(md, width=80, padding=0))
    assert abs(widths[0] - widths[1]) < 0.01 and abs(sum(widths) - 80) < 0.5


def test_body_rows_are_clamped_to_the_header_width() -> None:
    table = list(parse("| A | B |\n|---|---|\n| 1 | 2 | 3 |\n| 4 |\n"))[0]
    assert isinstance(table, Table)
    assert [len(row.cells) for row in table.rows] == [2, 2]
    assert table.rows[1].cells[1].spans == ()
    svg = render("| A | B |\n|---|---|\n| 1 | 2 | 3 |\n| 4 |\n", width=300)
    assert len(_widths(svg)) == 2


def test_the_floor_width_does_not_split_a_token() -> None:
    md = "| A | llllllllllllllll tail |\n|---|---|\n"
    renderer = SVGRenderer()
    floor = renderer.min_content_width(list(parse(md)))
    svg = render(md, width=floor, padding=0)
    assert "llllllllllllllll" in svg
