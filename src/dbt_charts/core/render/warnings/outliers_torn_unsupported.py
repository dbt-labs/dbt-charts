"""Detector: WARN_OUTLIERS_TORN_UNSUPPORTED — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

The engine raises it for a bar that tears under `variant: torn` on a chart
shape that cannot draw an off-the-chart bar: it draws every bar plain.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_OUTLIERS_TORN_UNSUPPORTED, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per bar chart whose torn bar the shape cannot draw."""
    return [
        Diagnostic.from_code(
            WARN_OUTLIERS_TORN_UNSUPPORTED,
            chart=chart_id,
            path=f"charts.{chart_id}.style.outliers.variant",
            message=WARN_OUTLIERS_TORN_UNSUPPORTED.message_template.format(
                chart_id=chart_id
            ),
            fix=WARN_OUTLIERS_TORN_UNSUPPORTED.fix_template,
        )
        for chart_id, _ in engine_warnings(ctx, WARN_OUTLIERS_TORN_UNSUPPORTED)
    ]
