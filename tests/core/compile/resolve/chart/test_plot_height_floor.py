"""``estimate_plot_height`` / ``plot_height_floor_px``: measuring a starved plot.

Pure-function unit tests. See ``test_bar_plot_height_floor.py`` for the
integration path (``resolve()`` on a real ``BarChart`` and the
``WARN-PLOT-HEIGHT-BELOW-MINIMUM`` diagnostic) and ``constants/estimates.py`` for the calibrated
constants below.

This function only measures — it never decides to remove chrome. See its
module docstring for why the earlier chrome-reduction ladder was retired.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.compile.models.primitives import FontStyle
from dbt_charts.core.compile.models.style.theme import AxisTitleStyle
from dbt_charts.core.compile.resolve.chart.plot_height_floor import (
    axis_title_cost_px,
    estimate_plot_height,
    plot_height_floor_px,
    subtitle_cost_px,
)
from dbt_charts.core.constants.layout import VEGA_AXIS_TITLE_PADDING

# The wired-up caller (bar.py, and now every cartesian family's own rung-2
# floor check) passes estimate_cartesian_plot_height's output as
# `card_height`, which is the card height the renderer builds: the
# aspect-ratio content height plus card padding on both sides. For the
# default bar aspect_ratio=1.5 (clamped 150-400px) and card_padding=16.0
# that is 300/1.5 + 32 = 232.0 at a 300px card, and 400 (clamped) + 32 =
# 432.0 at a 640px one.
_CARD_PADDING = 16.0
_TINY_CARD_HEIGHT = 232.0
_WIDE_CARD_HEIGHT = 432.0

# An arbitrary plausible legend height for these pure-function tests --
# `estimate_plot_height` treats `legend_height_px` as an opaque input, not
# something it derives from config itself, so this value need not match any
# real legend calculation.
_CALIBRATION_LEGEND_HEIGHT = 63.0
_AXIS_TITLE_HEIGHT = 21.0
_SUBTITLE_HEIGHT = 14.0


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


def _defaults(**overrides: float) -> dict[str, float]:
    base: dict[str, float] = {
        "card_padding_px": _CARD_PADDING,
        "legend_height_px": _CALIBRATION_LEGEND_HEIGHT,
        "subtitle_height_px": _SUBTITLE_HEIGHT,
        "axis_title_height_px": _AXIS_TITLE_HEIGHT,
    }
    base.update(overrides)
    return base


def _below_floor(
    card_height: float,
    *,
    card_padding_px: float,
    legend_height_px: float,
    subtitle_height_px: float,
    axis_title_height_px: float,
) -> bool:
    """The comparison ``_resolve_bar`` makes, built from the same two
    imported pieces it uses — no test-side copy of the formula."""
    return estimate_plot_height(
        card_height,
        card_padding_px=card_padding_px,
        legend_height_px=legend_height_px,
        subtitle_height_px=subtitle_height_px,
        axis_title_height_px=axis_title_height_px,
    ) < plot_height_floor_px(card_height)


def test_a_roomy_card_is_not_flagged() -> None:
    """A card tall enough to carry all its chrome and still clear the floor."""
    assert _below_floor(_WIDE_CARD_HEIGHT, **_defaults()) is False


def test_no_chrome_present_is_not_flagged() -> None:
    """A single-series bar with no legend, no subtitle, no axis titles has
    nothing competing for the plot's height — never flagged."""
    assert (
        _below_floor(
            _TINY_CARD_HEIGHT,
            card_padding_px=_CARD_PADDING,
            legend_height_px=0.0,
            subtitle_height_px=0.0,
            axis_title_height_px=0.0,
        )
        is False
    )


def test_calibration_chart_is_flagged() -> None:
    """At 300px the calibration chart's estimate (50px) is below the 0.30
    floor (69.6px of a 232px card) with all its chrome present."""
    assert _below_floor(_TINY_CARD_HEIGHT, **_defaults()) is True


def test_large_legend_alone_can_trip_the_floor() -> None:
    """The legend's height alone, with no other chrome, is enough to flag a
    severely squeezed card."""
    assert (
        _below_floor(
            _TINY_CARD_HEIGHT,
            card_padding_px=_CARD_PADDING,
            legend_height_px=500.0,
            subtitle_height_px=0.0,
            axis_title_height_px=0.0,
        )
        is True
    )


def test_floor_already_cleared_is_not_flagged() -> None:
    """A tall enough card (large aspect ratio) never trips the floor,
    however much chrome it carries."""
    assert _below_floor(1000.0, **_defaults()) is False


def test_estimate_plot_height_matches_the_calibration_chart() -> None:
    """Direct check of the arithmetic plot_height_below_floor relies on --
    50px, matching the real 300px render this constant set was calibrated
    against."""
    assert estimate_plot_height(_TINY_CARD_HEIGHT, **_defaults()) == 50.0


def test_estimate_plot_height_can_go_negative() -> None:
    """A severely squeezed card's estimate is negative, not clamped to
    zero -- callers compare against a floor, not against zero."""
    assert estimate_plot_height(90.0, **_defaults()) < 0


def test_card_padding_is_charged_against_the_plot() -> None:
    """The card's padding sits inside ``card_height`` and the plot never
    gets it, so it comes off the plot estimate on both sides.

    Keeping the padding inside the card (rather than netting it out of
    ``card_height``) is what makes the floor a fraction of the card the
    renderer actually draws.
    """
    without = estimate_plot_height(_TINY_CARD_HEIGHT, **_defaults(card_padding_px=0.0))
    assert estimate_plot_height(_TINY_CARD_HEIGHT, **_defaults()) == without - 32.0


def test_axis_title_costs_its_font_size_plus_padding() -> None:
    title = AxisTitleStyle(font=FontStyle(size=11), padding=10.0)
    assert axis_title_cost_px(title) == 21.0


def test_a_hidden_axis_title_costs_nothing() -> None:
    title = AxisTitleStyle(visible=False, font=FontStyle(size=11), padding=10.0)
    assert axis_title_cost_px(title) == 0.0


def test_subtitle_costs_one_line_of_its_font() -> None:
    font = FontStyle(size=11)
    assert subtitle_cost_px("A subtitle", font) == 14.0
    assert subtitle_cost_px(None, font) == 0.0


def test_cost_helpers_follow_a_non_default_font_size_and_padding() -> None:
    title = AxisTitleStyle(font=FontStyle(size=16), padding=7.0)
    assert axis_title_cost_px(title) == 23.0
    assert subtitle_cost_px("A subtitle", FontStyle(size=16)) == 19.0


def test_an_unset_axis_title_padding_is_vega_lites_default() -> None:
    title = AxisTitleStyle(font=FontStyle(size=11))
    assert axis_title_cost_px(title) == 11.0 + VEGA_AXIS_TITLE_PADDING


def test_an_unset_font_size_raises() -> None:
    with pytest.raises(ValueError, match="axis title font size is unset"):
        axis_title_cost_px(AxisTitleStyle(padding=10.0))
    with pytest.raises(ValueError, match="subtitle font size is unset"):
        subtitle_cost_px("A subtitle", FontStyle())
