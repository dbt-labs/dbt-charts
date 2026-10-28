"""Every divider honors ``visible`` and ``style``; a partial write recolors a
divider without switching it on or off."""

from __future__ import annotations

import re

from dbt_charts.core.compile.models.board.authored import AuthoredBoard

from ._svg_render import render_board_to_svg

_BOARD = """title: T
queries:
  q:
    type: values
    rows:
      - {{k: a, v: 1, role: value}}
      - {{k: b, v: 2, role: value}}
      - {{k: Total, v: 3, role: total}}
charts:
  t:
    query: q
    type: table
    style:
      row:
        role: role
rows: [t]
{board_style}"""


def _render(board_style: str = "") -> str:
    return render_board_to_svg(_BOARD.format(board_style=board_style))


def _footer_lines(svg: str) -> list[str]:
    return re.findall(r'<line[^>]*stroke-width="0.5"[^>]*/>', svg)


def test_footer_divider_draws_by_default() -> None:
    assert len(_footer_lines(_render())) == 1


def test_footer_divider_hidden() -> None:
    svg = _render("style:\n  footer:\n    divider:\n      visible: false\n")
    assert _footer_lines(svg) == []


def test_footer_divider_dashed() -> None:
    svg = _render("style:\n  footer:\n    divider:\n      dasharray: 6 3\n")
    (line,) = _footer_lines(svg)
    assert 'stroke-dasharray="6 3"' in line


def test_footer_divider_color_alone_keeps_it_on() -> None:
    svg = _render("style:\n  footer:\n    divider:\n      color: '#abcdef'\n")
    assert 'stroke="#abcdef"' in svg


def _table_dividers(svg: str) -> list[str]:
    return re.findall(
        r'<(?:rect|line)[^>]*shape-rendering="crispEdges"[^>]*/?>|<line[^>]*stroke-dasharray[^>]*/>',
        svg,
    )


def test_header_divider_hidden_removes_its_line() -> None:
    drawn = _table_dividers(_render())
    hidden = _table_dividers(
        _render(
            "style:\n  charts:\n    table:\n      header:\n        divider:\n"
            "          visible: false\n"
        )
    )
    assert len(hidden) == len(drawn) - 1


def test_table_dividers_dashed() -> None:
    svg = _render(
        "style:\n  charts:\n    table:\n      header:\n        divider:\n"
        "          dasharray: 6 3\n"
    )
    dashed = re.findall(r'<line[^>]*stroke-dasharray="6 3"[^>]*/>', svg)
    # The header line is dashed and the footer line is not.
    assert any("stroke-width" in line for line in dashed)
    assert len(dashed) == 1


def test_row_divider_off_by_default_and_on_when_switched() -> None:
    rows = "style:\n  charts:\n    table:\n      row:\n        divider:\n          visible: true\n"
    assert len(_table_dividers(_render(rows))) > len(_table_dividers(_render()))


def test_total_divider_is_independent_of_the_summary_divider() -> None:
    base = len(_table_dividers(_render()))
    off = len(
        _table_dividers(
            _render(
                "style:\n  charts:\n    table:\n      row:\n        roles:\n"
                "          total:\n            divider:\n              visible: false\n"
            )
        )
    )
    # A total row draws a double line.
    assert base - off == 2


def test_column_divider_partial_write_validates_and_draws_nothing() -> None:
    board = AuthoredBoard.model_validate(
        {"title": "t", "style": {"text": {"column": {"divider": {"color": "red"}}}}}
    )
    assert board.style is not None
    text = (
        "title: T\n"
        "text: " + "word " * 600 + "\n"
        "style:\n"
        "  frame:\n"
        "    width: 1000\n"
        "  text:\n"
        "    column:\n"
        "      max_number: 2\n"
        "      divider:\n"
        "        color: red\n"
    )
    assert 'stroke="red"' not in render_board_to_svg(text)
    assert 'stroke="red"' in render_board_to_svg(
        text.replace(
            "        color: red\n", "        color: red\n        visible: true\n"
        )
    )


_MARKDOWN = "title: T\ntext: |\n  above\n\n  ---\n\n  below\n"


def _markdown_rule(divider: str) -> str:
    return render_board_to_svg(
        f"{_MARKDOWN}style:\n  text:\n    divider:\n      {divider}\n"
    )


def test_markdown_rule_hidden_keeps_the_text() -> None:
    drawn = _markdown_rule("color: '#e0e0e0'")
    hidden = _markdown_rule("visible: false")
    assert 'fill="#e0e0e0"' in drawn
    assert 'fill="#e0e0e0"' not in hidden
    assert "above" in hidden and "below" in hidden


def test_markdown_rule_dashed() -> None:
    assert 'stroke-dasharray="6 3"' in _markdown_rule("dasharray: 6 3")


def test_table_divider_accepts_a_named_color() -> None:
    svg = _render(
        "style:\n  charts:\n    table:\n      header:\n        divider:\n"
        "          color: rebeccapurple\n"
    )
    assert 'fill="rebeccapurple"' in svg
