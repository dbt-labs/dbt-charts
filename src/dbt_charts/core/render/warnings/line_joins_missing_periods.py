"""Detector: WARN_LINE_JOINS_MISSING_PERIODS — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule, per series (panel x color x measure) of a line or area chart
whose x resolves to a temporal scale:
  - the distinct x values sit on a detected calendar grain, and the smallest
    step between neighbors is also most of the steps (otherwise the data is
    irregular or event-shaped and has no period to skip);
  - two neighbors that both have a y are more than one step apart.
A neighbor whose y is NULL breaks the line there, so a gap marked by a NULL row
never fires.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedLineChart,
)
from dbt_charts.core.compile.resolve.chart._wide_fields import wide_measure_fields
from dbt_charts.core.diagnostics import WARN_LINE_JOINS_MISSING_PERIODS, Diagnostic
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.time_unit_detect import (
    bucket_position,
    detect_time_unit,
)
from dbt_charts.core.render.warnings.base import WarningContext
from dbt_charts.core.render.warnings.x_axis import emitted_x_axis
from dbt_charts.core.utils import Rows

# time_unit -> (grain noun, adjective)
_GRAINS = {
    "year": ("year", "yearly"),
    "yearquarter": ("quarter", "quarterly"),
    "yearmonth": ("month", "monthly"),
    "yearweek": ("week", "weekly"),
    "yearmonthdate": ("day", "daily"),
}
# (x, y) query cells of one series.
_Point = tuple[
    Any,  # type-state: explicit_any — a query cell
    Any,  # type-state: explicit_any — a query cell
]
_SATURDAY = 5
_MAX_LISTED = 3


@dataclass(frozen=True)
class _Skips:
    label: str | None  # type-state: optional — a chart with one series
    y_field: str
    missing: int
    grain: str
    adjective: str
    first: str
    last: str


_Cell = Any  # type-state: explicit_any — a query cell


def _series_skips(
    label: str | None,  # type-state: optional — a chart with one series
    y_field: str,
    points: list[_Point],
) -> _Skips | None:  # type-state: optional — a series with no skip
    """The periods a series' line joins across, or None when it joins none or
    its x values keep no regular grain. ``points`` are ``(x, y)`` cells."""
    unit = detect_time_unit([x for x, _ in points])
    if unit is None:
        return None
    # position -> (an x cell there, whether any row there has a y)
    by_position: dict[int, tuple[_Cell, bool]] = {}
    for x, y in points:
        if (position := bucket_position(x, unit)) is not None:
            seen = position in by_position and by_position[position][1]
            by_position[position] = (x, y is not None or seen)
    positions = sorted(by_position)
    steps = [b - a for a, b in zip(positions, positions[1:], strict=False)]
    if not steps:
        return None
    step = min(steps)
    if sum(s == step for s in steps) * 2 <= len(steps) or any(s % step for s in steps):
        return None
    # A business-day series skips weekends by nature; they are not missing.
    skip_weekends = unit == "yearmonthdate" and all(
        dt.date.fromordinal(p).weekday() < _SATURDAY for p in positions
    )
    missing = sum(
        1
        for a, b in zip(positions, positions[1:], strict=False)
        if by_position[a][1] and by_position[b][1]
        for gap in range(a + step, b, step)
        if not (skip_weekends and dt.date.fromordinal(gap).weekday() >= _SATURDAY)
    )
    if not missing:
        return None
    grain, adjective = _GRAINS[unit]
    return _Skips(
        label=label,
        y_field=y_field,
        missing=missing,
        grain=grain if step == 1 else f"{step}-{grain}",
        adjective=adjective if step == 1 else f"{step}-{grain}",
        first=str(by_position[positions[0]][0])[:10],
        last=str(by_position[positions[-1]][0])[:10],
    )


def _chart_skips(
    chart: ResolvedLineChart | ResolvedAreaChart, rows: Rows
) -> list[_Skips]:
    """The skips of every series (panel x color x measure) of the chart."""
    assert chart.x is not None
    wide = wide_measure_fields(chart)
    y_fields = wide or ((chart.y,) if chart.y is not None else ())
    group_fields = [axis.field for axis in chart.panel_axes] + (
        [chart.color] if chart.color else []
    )
    groups: dict[tuple[Any, ...], Rows] = {}  # type-state: explicit_any — query cells
    for row in rows:
        groups.setdefault(tuple(row.get(f) for f in group_fields), []).append(row)
    skips: list[_Skips] = []
    for key, group in groups.items():
        for y_field in y_fields:
            parts = [str(v) for v in key if v is not None] + ([y_field] if wide else [])
            skip = _series_skips(
                " / ".join(parts) or None,
                y_field,
                [(row.get(chart.x), row.get(y_field)) for row in group],
            )
            if skip is not None:
                skips.append(skip)
    return skips


def _describe(skip: _Skips) -> str:
    text = f"{skip.missing} missing {skip.adjective} period" + (
        "" if skip.missing == 1 else "s"
    )
    return text if skip.label is None else f"{skip.label!r}: {text}"


def _stacks_series(chart: ResolvedLineChart | ResolvedAreaChart) -> bool:
    """Whether the engine stacks several series, drawing a missing row as zero
    rather than joining across it."""
    return (
        isinstance(chart, ResolvedAreaChart)
        and chart.stack not in (None, "none")
        and (chart.color is not None or bool(chart.wide_measures))
    )


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per line/area chart whose series join across gaps."""
    warnings: list[Diagnostic] = []

    for chart_id, chart in ctx.board_spec.charts.items():
        if not isinstance(chart, ResolvedLineChart | ResolvedAreaChart):
            continue
        if chart.x is None or chart.y is None and not chart.wide_measures:
            continue
        if _stacks_series(chart):
            continue
        # Orphan charts (query not in pre_executed_query_names) are absent from chart_results.
        if chart_id not in ctx.chart_results:
            continue
        rows = ctx.chart_results[chart_id]
        try:
            axis = emitted_x_axis(chart, rows)
        except (ValueError, ChartDataError):
            # The emitter rejects this x column too; that error says why.
            continue
        if axis.vl_type != "temporal":
            continue
        try:
            skips = _chart_skips(chart, axis.rows)
        except ValueError:
            # Mixed or unparseable x values: the x axis error says why.
            continue
        if not skips:
            continue

        first = skips[0]
        detail = "; ".join(map(_describe, skips[:_MAX_LISTED]))
        if len(skips) > _MAX_LISTED:
            detail += f"; {len(skips) - _MAX_LISTED} more series"
        warnings.append(
            Diagnostic.from_code(
                WARN_LINE_JOINS_MISSING_PERIODS,
                chart=chart_id,
                path=f"charts.{chart_id}.query",
                field=chart.x,
                message=WARN_LINE_JOINS_MISSING_PERIODS.message_template.format(
                    chart_id=chart_id, chart_type=chart.chart_type, detail=detail
                ),
                fix=WARN_LINE_JOINS_MISSING_PERIODS.fix_template.format(
                    chart_id=chart_id,
                    y_field=first.y_field,
                    grain=first.grain,
                    first=first.first,
                    last=first.last,
                    by_series=(
                        ", crossed with the distinct series values"
                        if first.label is not None
                        else ""
                    ),
                ),
            )
        )

    return warnings
