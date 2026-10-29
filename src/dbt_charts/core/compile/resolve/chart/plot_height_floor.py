"""Height floor for narrow cartesian cards: a measurement, not a mutation.

Extends the ``pie`` family's ``WHEEL_DOMINANCE_MIN_RATIO`` precedent
(``libs/chart-svg/chart-renderer/src/families/pie.rs``) to cartesian charts: below a
calibrated fraction of the card's own height, the plot is starved. Shared by
every cartesian family that estimates a plot-height floor -- currently
``bar.py``'s own ``WARN-PLOT-HEIGHT-BELOW-MINIMUM`` measurement and this
module's ``legend_wrap_fits_height_budget`` (``_axes.py``), which asks this
same floor whether a candidate wrapped legend still leaves the plot clear,
rather than carrying a second, independently-calibrated budget.

Two invariants hold this together:

- **The floor is a fraction of the card's own height**, so card width cannot
  gate it. A short, wide card carrying heavy chrome is starved the same way a
  narrow one is.
- **``card_height`` is the card the renderer will actually build** —
  ``get_item_content_height`` (render/sizing.py), which is the aspect-ratio
  content height *plus* ``2 * style.frame.card_padding``. Measuring the
  content box alone put the floor on a card two paddings shorter than the
  drawn one. Card padding is therefore charged here as chrome, where it
  belongs, rather than netted out of the card.

Nothing in this module removes chrome. An earlier version also chose what to
drop (axis titles, then the subtitle) to keep the plot clear of the floor;
that ladder was retired because for a bar the legend is typically the only
thing naming a series, so it can never be a candidate to give way, and a
ladder sparing the legend fires almost never. ``bar.py`` surfaces the
measurement as ``WARN-PLOT-HEIGHT-BELOW-MINIMUM`` instead.

The chrome figures are calibrated constants (``constants/estimates.py``),
not live measurements: Vega-Lite's ``autosize:fit`` divides a card's height
between chrome and plot at render time, and replicating that at compile time
is neither tractable nor how the sibling ``_stack_legend_should_yield``
estimator does it. They carry a known bias, recorded beside them in
``constants/estimates.py``. ``legend_height_px`` is the one input this module
does NOT read from those constants -- it is a caller-computed fact (a family- and
layout-specific legend estimate), passed in rather than read here, so this
module stays legend-shape-agnostic.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.primitives import FontStyle
from dbt_charts.core.compile.models.style.theme import AxisTitleStyle
from dbt_charts.core.constants.estimates import PLOT_HEIGHT_FLOOR_IRREDUCIBLE_PX
from dbt_charts.core.constants.layout import (
    PLOT_HEIGHT_FLOOR_RATIO,
    VEGA_AXIS_TITLE_PADDING,
    VEGA_SUBTITLE_PADDING,
)

__all__ = [
    "axis_title_cost_px",
    "estimate_plot_height",
    "plot_height_floor_px",
    "subtitle_cost_px",
]


def estimate_plot_height(
    card_height: float,
    *,
    card_padding_px: float,
    legend_height_px: float,
    subtitle_height_px: float,
    axis_title_height_px: float,
) -> float:
    """Estimate the plot's own height once its known chrome is subtracted.

    Args:
        card_height: Total card height, matching what the renderer builds for
            this chart — an aspect-ratio estimate, not a live measurement.
        card_padding_px: ``style.frame.card_padding``, charged on both sides.
        legend_height_px: The legend's own estimated height. 0.0 when no
            legend renders, including when a series-naming rail replaces it —
            that rail sits beside the plot and costs width, not height.
        subtitle_height_px: ``subtitle_cost_px()`` of the chart's subtitle.
        axis_title_height_px: ``axis_title_cost_px()`` of the axis title on
            the horizontal rail. Only that one costs the plot height; the
            other is rotated and costs width.

    Returns:
        The estimated plot height in px. Can be negative for a severely
        squeezed card — callers compare it against a floor, not zero.
    """
    return (
        card_height
        - 2 * card_padding_px
        - PLOT_HEIGHT_FLOOR_IRREDUCIBLE_PX
        - legend_height_px
        - axis_title_height_px
        - subtitle_height_px
    )


def axis_title_cost_px(title: AxisTitleStyle) -> float:
    """Height an axis title costs the plot: its font size plus its padding.

    An unset padding is Vega's axis default.
    """
    if title.visible is False:
        return 0.0
    if title.font.size is None:
        raise ValueError("axis title font size is unset; cannot estimate its height")
    padding = VEGA_AXIS_TITLE_PADDING if title.padding is None else title.padding
    return float(title.font.size) + padding


def subtitle_cost_px(subtitle: str | None, font: FontStyle) -> float:
    """Height one rendered subtitle line costs: its font size plus Vega's gap.

    A wrapped subtitle under-counts.
    """
    if not subtitle:
        return 0.0
    if font.size is None:
        raise ValueError("subtitle font size is unset; cannot estimate its height")
    return float(font.size) + VEGA_SUBTITLE_PADDING


def plot_height_floor_px(card_height: float) -> float:
    """The height this card's plot must clear to stay readable.

    The single definition of the floor, shared by the resolver that decides,
    the detector that reports the number to the author, and the tests. A
    second copy anywhere would let the two disagree silently.
    """
    return card_height * PLOT_HEIGHT_FLOOR_RATIO
