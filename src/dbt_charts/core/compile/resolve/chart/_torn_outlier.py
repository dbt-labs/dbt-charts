"""Torn outlier bars: pick the break value where long bars are cut.

The renderer draws the tear and the floating caps; this module only decides
*where* the tear sits and refuses the chart shapes a tear cannot represent.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import NamedTuple

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.authored._layer import BarLayer
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.style.theme import AxisYStyle, BarTornOutlierStyle
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
from dbt_charts.core.constants.bar import (
    TORN_OUTLIER_MIN_BARS,
    TORN_OUTLIER_MIN_RATIO,
)
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
            category = row[x_field] if x_field else f"row {index + 1}"
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
    torn: BarTornOutlierStyle,
    *,
    stack: str | None,
    y_fields: list[str],
    layer_values: Sequence[float],
) -> TornPlan | None:
    """The chart's tear, refusing every shape a tear cannot represent.

    ``layer_values`` are the values of layers sharing the bars' scale. Call only
    when ``torn.enabled``.
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
        else "with both `torn_outlier.at` and an authored axis_y domain; "
        "the domain's max is the break"
        if authored is not None and torn.at is not None
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
            extents, headroom, torn.at, max(layer_values, default=None)
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
    at: float | None,
    layer_top: float | None,
) -> float | None:
    """The value the measure axis ends at, or None when no bar needs tearing.

    An authored ``at`` is the break whenever a bar passes it. Otherwise the
    bars are sorted longest first and the break goes after the first ``k``
    (at most a third of them) where the next bar is shortest relative to the
    last one cut; the break is then the ordinary headroom-expanded domain max
    of the bars that stay and ``layer_top``, the highest value of a layer
    sharing the scale, so the plot reads as if the outliers were absent.
    Headroom that lifts the break past the shortest cut bar leaves nothing to tear.
    """
    ordered = sorted(extents, reverse=True)
    if at is not None:
        return at if ordered and ordered[0] > at else None
    if len(ordered) < TORN_OUTLIER_MIN_BARS:
        return None
    candidates = [
        (ordered[k - 1] / ordered[k], k)
        for k in range(1, max(1, len(ordered) // 3) + 1)
        if ordered[k] > 0
    ]
    if not candidates:
        return None
    ratio, k = max(candidates, key=lambda c: c[0])
    if ratio < TORN_OUTLIER_MIN_RATIO:
        return None
    kept = ordered[k] if layer_top is None else max(ordered[k], layer_top)
    torn_break = apply_headroom(kept, headroom)
    return torn_break if torn_break < ordered[k - 1] else None


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
