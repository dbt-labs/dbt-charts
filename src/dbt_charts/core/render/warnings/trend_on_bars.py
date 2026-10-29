"""Detector: WARN_TREND_ON_BARS: see the code's `doc` in
core/diagnostics/codes_render.py.

Resolve records each trend that reads a bar mark (`annotation_bar_trends`);
this detector reports each.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedBarChart,
    ResolvedLineChart,
    ResolvedScatterChart,
)
from dbt_charts.core.diagnostics import WARN_TREND_ON_BARS, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """One Diagnostic per trend annotation that fits a bar mark."""
    return [
        Diagnostic.from_code(
            WARN_TREND_ON_BARS,
            chart=chart_id,
            message=WARN_TREND_ON_BARS.message_template.format(
                chart_id=chart_id, index=index
            ),
            fix=WARN_TREND_ON_BARS.fix_template,
        )
        for chart_id, chart in ctx.board_spec.charts.items()
        if isinstance(
            chart,
            ResolvedLineChart
            | ResolvedAreaChart
            | ResolvedBarChart
            | ResolvedScatterChart,
        )
        for index in chart.annotation_bar_trends
    ]
