"""Detector: WARN_LEGEND_OVERLAY_COVERS_MARKS — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

The engine measures the drawn legend box against the plotted marks; this
detector anchors the report on the legend's position.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_LEGEND_OVERLAY_COVERS_MARKS, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per chart whose overlay legend sits on its marks."""
    return [
        Diagnostic.from_code(
            WARN_LEGEND_OVERLAY_COVERS_MARKS,
            chart=chart_id,
            path=f"charts.{chart_id}.style.legend.position",
            message=WARN_LEGEND_OVERLAY_COVERS_MARKS.message_template.format(
                chart_id=chart_id
            ),
            fix=WARN_LEGEND_OVERLAY_COVERS_MARKS.fix_template,
        )
        for chart_id, _ in engine_warnings(ctx, WARN_LEGEND_OVERLAY_COVERS_MARKS)
    ]
