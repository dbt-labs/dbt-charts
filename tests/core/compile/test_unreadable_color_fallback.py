"""No breaking change: a background or mark color dbt Charts cannot read.

RJ's call (2026-09-22): nothing that compiled on main stops compiling. An
authored background the engine cannot read (oklch(), var(--x), a malformed
function, a typo) contributes no ink of its own -- ink_canvas() skips that
layer, same as an inherited one, and the result falls through to the theme's
own canvas. An authored mark color the engine cannot read is inked as
itself -- mark_ink() returns it unchanged, the old companion-lookup's own
contract for a color with no `-dark` twin.

A bare-identifier-shaped string that names neither a real color nor a real
theme role/shipped palette (`"bloo"`) is still rejected with
ERR-PALETTE-UNKNOWN -- that is an invalid name, not a valid CSS color, and
the role/typo distinction this guards is unrelated to color-grammar
readability.
"""

from __future__ import annotations

from dbt_charts.core.colors import hex_to_oklch
from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.compile.config import get_theme_style


def _board_background(value: str, theme: str = "stark") -> str:
    return f"""
theme: {theme}
style:
  background: {value}
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - revenue
"""


def test_oklch_background_on_stark_derives_ink_against_the_theme_canvas():
    """`oklch()` is a real CSS color function browsers understand, but the
    engine's own grammar (hex, a CSS keyword name, rgb()/rgba(),
    hsl()/hsla(), transparent/none) does not cover it -- it contributes no
    ink of its own, so ink_canvas() falls through to the theme's own
    canvas, exactly as an unauthored background would."""
    result = compile_board(_board_background('"oklch(0.7 0.1 250)"'))
    assert result.success, result.errors
    assert result.board is not None
    assert (
        result.board.chart_style_context.ink_canvas
        == get_theme_style("stark").background.lower()
    )


def test_oklch_background_on_neon_inks_lighter_than_the_mark():
    """Same fallback, on a dark theme: the composited canvas is neon's own
    near-black default, so every mark's dark-companion ink still reads
    lighter than the mark, same as any other neon canvas."""
    result = compile_board(_board_background('"oklch(0.7 0.1 250)"', theme="neon"))
    assert result.success, result.errors
    assert result.board is not None
    ctx = result.board.chart_style_context
    assert ctx.ink_canvas == get_theme_style("neon").background.lower()
    for mark, ink in zip(ctx.palette, ctx.dark_companion_palette, strict=True):
        assert hex_to_oklch(ink)[0] > hex_to_oklch(mark)[0], (mark, ink)


def _board_palette(value: str) -> str:
    return f"""
title: T
style:
  charts:
    color:
      categorical:
        palette: {value}
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - revenue
"""


def test_unreadable_palette_member_compiles_and_inks_as_itself():
    """`palette: ["not-a-color!!!", "#4e79a7"]` -- neither a color dbt
    Charts can read nor a candidate role name. Compiles clean; the
    unreadable stop's own ink is the string unchanged."""
    result = compile_board(_board_palette('["not-a-color!!!", "#4e79a7"]'))
    assert result.success, result.errors
    assert result.board is not None
    ctx = result.board.chart_style_context
    assert ctx.dark_companion_palette[0] == "not-a-color!!!"


def test_nan_channel_background_falls_back_to_the_theme_canvas():
    """`float("nan")` parses without raising, so a naive channel parse
    would silently clamp it to a real color -- parse_css_color rejects it
    outright, and the background falls back the same as any other
    unreadable one."""
    result = compile_board(_board_background('"rgb(nan,0,0)"'))
    assert result.success, result.errors
    assert result.board is not None
    assert (
        result.board.chart_style_context.ink_canvas
        == get_theme_style("stark").background.lower()
    )


def test_malformed_comma_syntax_background_falls_back_to_the_theme_canvas():
    """`rgb(10,,20,30)` -- a double comma, an empty channel between two
    real ones -- is malformed CSS, not a color with an empty channel;
    parse_css_color rejects it, and the background falls back."""
    result = compile_board(_board_background('"rgb(10,,20,30)"'))
    assert result.success, result.errors
    assert result.board is not None
    assert (
        result.board.chart_style_context.ink_canvas
        == get_theme_style("stark").background.lower()
    )
