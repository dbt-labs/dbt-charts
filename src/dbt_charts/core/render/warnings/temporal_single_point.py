"""Detector: WARN_TEMPORAL_SINGLE_POINT — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule:
  chart.chart_type in {"line", "area"}
  AND the chart's x axis resolves to a temporal scale over its rows
  AND len(ctx.chart_results[chart_id]) == 1
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedLineChart,
)
from dbt_charts.core.diagnostics import WARN_TEMPORAL_SINGLE_POINT, Diagnostic
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.warnings.base import WarningContext
from dbt_charts.core.render.warnings.x_axis import emitted_x_axis


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per line/area chart with a single temporal data point."""
    warnings: list[Diagnostic] = []

    for chart_id, chart in ctx.board_spec.charts.items():
        if not isinstance(chart, ResolvedLineChart | ResolvedAreaChart):
            continue
        if chart.x is None:
            continue
        # Orphan charts (query not in pre_executed_query_names) are absent from chart_results.
        if chart_id not in ctx.chart_results:
            continue
        rows = ctx.chart_results[chart_id]
        if len(rows) != 1:
            continue
        try:
            axis = emitted_x_axis(chart, rows)
        except (ValueError, ChartDataError):
            # The emitter rejects this x column too; that error says why.
            continue
        if axis.vl_type != "temporal":
            continue

        warnings.append(
            Diagnostic.from_code(
                WARN_TEMPORAL_SINGLE_POINT,
                chart=chart_id,
                path=f"charts.{chart_id}.x",
                field=chart.x,
                message=WARN_TEMPORAL_SINGLE_POINT.message_template.format(
                    chart_id=chart_id, chart_type=chart.chart_type
                ),
                fix=WARN_TEMPORAL_SINGLE_POINT.fix_template,
            )
        )

    return warnings
