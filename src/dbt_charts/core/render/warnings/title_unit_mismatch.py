"""Detector: WARN_TITLE_UNIT_MISMATCH — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

The engine reads the unit off the painted value-axis ticks and compares it with
the chart's title and subtitle; this detector words what it raised.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_TITLE_UNIT_MISMATCH, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """One Diagnostic per value-axis unit the title or subtitle contradicts."""
    return [
        Diagnostic.from_code(
            WARN_TITLE_UNIT_MISMATCH,
            chart=chart_id,
            path=f"charts.{chart_id}.title",
            message=WARN_TITLE_UNIT_MISMATCH.message_template.format(**params),
            fix=WARN_TITLE_UNIT_MISMATCH.fix_template,
        )
        for chart_id, params in engine_warnings(ctx, WARN_TITLE_UNIT_MISMATCH)
    ]
