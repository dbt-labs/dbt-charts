"""Detector: WARN_BAR_GROUPED_SERIES_COINCIDE — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

The engine raises it for a vertical bar whose series split a band that has no
band scale to divide (a continuous temporal x): every series paints
at the identical position and width.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_BAR_GROUPED_SERIES_COINCIDE, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per grouped bar chart whose series coincide."""
    return [
        Diagnostic.from_code(
            WARN_BAR_GROUPED_SERIES_COINCIDE,
            chart=chart_id,
            path=f"charts.{chart_id}.x",
            field=params["field"],
            message=WARN_BAR_GROUPED_SERIES_COINCIDE.message_template.format(
                chart_id=chart_id, field=params["field"]
            ),
            fix=WARN_BAR_GROUPED_SERIES_COINCIDE.fix_template,
        )
        for chart_id, params in engine_warnings(ctx, WARN_BAR_GROUPED_SERIES_COINCIDE)
    ]
