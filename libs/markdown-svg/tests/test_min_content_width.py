"""``min_content_width``: the narrowest width that never splits a word."""

from __future__ import annotations

from mdsvg import parse
from mdsvg.renderer import SVGRenderer


def _lines_at(renderer: SVGRenderer, markdown: str, width: float) -> int:
    return len(
        renderer.measure_blocks(list(parse(markdown)), width=width)[0].line_advances
    )


def test_paragraph_floor_is_the_widest_word() -> None:
    renderer = SVGRenderer()
    md = "tiny words and Extraordinarily here"
    floor = renderer.min_content_width(list(parse(md)))
    assert _lines_at(renderer, "Extraordinarily", floor) == 1
    assert _lines_at(renderer, "Extraordinarily", floor - 3) == 2


def test_table_floor_counts_every_equal_column() -> None:
    renderer = SVGRenderer()
    md = "| Region | Units |\n|---|---|\n| West | 812 |\n"
    blocks = list(parse(md))
    floor = renderer.min_content_width(blocks)
    pad = renderer.style.table_cell_padding
    header_word = renderer.min_content_width(list(parse("**Region**")))
    assert floor >= 2 * (header_word + 2 * pad) - 2
    one_line = renderer.measure_blocks(blocks, width=floor)[0].line_advances
    narrower = renderer.measure_blocks(blocks, width=floor - 8)[0].line_advances
    assert sum(narrower) > sum(one_line)


def test_non_text_blocks_have_no_floor() -> None:
    assert SVGRenderer().min_content_width(list(parse("---"))) == 0.0


def test_list_floor_includes_indent_and_nested_items() -> None:
    renderer = SVGRenderer()
    flat = renderer.min_content_width(list(parse("- Extraordinarily")))
    nested = renderer.min_content_width(list(parse("- a\n  - Extraordinarily")))
    assert flat > renderer.style.list_indent
    assert nested > flat
