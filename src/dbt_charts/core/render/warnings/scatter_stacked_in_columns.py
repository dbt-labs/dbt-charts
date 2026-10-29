"""Detector: WARN_SCATTER_STACKED_IN_COLUMNS — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule:
  chart.chart_type == "scatter"
  AND a channel (x or y) holds numeric values with at most _MAX_DISTINCT
      distinct values
  AND the channel averages at least _MIN_POINTS_PER_VALUE points per value
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved.scatter import ResolvedScatterChart
from dbt_charts.core.diagnostics import WARN_SCATTER_STACKED_IN_COLUMNS, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext

_MAX_DISTINCT = 5
# Fewer points per value than this reads as a few deliberate points, not a column.
_MIN_POINTS_PER_VALUE = 4


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per scatter channel whose points stack in columns."""
    warnings: list[Diagnostic] = []

    for chart_id, chart in ctx.board_spec.charts.items():
        if not isinstance(chart, ResolvedScatterChart):
            continue
        if chart_id not in ctx.chart_results:
            continue
        for channel in ("x", "y"):
            field = getattr(chart, channel)
            if field is None:
                continue
            values = [
                row[field]
                for row in ctx.chart_results[chart_id]
                if isinstance(row.get(field), int | float)
                and not isinstance(row[field], bool)
            ]
            distinct = len(set(values))
            if not 0 < distinct <= _MAX_DISTINCT:
                continue
            if len(values) < _MIN_POINTS_PER_VALUE * distinct:
                continue
            warnings.append(
                Diagnostic.from_code(
                    WARN_SCATTER_STACKED_IN_COLUMNS,
                    chart=chart_id,
                    path=f"charts.{chart_id}.{channel}",
                    field=field,
                    message=WARN_SCATTER_STACKED_IN_COLUMNS.message_template.format(
                        channel=channel, distinct=distinct, points=len(values)
                    ),
                    fix=WARN_SCATTER_STACKED_IN_COLUMNS.fix_template,
                )
            )

    return warnings
