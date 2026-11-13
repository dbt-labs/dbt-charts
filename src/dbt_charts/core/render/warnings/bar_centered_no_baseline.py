"""Detector: WARN_BAR_CENTERED_NO_BASELINE — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Reads only the resolved chart, so it runs for every bar whose stack resolved to
`center`, whether or not its query returned rows.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.diagnostics import WARN_BAR_CENTERED_NO_BASELINE, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per centered bar chart."""
    code = WARN_BAR_CENTERED_NO_BASELINE
    return [
        Diagnostic.from_code(
            code,
            chart=chart_id,
            path=f"charts.{chart_id}.style.stack",
            message=code.message_template,
            fix=code.fix_template,
        )
        for chart_id, chart in ctx.board_spec.charts.items()
        if isinstance(chart, ResolvedBarChart) and chart.stack == "center"
    ]
