"""Max-content width: the narrowest width at which markdown lays out unwrapped.

Checked against the renderer's own layout, so the number cannot drift from what
actually wraps: a hair above it nothing wraps, well below it something does.
"""

from __future__ import annotations

import base64
import math
import re
from pathlib import Path

import pytest

from mdsvg import parse
from mdsvg.fonts import FontFace, FontFaces
from mdsvg.renderer import SVGRenderer
from mdsvg.style import Style

SENTENCE = "Pipeline coverage held steady across every region this quarter"
TABLE = (
    "| Region | Pipeline coverage |\n|---|---|\n| Americas | 3.2x |\n| EMEA | 2.9x |"
)
LOGO = base64.b64encode(
    b'<svg xmlns="http://www.w3.org/2000/svg" width="412" height="128"/>'
).decode()
CASES = {
    "paragraph": SENTENCE,
    "heading": f"# {SENTENCE}",
    "bullets": f"- {SENTENCE}\n- short",
    "nested bullets": f"- top\n  - {SENTENCE}",
    "numbered": f"1. {SENTENCE}",
    "blockquote": f"> {SENTENCE}",
    "bold run": f"Plain **{SENTENCE}** plain",
    "table": TABLE,
    "inline image": f"Before ![logo](data:image/svg+xml;base64,{LOGO}) after",
}


@pytest.fixture
def renderer() -> SVGRenderer:
    return SVGRenderer()


def _height(renderer: SVGRenderer, markdown: str, width: float) -> float:
    return renderer.measure(parse(markdown), width=width).height


@pytest.mark.parametrize("markdown", CASES.values(), ids=CASES.keys())
def test_nothing_wraps_at_max_content_and_something_wraps_below(
    renderer: SVGRenderer, markdown: str
) -> None:
    natural = renderer.max_content_width(parse(markdown))
    unwrapped = _height(renderer, markdown, 10_000.0)
    assert _height(renderer, markdown, math.ceil(natural)) == unwrapped
    assert _height(renderer, markdown, natural * 0.8) > unwrapped


def test_widest_block_sets_the_width(renderer: SVGRenderer) -> None:
    short = renderer.max_content_width(parse("Short"))
    both = renderer.max_content_width(parse(f"Short\n\n{SENTENCE}"))
    assert both == renderer.max_content_width(parse(SENTENCE)) > short


def test_empty_document_has_no_width(renderer: SVGRenderer) -> None:
    assert renderer.max_content_width(parse("")) == 0.0


def test_a_block_image_asks_for_the_width_it_is_drawn_at() -> None:
    sized = "![logo](logo.svg){width=140}"
    assert SVGRenderer().max_content_width(parse(sized)) == 140
    styled = SVGRenderer(style=Style(image_width=90))
    assert styled.max_content_width(parse("![logo](logo.svg)")) == 90
    assert SVGRenderer().max_content_width(parse("![logo](logo.svg)")) == 0.0


MIXED = "Revenue is `net_revenue` minus **returns** for [the region](http://x) overall"
WRAP_CASES = {
    "inline code": "a `c` b `d` e",
    "bold after a space": "alpha **beta** gamma **delta** epsilon",
    "links": "see [one](http://x) and [two](http://y) now",
    "mixed sentence": MIXED,
    "blockquote typography": "> Revenue is `net_revenue` minus **returns** overall",
    "list item": "- Pipeline coverage held steady across every region this quarter",
}


def _real_renderer(
    fonts_dir: Path, style: Style | None = None, regular_weight: int = 400
) -> SVGRenderer:
    inter = str(fonts_dir / "InterVariable.ttf")
    return SVGRenderer(
        style=style,
        fonts=FontFaces(
            regular=FontFace(path=inter, weight=regular_weight),
            bold=FontFace(path=inter, weight=700),
            mono=FontFace(path=str(fonts_dir / "SourceCodePro-Regular.ttf")),
        ),
    )


# A body weight other than 400 catches a branch that measures without the
# weight it draws at.
@pytest.mark.parametrize("regular_weight", [400, 300])
@pytest.mark.parametrize("markdown", WRAP_CASES.values(), ids=WRAP_CASES.keys())
def test_real_fonts_one_line_at_max_content_and_wrapped_just_below(
    dbt_charts_fonts_dir: Path, markdown: str, regular_weight: int
) -> None:
    style = Style(blockquote_font_size=22.0, blockquote_font_case="upper")
    renderer = _real_renderer(dbt_charts_fonts_dir, style, regular_weight)
    blocks = parse(markdown)
    natural = renderer.max_content_width(blocks)
    unwrapped = renderer.measure(blocks, width=10_000.0).height
    assert renderer.measure(blocks, width=natural).height == unwrapped
    assert renderer.measure(blocks, width=natural - 1).height > unwrapped


def test_code_fence_width_is_the_width_it_draws(dbt_charts_fonts_dir: Path) -> None:
    renderer = _real_renderer(dbt_charts_fonts_dir)
    blocks = parse("```\nsome long code line here\n```")
    drawn = renderer.render(blocks, width=10_000.0)
    match = re.search(r'<rect[^>]*width="([\d.]+)"', drawn)
    assert match is not None
    rect_width = float(match.group(1))
    assert renderer.max_content_width(blocks) == pytest.approx(rect_width, abs=0.01)
