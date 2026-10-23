"""Vertical (x) gridlines are a per-family decision, not a per-theme one.

`_base` turns the grid on for both axes and opts exactly three families out of the
vertical grid — bar, histogram and heatmap — because a gridline running parallel to
the marks is noise. Line, area and scatter keep theirs.

These tests assert the *relationships* the design encodes, never the widths or colors
themselves (those are tunable theme values, and pinning them is banned — see
`dbt-charts/AGENTS.md`):

  * every theme draws a vertical grid on line/area, and none draws one on bar;
  * where a theme differentiates the two axes, the vertical grid is a different
    color from the horizontal one;
  * scatter is quant x quant, so both of its axes match in color *and* width;
  * the vertical grid clears a legibility floor against the canvas, yet stays exactly
    one scaffold rung quieter than the horizontal grid where the two differ;
  * a vertical tick wears its gridline's color, so the two read as one mark.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.colors import wcag_contrast
from dbt_charts.core.compile.config import (
    get_theme_style,
    user_facing_theme_names,
)
from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.compile.resolve.style.palette import palette

# The themes that thin every gridline to a hairline. No width headroom left to
# separate the axes, so they separate them by contrast instead. Hardcoded because
# it is a design subset, not a roster — a new theme is not automatically hairline.
HAIRLINE_THEMES = ["vivid", "neon"]
# The canonical roster, so a newly added theme is swept in rather than escaping.
ALL_THEMES = user_facing_theme_names()

# Below this a hairline on the canvas stops reading as a line. The light themes
# clear it by well under one scaffold rung, so this also pins the vertical grid
# against slipping back to the faintest rung.
_VERTICAL_GRID_MIN_CONTRAST = 1.25


def _resolved_axes(theme: str) -> tuple[str, str, str, str]:
    """(canvas, vertical grid, vertical tick, horizontal grid) colors for a line."""
    style = get_theme_style(theme)
    ctx = resolve_chart_style_context(style)
    x = resolved_axis_style(ctx, "axis_x", "temporal", chart_type="line", labeled=False)
    y = resolved_axis_style(
        ctx, "axis_y", "quantitative", chart_type="line", labeled=False
    )
    return style.background, x.grid.color, x.ticks.color, y.grid.color


def _rung(hex_color: str) -> tuple[str, int]:
    """(ladder, index) of a chrome color; raises if it is on no scaffold ladder."""
    for name in ("dbt-grays", "dbt-creams"):
        ladder = [c.upper() for c in palette(name)]
        if hex_color.upper() in ladder:
            return name, ladder.index(hex_color.upper())
    raise AssertionError(f"{hex_color} is not a scaffold rung")


@pytest.mark.parametrize("theme", ALL_THEMES)
def test_vertical_grid_clears_the_contrast_floor(theme):
    """However quiet the vertical grid is, it still reads against the canvas."""
    canvas, x_grid, _, _ = _resolved_axes(theme)
    assert wcag_contrast(x_grid, canvas) >= _VERTICAL_GRID_MIN_CONTRAST


@pytest.mark.parametrize("theme", ALL_THEMES)
def test_vertical_grid_sits_one_rung_below_the_horizontal(theme):
    """Where color separates the axes, one rung is the whole gap.

    Quieter means less contrast with the canvas, on light and dark themes alike.
    """
    canvas, x_grid, _, y_grid = _resolved_axes(theme)
    if x_grid == y_grid:
        pytest.skip(f"{theme} separates its axes by width, not color")
    x_ladder, x_rung = _rung(x_grid)
    y_ladder, y_rung = _rung(y_grid)
    assert x_ladder == y_ladder
    assert abs(x_rung - y_rung) == 1
    assert wcag_contrast(x_grid, canvas) < wcag_contrast(y_grid, canvas)


@pytest.mark.parametrize("theme", ALL_THEMES)
def test_vertical_tick_wears_its_gridline_color(theme):
    """A tick and its gridline mark the same value, so they read as one mark."""
    _, x_grid, x_tick, _ = _resolved_axes(theme)
    assert x_tick == x_grid
