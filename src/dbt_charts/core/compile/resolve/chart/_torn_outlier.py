"""Torn outlier bars: pick the break value where long bars are cut.

The renderer draws the tear and the floating caps; this module only decides
*where* the tear sits and refuses the chart shapes a tear cannot represent.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.style.theme import AxisYStyle, BarTornOutlierStyle
from dbt_charts.core.compile.resolve.chart._chart_rows import ChartDataset, restamp
from dbt_charts.core.compile.resolve.chart._domain import (
    _authored_tick_ladder,
    _axis_headroom,
    _measure_axis_min_step,
)
from dbt_charts.core.compile.resolve.chart.tick_values import apply_headroom
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
    chart_id: str,
    dataset: ChartDataset,
    x_field: str | None,
    y_fields: list[str],
    *,
    stacked: bool,
) -> list[float]:
    """Each bar's length: a stack's total when stacked.

    Raises on a negative value, naming its category: a tear is cut from a
    zero-based bar end and has no honest form below zero.
    """
    extents: list[float] = []
    for panel in restamp(dataset, x_field).panels if x_field else dataset.panels:
        totals: defaultdict[CellValue, float] = defaultdict(float)
        for index, row in enumerate(panel.rows):
            category = row[x_field] if x_field else f"row {index + 1}"
            for y_field in y_fields:
                value = coerce_numeric_cell(row.get(y_field))
                if value is None:
                    continue
                if value < 0:
                    raise unsupported(
                        chart_id,
                        f"with a negative bar: category {category!r} has "
                        f"{y_field} = {value:g}",
                    )
                if stacked:
                    totals[category] += value
                else:
                    extents.append(value)
        extents.extend(totals.values())
    return extents


def resolve_torn_break(
    normalized: BarChart,
    dataset: ChartDataset,
    ay: AxisYStyle,
    torn: BarTornOutlierStyle,
    *,
    stack: str | None,
    y_fields: list[str],
) -> float | None:
    """The chart's break value, refusing every shape a tear cannot represent.

    Call only when ``torn.enabled``.
    """
    reason = (
        "on a `stack: normalize` chart"
        if stack == "normalize"
        else "with `y_start` (ranged bars)"
        if normalized.y_start is not None
        else "with `layers`"
        if normalized.layers
        else "with `multiples`"
        if normalized.multiples is not None
        else "with an authored axis_y domain"
        if ay.scale is not None
        and ay.scale.continuous is not None
        and ay.scale.continuous.domain is not None
        else "with `axis_y.scale.continuous.zero: false`"
        if ay.scale is not None
        and ay.scale.continuous is not None
        and ay.scale.continuous.zero is False
        else None
    )
    if reason is not None:
        raise unsupported(normalized.id, reason)
    extents = bar_extents(
        normalized.id,
        dataset,
        normalized.x,
        y_fields,
        stacked=stack not in (None, "none"),
    )
    return pick_torn_break(extents, _axis_headroom(ay), torn.at)


def pick_torn_break(
    extents: Iterable[float], headroom: float | None, at: float | None
) -> float | None:
    """The value the measure axis ends at, or None when no bar needs tearing.

    An authored ``at`` is the break whenever a bar passes it. Otherwise the
    bars are sorted longest first and the break goes after the first ``k``
    (at most a third of them) where the next bar is shortest relative to the
    last one cut; the break is then the ordinary headroom-expanded domain max
    of the bars that stay, so the plot reads as if the outliers were absent.
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
    torn_break = apply_headroom(ordered[k], headroom)
    return torn_break if torn_break < ordered[k - 1] else None


def refuse_annotations_past(
    chart_id: str, torn_break: float, y_values: Iterable[float]
) -> None:
    """Raise on an annotation past the break: the plot ends there, so it has no place."""
    past = [v for v in y_values if v > torn_break]
    if past:
        raise unsupported(
            chart_id,
            f"with an annotation at {max(past):g}, past the break at {torn_break:g}",
        )


def torn_ticks(ay: AxisYStyle, torn_break: float) -> tuple[float, ...]:
    """The measure axis ladder over ``[0, torn_break]``, nothing past the break."""
    authored = _authored_tick_ladder(ay)
    if authored is not None:
        return tuple(v for v in authored if v <= torn_break)
    target_count = target_tick_count(ay.ticks.count)
    if target_count is None:
        return ()
    return tuple(
        nice_tick_values(
            0.0, torn_break, target_count, min_step=_measure_axis_min_step(ay)
        )
    )
