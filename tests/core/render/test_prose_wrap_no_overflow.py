"""Wide prose must stay inside the box it is drawn into.

``_render_text_svg`` emits a nested ``<svg width=... viewBox=...>``. A nested SVG
establishes a viewport, and SVG viewports clip by default — so a line wider than the
box is not drawn outside it, it is cut off. Nothing errors and the board still looks
finished, which is what made this bug survive.

This is the in-process equivalent of the browser DOM scan that found it: measure every
emitted ``<text>`` with the font the browser will actually paint, and assert none of
them runs past the viewport that will clip it.

The width sweep matters. Whether a given line overflows depends on where the last word
boundary happens to fall, so a single width proves almost nothing — the original defect
looked intermittent for exactly that reason.
"""

from __future__ import annotations

import re

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.fonts import SOURCE_SERIF_4_FONT_FAMILY, get_face, get_fonts_dir
from dbt_charts.core.render.boards import _render_text_svg
from dbt_charts.core.render.sizing import body_text_font_family, get_compact_style
from mdsvg.fonts import FontMeasurer

from .._prose_plan import full_width_plan, text_nodes

_PROSE = (
    "The quick brown fox jumps over the lazy dog and keeps on running well past the "
    "point where a sentence would ordinarily stop, so it may run to eighty characters "
    "and keep going after that as well, which is the condition under which a wrapped "
    "line lands hard against the right edge. Antidisestablishmentarianism "
    "notwithstanding, internationalization remains a mouthful."
)

# Serif themes are where the defect showed; sans themes guard against a fix that
# trades one family's correctness for another's.
_THEMES = ("clarity", "paper", "stark")
_WIDTHS = tuple(float(w) for w in range(240, 1300, 53))

_SVG_WIDTH = re.compile(r'<svg[^>]*\swidth="([\d.]+)"')


def _painted_measurer(font_family: str) -> FontMeasurer:
    """A measurer over the file the browser will actually paint for this stack.

    Deliberately resolved from ``web_file`` rather than from the measurement path:
    asking the measurement path to check itself is how this bug went unnoticed for
    four months. Once the two agree, it is the same font either way.
    """
    primary = font_family.split(",", 1)[0].strip().strip("'\"")
    board = get_face(
        SOURCE_SERIF_4_FONT_FAMILY if primary.startswith("Source Serif") else primary
    )
    painted = board.web_file or board.measure_file
    measurer = FontMeasurer(str(get_fonts_dir() / painted))
    assert measurer.is_available, f"could not read painted font {painted}"
    return measurer


@pytest.mark.parametrize("theme", _THEMES)
def test_prose_never_paints_past_the_viewport_that_clips_it(theme: str) -> None:
    """No painted line runs past its nested SVG, at any width.

    Regression test for prose whose last word was sliced in half at the right edge.
    Measurement read a narrower cut of Source Serif than the browser painted, so mdsvg
    fitted more onto each line than would actually fit and the overflow was silently
    truncated by the viewport instead of reported.
    """
    resolved = resolve_style(get_theme_style(theme))
    font_size = float(get_compact_style(resolved).base_font_size)
    measurer = _painted_measurer(body_text_font_family(resolved))

    overflowing: list[str] = []
    lines_checked = 0
    for width in _WIDTHS:
        svg, _height = _render_text_svg(
            _PROSE,
            {},
            width,
            resolved,
            text_style=resolved.text,
            painted_canvas=None,
            plan=full_width_plan(resolved, width),
        )
        width_match = _SVG_WIDTH.search(svg)
        assert width_match, f"rendered block at width {width} has no nested <svg width>"
        viewport = float(width_match.group(1))

        for x_abs, raw in text_nodes(svg):
            text = raw.replace("&amp;", "&").replace("&#39;", "'")
            if not text.strip():
                continue
            lines_checked += 1
            right_edge = x_abs + measurer.measure(text, font_size)
            if right_edge > viewport:
                overflowing.append(
                    f"{width:.0f}px slot: {text[-45:]!r} reaches "
                    f"{right_edge:.1f}px in a {viewport:.0f}px box"
                )

    # Guards against the sweep silently measuring nothing (a regex that stops matching
    # would otherwise turn this into a test that always passes).
    assert lines_checked >= 3 * len(_WIDTHS), (
        f"sweep laid out only {lines_checked} lines across {len(_WIDTHS)} widths"
    )
    assert not overflowing, (
        f"{len(overflowing)} of {lines_checked} lines paint past the viewport that "
        f"clips them. First five: " + "; ".join(overflowing[:5])
    )
