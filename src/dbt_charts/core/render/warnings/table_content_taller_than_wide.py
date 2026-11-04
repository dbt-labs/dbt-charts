"""Detector: WARN_TABLE_CONTENT_TALLER_THAN_WIDE — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

The engine measures each wrapped text cell where it wraps it and raises one
warning per column, with the worst cell's line count and how many cells tripped
it; this detector turns each into a Diagnostic at the column's `style.columns`
entry. Cell values never reach the message.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_TABLE_CONTENT_TALLER_THAN_WIDE, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per text column that wraps into towers."""
    return [
        Diagnostic.from_code(
            WARN_TABLE_CONTENT_TALLER_THAN_WIDE,
            chart=chart_id,
            path=f"charts.{chart_id}.style.columns.{params['column']}",
            message=WARN_TABLE_CONTENT_TALLER_THAN_WIDE.message_template.format(
                **params
            ),
            fix=WARN_TABLE_CONTENT_TALLER_THAN_WIDE.fix_template.format(**params),
        )
        for chart_id, params in engine_warnings(
            ctx, WARN_TABLE_CONTENT_TALLER_THAN_WIDE
        )
    ]
