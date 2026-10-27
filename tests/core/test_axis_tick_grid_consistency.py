"""An axis tick reads at the same weight as the gridline it shares a position with.

A tick and its gridline mark the same value and are read as one mechanism
crossing the axis line. Nothing couples them: ``axis_x``, ``axis_y``, and
``axis_quantitative`` each carry ``SkipInheritSlots()``, so a theme that
overrides ``axis.grid`` without touching ``axis.ticks`` beside it silently
splits the pair into two weights.

Cases go through the chart-type patch (Layer 4) as well as the theme slots,
because that layer is where the per-family grid weights live — a bare slot
pairing would assert on a combination no chart actually renders.

Scope: ``AXIS_ROLES`` pins the family/channel pairings that carry the drift this
module guards, not every channel type a family accepts. Two combinations outside
it fail today on the four themes that never overrode ``axis.grid`` — a line on a
quantitative x, and a scatter on a temporal x — from a pre-existing mismatch
between ``_base.yaml``'s ``axis_quantitative.ticks.width`` and the ``axis_x`` /
scatter grid widths. That drift predates this guard and is not what it covers.

These assert relationships between resolved fields, never a hex or a px — theme
values stay tunable.
"""

from typing import get_args

import pytest

from dbt_charts.core.colors import wcag_contrast as _wcag_contrast
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.schema_names import ThemeName
from dbt_charts.core.compile.resolve.style.axis_cascade import (
    resolved_axis_style,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

THEMES = get_args(ThemeName)

# (chart family, slot, channel type) triples covering the axis roles a theme
# styles separately: the category/time axis, the measure axis, and the
# quantitative x a scatter draws (which the themes treat as a measure too).
AXIS_ROLES = [
    ("line", "axis_x", "temporal"),
    ("line", "axis_y", "quantitative"),
    ("area", "axis_x", "temporal"),
    # area is the family that turns measure ticks on (charts.area.axis_y.ticks),
    # so it is where a measure-axis mismatch actually reaches the page.
    ("area", "axis_y", "quantitative"),
    ("bar", "axis_y", "quantitative"),
    ("scatter", "axis_x", "quantitative"),
    ("scatter", "axis_y", "quantitative"),
]


def _axis(theme: str, family: str, slot: str, channel_type: str):
    context = resolve_chart_style_context(get_theme_style(theme))
    return resolved_axis_style(
        context,
        slot,
        channel_type,
        chart_type=family,
        labeled=False,
    )


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("family", "slot", "channel_type"), AXIS_ROLES)
def test_ticks_match_their_own_gridline(
    theme: str, family: str, slot: str, channel_type: str
):
    """Where a family draws both, the tick and its gridline are one weight.

    Cases where only one of the pair is drawn have nothing to match and are
    skipped rather than asserted into a vacuous pass — on the ``bar`` row here
    that is a visible measure grid with its ticks turned off.
    """
    axis = _axis(theme, family, slot, channel_type)
    if not (axis.ticks.visible and axis.grid.visible):
        pytest.skip(f"{family} does not draw both on {slot}")
    # A temporal x axis's ticks continue the plot floor, so they wear the measure grid's color, not the vertical grid's.
    floor = _axis(theme, family, "axis_y", "quantitative").grid
    assert axis.ticks.color == (
        floor.color
        if slot == "axis_x" and channel_type == "temporal"
        else axis.grid.color
    )
    assert axis.ticks.width == axis.grid.width


@pytest.mark.parametrize("theme", THEMES)
def test_measure_grid_reads_above_the_category_grid(theme: str):
    """The measure axis carries structure; the category axis carries scaffolding.

    Pins the direction on both channels a theme can use to express it. Width is
    the weaker of the two: vivid, neon, and solid draw every gridline at one
    hairline, so on those themes contrast is carrying the hierarchy alone, which
    is why asserting width by itself would be inert exactly where it matters.
    Contrast is measured against the canvas so the rule reads the same on a dark
    theme, where the measure grid moves away from the background rather than
    toward it.
    """
    category = _axis(theme, "line", "axis_x", "temporal")
    measure = _axis(theme, "line", "axis_y", "quantitative")
    canvas = resolve_style(get_theme_style(theme)).background

    assert measure.grid.width >= category.grid.width
    assert _wcag_contrast(measure.grid.color, canvas) >= _wcag_contrast(
        category.grid.color, canvas
    )
