"""No line break between an inline span and punctuation it touches."""

from __future__ import annotations

import base64

import pytest

from mdsvg import Paragraph, Style, parse
from mdsvg.fonts import WrapPiece, wrap_measured_pieces
from mdsvg.renderer import SVGRenderer

QUOTE = (
    "“It may not have been a very clever fellow who invented curves, "
    "but he was assuredly lazy” (*Charts and Graphs*, 1923)."
)


CODE_SPAN = (
    "The temporal axis lands on that row (`'25`) at the first label, and then "
    "(`Jan'26`) at the second label, so a break must keep each pair together."
)


_SQUARE = (
    "data:image/svg+xml;base64,"
    + base64.b64encode(
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" '
        b'height="16"><rect width="16" height="16"/></svg>'
    ).decode()
)
IMAGE = (
    "A small mark sits beside each name (![mark](" + _SQUARE + "){width=16 height=16}) "
    "in this sentence, so a break must keep the image with its parentheses."
)


def _lines(markdown: str, width: float, avoid_runts: bool = False) -> list[str]:
    renderer = SVGRenderer(Style(avoid_runts=avoid_runts))
    paragraph = parse(markdown)[0]
    assert isinstance(paragraph, Paragraph)
    runs = renderer._build_text_runs(paragraph.spans, width)
    wrapped = renderer._wrap_runs(runs, width, renderer.style.base_font_size)
    return ["".join(run.text for run in line) for line in wrapped]


@pytest.mark.parametrize("avoid_runts", [False, True])
@pytest.mark.parametrize("width", range(150, 420, 5))
def test_no_break_between_span_and_adjacent_punctuation(
    width: int, avoid_runts: bool
) -> None:
    for line in _lines(QUOTE, float(width), avoid_runts):
        assert not line.endswith("("), f"orphaned '(' at width {width}: {line!r}"
        assert not line.startswith(","), f"orphaned ',' at width {width}: {line!r}"


@pytest.mark.parametrize("avoid_runts", [False, True])
@pytest.mark.parametrize("width", range(120, 420, 5))
def test_no_break_between_code_span_and_adjacent_punctuation(
    width: int, avoid_runts: bool
) -> None:
    for line in _lines(CODE_SPAN, float(width), avoid_runts):
        assert not line.endswith("("), f"orphaned '(' at width {width}: {line!r}"
        assert not line.startswith(")"), f"orphaned ')' at width {width}: {line!r}"
        assert not line.startswith(","), f"orphaned ',' at width {width}: {line!r}"


@pytest.mark.parametrize("avoid_runts", [False, True])
@pytest.mark.parametrize("width", range(120, 420, 5))
def test_no_break_between_inline_image_and_adjacent_punctuation(
    width: int, avoid_runts: bool
) -> None:
    for line in _lines(IMAGE, float(width), avoid_runts):
        assert not line.endswith("("), f"orphaned '(' at width {width}: {line!r}"
        assert not line.startswith(")"), f"orphaned ')' at width {width}: {line!r}"


def test_glued_piece_moves_with_its_predecessor() -> None:
    def piece(text: str, separator: str = "", glued: bool = False) -> WrapPiece:
        return WrapPiece(
            text=text,
            width=10.0 * len(text),
            separator=separator,
            separator_width=5.0 if separator else 0.0,
            glued=glued,
        )

    lines = wrap_measured_pieces(
        [piece("aa"), piece("(", " "), piece("bb", glued=True), piece(",", glued=True)],
        max_width=45.0,
    )
    assert [[p.text for p in line] for line in lines] == [["aa"], ["(", "bb", ","]]


@pytest.mark.parametrize("avoid_runts", [False, True])
@pytest.mark.parametrize("max_width", range(40, 120, 3))
def test_glue_carry_never_exceeds_max_width(max_width: int, avoid_runts: bool) -> None:
    pieces = [
        WrapPiece(text="aa", width=20.0),
        WrapPiece(text="(", width=10.0, separator=" ", separator_width=5.0),
        WrapPiece(text="long", width=38.0, glued=True),
        WrapPiece(text=")", width=10.0, glued=True),
        WrapPiece(text="zz", width=20.0, separator=" ", separator_width=5.0),
    ]
    for line in wrap_measured_pieces(pieces, float(max_width), avoid_runts):
        width = sum(p.width for p in line) + sum(p.separator_width for p in line[1:])
        if len(line) > 1:
            assert width <= max_width, (max_width, [p.text for p in line])
