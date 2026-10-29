"""Detector: WARN_BAR_CATEGORY_TICKS — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule:
  the chart is a bar chart whose x scale (x_scale_type) is "nominal" or
  "ordinal" (a category axis; horizontal bars and histograms have none)
  AND the resolved axis_x.ticks.visible is true
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved import ResolvedBarChart
from dbt_charts.core.diagnostics import WARN_BAR_CATEGORY_TICKS, Diagnostic
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.warnings.base import WarningContext
from dbt_charts.core.render.warnings.x_axis import x_scale_type

_CATEGORICAL_TYPES = frozenset({"nominal", "ordinal"})


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per bar chart drawing ticks on a category axis."""
    warnings: list[Diagnostic] = []
    for chart_id, chart in ctx.board_spec.charts.items():
        if not isinstance(chart, ResolvedBarChart):
            continue
        if not chart.style.axis_x.ticks.visible:
            continue
        if chart_id not in ctx.chart_results:
            continue
        try:
            x_type = x_scale_type(chart, ctx.chart_results[chart_id])
        except (ValueError, ChartDataError):
            # The emitter rejects this x column too; that error says why.
            continue
        if x_type not in _CATEGORICAL_TYPES:
            continue
        warnings.append(
            Diagnostic.from_code(
                WARN_BAR_CATEGORY_TICKS,
                chart=chart_id,
                path=f"charts.{chart_id}.style.axis_x.ticks.visible",
                message=WARN_BAR_CATEGORY_TICKS.message_template,
                fix=WARN_BAR_CATEGORY_TICKS.fix_template,
            )
        )
    return warnings
