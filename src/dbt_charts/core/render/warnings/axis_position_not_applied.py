"""Detector: WARN_AXIS_POSITION_NOT_APPLIED — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule: a ``ResolvedBarChart`` whose resolve dropped a board or theme
axis edge that only fits vertical charts (``unapplied_axis_edges``).
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.diagnostics import WARN_AXIS_POSITION_NOT_APPLIED, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext

_INSTEAD = {"axis_x": "axis_y", "axis_y": "axis_x"}


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per dropped edge on a horizontal bar."""
    return [
        Diagnostic.from_code(
            WARN_AXIS_POSITION_NOT_APPLIED,
            chart=chart_id,
            field=f"{axis}.position",
            message=WARN_AXIS_POSITION_NOT_APPLIED.message_template.format(
                chart_id=chart_id,
                key=f"{axis}.position: {position}",
                instead=f"{_INSTEAD[axis]}.position: {position}",
            ),
            fix=WARN_AXIS_POSITION_NOT_APPLIED.fix_template,
        )
        for chart_id, chart in ctx.layout_charts.items()
        if isinstance(chart, ResolvedBarChart)
        for axis, position in chart.unapplied_axis_edges
    ]
