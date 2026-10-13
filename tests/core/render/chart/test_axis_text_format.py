"""An axis under an ``email`` format prints the text and never links."""

from __future__ import annotations

from ..._svg_render import render_board_to_svg
from ..._text_format_board import link_hrefs, text_format_board


def test_axis_prints_the_text_without_links() -> None:
    rows = "      - {email: a@x.com, n: 1}\n      - {email: b@x.com, n: 2}\n"
    chart = "    type: bar\n    x: email\n    y: n\n"
    svg = render_board_to_svg(
        text_format_board(rows, chart, formats="      email: email\n")
    )
    assert "a@x.com" in svg
    assert not any(h.startswith("mailto:") for h in link_hrefs(svg))
