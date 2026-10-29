"""Sweep of the rule gray: every theme against the mark colors a rule can cross.

Each case renders a real board through the engine and reads the rule's stroke
and its label out of the SVG. Light themes draw rung 7 of the dbt-grays ladder;
paper and stark keep the rule ink they set; neon picks per rule the quietest
rung that holds 3:1 against the canvas and the mark crossed, a step louder off
any gridline, never from the ladder's two pale or two near-black ends.
"""

from __future__ import annotations

import re

import pytest

from dbt_charts.core.colors import wcag_contrast
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.compile.resolve.style.palette import (
    color,
    color_from_theme,
    palette,
    variant,
)

from .._svg_render import render_board_to_svg

GRAYS = palette("dbt-grays")
RUNG_7 = GRAYS[6]
# Rungs 1, 2, 11 and 12 are never eligible for a picked rule.
ELIGIBLE = GRAYS[2:10]
MIN_CONTRAST = 3.0
GRID_CLEARANCE = 1.25

SLOTS = [color(f"vivid-10.{k}") for k in range(1, 11)]
VARIANTS = [
    variant(slot, kind) for slot in SLOTS for kind in ("light", "dark", "pale", "deep")
]
# The rule at 120 crosses the 150 bar; at 400 it floats above both.
CROSSES, CLEAR = 120, 400


def _board(theme: str, fill: str, rule_y: int) -> str:
    return f"""
theme: {theme}
queries:
  q1:
    type: values
    rows:
      - {{month: Jan, revenue: 100}}
      - {{month: Feb, revenue: 150}}
charts:
  c1:
    query: q1
    type: bar
    x: month
    y: revenue
    style:
      color:
        categorical:
          single_series_palette: ['{fill}']
    annotations:
      - type: rule
        y: {rule_y}
        text: Goal
rows:
  - c1
"""


def _rule_ink(theme: str, fill: str, rule_y: int) -> tuple[str, str]:
    """The rule's stroke and its label's fill."""
    svg = render_board_to_svg(_board(theme, fill, rule_y))
    stroke = re.search(r'class="annotations-marks".*?<line[^>]*stroke="(#\w+)"', svg)
    label = re.search(r'aria-label="Goal".*?fill="(#\w+)"', svg)
    assert stroke and label
    return stroke.group(1).upper(), label.group(1).upper()


@pytest.mark.parametrize("theme", ["clarity", "vivid"])
@pytest.mark.parametrize("rule_y", [CROSSES, CLEAR])
def test_a_light_theme_draws_every_rule_in_rung_seven(theme: str, rule_y: int) -> None:
    for fill in SLOTS:
        assert _rule_ink(theme, fill, rule_y) == (RUNG_7, RUNG_7), fill


@pytest.mark.parametrize("theme", ["paper", "stark"])
def test_paper_and_stark_keep_their_own_rule_ink(theme: str) -> None:
    ctx = resolve_chart_style_context(get_theme_style(theme))
    ink = color_from_theme("muted", palettes=ctx.palettes, roles=ctx.roles).upper()
    for fill in SLOTS:
        assert _rule_ink(theme, fill, CROSSES) == (ink, ink), fill


def _neon_grids() -> list[str]:
    ctx = resolve_chart_style_context(get_theme_style("neon"))
    grids = (ctx.axis.grid.color, ctx.axis_x.grid.color, ctx.axis_y.grid.color)
    return [g for g in grids if g is not None]


def _quietest_holding(canvas: str, crossed: list[str]) -> str | None:
    """The eligible rung nearest the canvas that holds 3:1 against it and the marks."""
    holding = [
        g
        for g in ELIGIBLE
        if all(wcag_contrast(g, c) >= MIN_CONTRAST for c in [canvas, *crossed])
    ]
    return min(holding, key=lambda g: wcag_contrast(g, canvas), default=None)


@pytest.mark.parametrize("rule_y", [CROSSES, CLEAR])
def test_neon_picks_a_gray_that_holds_off_the_canvas_the_mark_and_the_grid(
    rule_y: int,
) -> None:
    canvas = get_theme_style("neon").background
    assert canvas is not None
    grids = _neon_grids()
    eligible = {g.upper() for g in ELIGIBLE}
    picks = set()
    for fill in SLOTS + VARIANTS:
        stroke, label = _rule_ink("neon", fill, rule_y)
        assert label == stroke, fill
        assert stroke in eligible, f"{fill}: {stroke} is an end rung"
        for grid in grids:
            assert wcag_contrast(stroke, grid) >= GRID_CLEARANCE, (fill, stroke, grid)
        if any(wcag_contrast(g, canvas) >= MIN_CONTRAST for g in ELIGIBLE):
            assert wcag_contrast(stroke, canvas) >= MIN_CONTRAST, (fill, stroke)
        quietest = _quietest_holding(canvas, [fill] if rule_y == CROSSES else [])
        if quietest is not None and quietest.upper() != stroke:
            # Only the gridline check may move a pick off the quietest rung that holds.
            assert any(wcag_contrast(quietest, g) < GRID_CLEARANCE for g in grids), fill
        picks.add(stroke)
    # The pick follows the mark: one gray for every fill would be a fixed color.
    assert len(picks) > 1 or rule_y == CLEAR
