"""Detector: WARN_TORN_OUTLIER_NO_OUTLIER — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule: a ``ResolvedBarChart`` whose ``style.torn_outlier.enabled`` is
on but whose resolve baked no ``torn_break``.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.diagnostics import WARN_TORN_OUTLIER_NO_OUTLIER, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per bar chart that opted in and has nothing to tear."""
    return [
        Diagnostic.from_code(
            WARN_TORN_OUTLIER_NO_OUTLIER,
            chart=chart_id,
            message=WARN_TORN_OUTLIER_NO_OUTLIER.message_template.format(
                chart_id=chart_id
            ),
            fix=WARN_TORN_OUTLIER_NO_OUTLIER.fix_template,
        )
        for chart_id, chart in ctx.layout_charts.items()
        if isinstance(chart, ResolvedBarChart)
        and chart.style.torn_outlier.enabled
        and chart.torn_break is None
    ]
