"""Detector: WARN_TOO_MANY_X_CATEGORIES — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule:
  the chart's x scale (x_scale_type) is "nominal" or "ordinal" (or, for a
  bar chart, also "temporal" — see _BAR_ALLOWED_X_TYPES below)
  AND distinct x values in chart_results > _MAX_CATEGORIES

The temporal-bar branch emits the same code with its own message and fix
(_TEMPORAL_MESSAGE_TEMPLATE / _TEMPORAL_FIX_TEMPLATE below).
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved._base import (
    _CartesianResolvedChartFields,
)
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.diagnostics import WARN_TOO_MANY_X_CATEGORIES, Diagnostic
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.warnings.base import WarningContext
from dbt_charts.core.render.warnings.x_axis import x_scale_type

# Beyond this many categories a band axis is unreadable.
_MAX_CATEGORIES = 50

_CATEGORICAL_TYPES = frozenset({"nominal", "ordinal"})

# A bar draws one band per distinct x value regardless of whether the axis
# resolves ordinal or temporal — the density gate that flips bucketed temporal
# data off ordinal above ~60 distinct values does so purely to manage
# axis-label layout, not because the chart stopped being banded.
# Line/area/scatter charts don't get this widening: a dense temporal axis
# there is a continuous draw, not a crowded band, however many points it has.
_BAR_ALLOWED_X_TYPES = _CATEGORICAL_TYPES | frozenset({"temporal"})

# Own wording for the temporal-bar branch: only the band half of the crowding
# question holds here. A temporal axis thins its own tick labels to quarters and
# years, so the registry's collision claim is false, and its categorical fix
# ladder is unavailable to a time series (there is no "top N" month, and a table
# is not a substitute for a trend).
_TEMPORAL_MESSAGE_TEMPLATE = (
    "Chart {chart_id!r}: x field {field!r} has {count} distinct time buckets; "
    "a bar draws one band per bucket, so the bands are too thin to read "
    "(limit: {max_categories})."
)

_TEMPORAL_FIX_TEMPLATE = (
    "Widen the chart, roll the buckets up to a coarser time grain "
    "(e.g. day -> week or month), or switch to a line chart."
)


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per chart with an overcrowded categorical x-axis."""
    warnings: list[Diagnostic] = []

    for chart_id, chart in ctx.board_spec.charts.items():
        if not isinstance(chart, _CartesianResolvedChartFields):
            continue
        if chart.x is None:
            continue
        if chart_id not in ctx.chart_results:
            continue

        x_field: str = chart.x
        rows = ctx.chart_results[chart_id]
        distinct = len({row[x_field] for row in rows if x_field in row})
        if distinct <= _MAX_CATEGORIES:
            continue

        allowed_x_types = (
            _BAR_ALLOWED_X_TYPES
            if isinstance(chart, ResolvedBarChart)
            else _CATEGORICAL_TYPES
        )
        try:
            x_type = x_scale_type(chart, rows)
        except (ValueError, ChartDataError):
            # The emitter rejects this x column too; that error says why.
            continue
        if x_type not in allowed_x_types:
            continue

        temporal = x_type == "temporal"
        message_template = (
            _TEMPORAL_MESSAGE_TEMPLATE
            if temporal
            else WARN_TOO_MANY_X_CATEGORIES.message_template
        )
        warnings.append(
            Diagnostic.from_code(
                WARN_TOO_MANY_X_CATEGORIES,
                chart=chart_id,
                path=f"charts.{chart_id}.x",
                field=x_field,
                message=message_template.format(
                    chart_id=chart_id,
                    field=x_field,
                    count=distinct,
                    max_categories=_MAX_CATEGORIES,
                ),
                fix=(
                    _TEMPORAL_FIX_TEMPLATE
                    if temporal
                    else WARN_TOO_MANY_X_CATEGORIES.fix_template
                ),
            )
        )

    return warnings
