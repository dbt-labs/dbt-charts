"""Outlier bars: pick the break value where long bars are cut.

The renderer draws the tear (past the card's top, or as floating caps); this
module only decides *where* the tear sits and, for the cap, refuses the chart
shapes it cannot represent.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from statistics import median
from typing import NamedTuple

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.authored._layer import BarLayer
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.style.theme import AxisYStyle
from dbt_charts.core.compile.resolve.chart._chart_rows import ChartDataset, restamp
from dbt_charts.core.compile.resolve.chart._domain import (
    _authored_tick_ladder,
    _axis_headroom,
    _headroom_floor,
    _measure_axis_min_step,
    layers_own_y_scale,
)
from dbt_charts.core.compile.resolve.chart.tick_values import (
    apply_headroom,
    numeric_domain_bounds,
)
from dbt_charts.core.compile.resolve.style.axis_cascade import target_tick_count
from dbt_charts.core.diagnostics.codes_compile import ERR_TORN_OUTLIER_UNSUPPORTED
from dbt_charts.core.numeric import nice_tick_values
from dbt_charts.core.utils import CellValue, coerce_numeric_cell


def unsupported(chart_id: str, reason: str) -> CompilationError:
    return CompilationError.from_code(
        ERR_TORN_OUTLIER_UNSUPPORTED, chart_id=chart_id, reason=reason
    )


def bar_extents(
    dataset: ChartDataset,
    x_field: str | None,
    y_fields: list[str],
    *,
    stacked: bool,
) -> tuple[list[float], float]:
    """Each bar's positive length (a stack's positive pile when stacked), and the
    lowest negative end (0 when no bar hangs below zero).

    A tear is cut from the positive end only; a negative bar is drawn true to scale.
    """
    extents: list[float] = []
    floor = 0.0
    for panel in restamp(dataset, x_field).panels if x_field else dataset.panels:
        piles: tuple[defaultdict[CellValue, float], defaultdict[CellValue, float]] = (
            defaultdict(float),
            defaultdict(float),
        )
        positive, negative = piles
        for index, row in enumerate(panel.rows):
            # Detection runs on every bar chart, so a row missing x is left to the
            # error the chart's own x channel raises, not a KeyError here.
            category = row.get(x_field) if x_field else f"row {index + 1}"
            for y_field in y_fields:
                value = coerce_numeric_cell(row.get(y_field))
                if value is None:
                    continue
                if stacked:
                    piles[value < 0][category] += value
                elif value < 0:
                    floor = min(floor, value)
                else:
                    extents.append(value)
        extents.extend(positive.values())
        floor = min([floor, *negative.values()])
    return extents, floor


class TornPlan(NamedTuple):
    """Where the tear sits and, when the plot reaches below zero, its low end."""

    break_value: float
    domain_min: float | None


def resolve_torn_break(
    normalized: BarChart,
    dataset: ChartDataset,
    ay: AxisYStyle,
    median_multiplier: float | None,
    *,
    stack: str | None,
    y_fields: list[str],
    layer_values: Sequence[float],
) -> TornPlan | None:
    """The chart's tear, refusing every shape a tear cannot represent.

    ``layer_values`` are the values of layers sharing the bars' scale. Call only
    for ``variant: torn_with_cap``.
    """
    continuous = ay.scale.continuous if ay.scale is not None else None
    authored = numeric_domain_bounds(
        continuous.domain if continuous is not None else None
    )
    reason = (
        "on a `stack: normalize` chart"
        if stack == "normalize"
        else "with `y_start` (ranged bars)"
        if normalized.y_start is not None
        else "with `multiples`"
        if normalized.multiples is not None
        else "with a bar layer on the shared axis_y scale"
        if not layers_own_y_scale(normalized)
        and any(isinstance(layer, BarLayer) for layer in normalized.layers)
        else "with a one-sided axis_y domain"
        if continuous is not None and continuous.domain is not None and authored is None
        else "with an authored axis_y domain above zero"
        if authored is not None and authored[0] > 0
        else "with `axis_y.scale.continuous.zero: false`"
        if continuous is not None and continuous.zero is False
        else None
    )
    if reason is not None:
        raise unsupported(normalized.id, reason)
    extents, floor = bar_extents(
        dataset,
        normalized.x,
        y_fields,
        stacked=stack not in (None, "none"),
    )
    headroom = _axis_headroom(ay)
    if authored is not None:
        low, high = authored
        torn_break = high if max(extents, default=0.0) > high else None
        domain_min = low
    else:
        torn_break = pick_torn_break(
            extents, headroom, median_multiplier, max(layer_values, default=None)
        )
        floor = min([floor, *layer_values])
        domain_min = _headroom_floor(floor, headroom) if floor < 0 else None
    if torn_break is None:
        return None
    refuse_values_past(normalized.id, torn_break, layer_values, "a layer value")
    return TornPlan(torn_break, domain_min)


def pick_torn_break(
    extents: Iterable[float],
    headroom: float | None,
    median_multiplier: float | None,
    layer_top: float | None,
) -> float | None:
    """The value the measure axis ends at, or None when no bar needs tearing.

    A bar tears when it is at least ``median_multiplier`` times the median bar
    length; ``None`` turns automatic tearing off. The break is the ordinary
    headroom-expanded domain max of the bars that stay and ``layer_top``, the
    highest value sharing the scale, so the plot reads as if the outliers were
    absent. Headroom that lifts the break past the shortest torn bar leaves
    nothing to tear.
    """
    lengths = list(extents)
    if median_multiplier is None or not lengths:
        return None
    cutoff = median_multiplier * median(lengths)
    kept = [v for v in lengths if v < cutoff]
    torn = [v for v in lengths if v >= cutoff]
    if not kept or not torn:
        return None
    top = max(kept) if layer_top is None else max(max(kept), layer_top)
    torn_break = apply_headroom(top, headroom)
    return torn_break if torn_break < min(torn) else None


class OffChartPlan(NamedTuple):
    """The break a ``variant: torn`` chart tears at, and the domain to pin for it.

    ``domain`` is None when the author already pinned the axis, or when the shape
    cannot draw an off-chart bar (the renderer then draws plain and warns).
    """

    break_value: float
    domain: tuple[float, float] | None


def plan_off_chart(
    normalized: BarChart,
    dataset: ChartDataset,
    ay: AxisYStyle,
    median_multiplier: float | None,
    *,
    stack: str | None,
    y_fields: list[str],
    layer_values: Sequence[float],
    vertical: bool,
    quantitative_x: bool,
) -> OffChartPlan | None:
    """The tear of a ``variant: torn`` chart, or None when no bar passes it.

    A pinned ``axis_y`` domain max, one-sided or not, is the break whenever a
    bar passes it, whatever ``median_multiplier`` says; without one the break is
    the axis the untorn bars alone would have, pinned here so the renderer needs
    no second rule. Shapes the off-chart bar cannot draw (a numeric x among them: its bars
    sit on a linear scale, not bands) get the break but no domain.
    """
    continuous = ay.scale.continuous if ay.scale is not None else None
    domain = continuous.domain if continuous is not None else None
    extents, floor = bar_extents(
        dataset, normalized.x, y_fields, stacked=stack not in (None, "none")
    )
    headroom = _axis_headroom(ay)
    pinned_high = domain.max if domain is not None else None
    if isinstance(pinned_high, (int, float)):
        high = float(pinned_high)
        return OffChartPlan(high, None) if max(extents, default=0.0) > high else None
    torn_break = pick_torn_break(
        extents, headroom, median_multiplier, max(layer_values, default=None)
    )
    if torn_break is None:
        return None
    drawable = (
        vertical
        and not quantitative_x
        and stack in (None, "none")
        and normalized.multiples is None
        and not any(isinstance(layer, BarLayer) for layer in normalized.layers)
        and normalized.y_start is None
        and not ay.mirror
        and not (continuous is not None and continuous.zero is False)
    )
    floor = min([floor, *layer_values])
    low = _headroom_floor(floor, headroom) if floor < 0 else 0.0
    return OffChartPlan(torn_break, (low, torn_break) if drawable else None)


def refuse_values_past(
    chart_id: str, torn_break: float, y_values: Iterable[float], what: str
) -> None:
    """Raise on a value past the break: the plot ends there, so it has no place."""
    past = [v for v in y_values if v > torn_break]
    if past:
        raise unsupported(
            chart_id,
            f"with {what} at {max(past):g}, past the break at {torn_break:g}",
        )


def torn_ticks(
    ay: AxisYStyle, torn_break: float, domain_min: float | None, *, stacked: bool
) -> tuple[float, ...]:
    """The measure axis ladder up to ``torn_break``, nothing past it.

    Below zero it keeps to ``domain_min``, except on a stack: a zero-anchored stack
    floors at the ladder's first rung, which must not sit above its negative pile,
    so the rung that rounds out past it stays.
    """
    low = 0.0 if domain_min is None else domain_min
    authored = _authored_tick_ladder(ay)
    if authored is not None:
        return tuple(v for v in authored if low <= v <= torn_break)
    target_count = target_tick_count(ay.ticks.count)
    if target_count is None:
        return ()
    ladder = nice_tick_values(
        low, torn_break, target_count, min_step=_measure_axis_min_step(ay)
    )
    return tuple(ladder if stacked else (v for v in ladder if v >= low))
