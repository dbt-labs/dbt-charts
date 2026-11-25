"""Detector: WARN_DIRECT_OVERLAY_X_UNSEEN: see the code's `doc` in
core/diagnostics/codes_render.py.

Resolve records each `style.key.direct.overlay_x` key that names no drawn series
(`endpoint_labels.unseen_overlay_x`); this detector reports them per chart, naming
the series the chart does draw.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedLineChart,
)
from dbt_charts.core.diagnostics import WARN_DIRECT_OVERLAY_X_UNSEEN, Diagnostic
from dbt_charts.core.diagnostics.hints import backticked_list
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """One Diagnostic per line or area chart whose `overlay_x` names an undrawn series."""
    warnings: list[Diagnostic] = []
    for chart_id, chart in ctx.board_spec.charts.items():
        if not isinstance(chart, ResolvedLineChart | ResolvedAreaChart):
            continue
        unseen = chart.style.endpoint_labels.unseen_overlay_x
        if not unseen:
            continue
        rows = ctx.chart_results[chart_id]
        drawn = (
            sorted(
                {str(row[chart.color]) for row in rows if row[chart.color] is not None}
            )
            if chart.color is not None
            else list(chart.wide_measures)
        )
        warnings.append(
            Diagnostic.from_code(
                WARN_DIRECT_OVERLAY_X_UNSEEN,
                chart=chart_id,
                message=WARN_DIRECT_OVERLAY_X_UNSEEN.message_template.format(
                    chart_id=chart_id,
                    names=backticked_list(list(unseen)),
                    drawn=backticked_list(drawn),
                ),
                fix=WARN_DIRECT_OVERLAY_X_UNSEEN.fix_template,
            )
        )
    return warnings
