"""Detector: WARN_PATTERN_FALLBACK_FLAT — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

The engine raises it for a patterned chart that drew marks as a flat tint
because they were too small for their tile, while hue (or the axis) still
tells the series apart.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_PATTERN_FALLBACK_FLAT, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per patterned chart with marks that fell back to a tint."""
    return [
        Diagnostic.from_code(
            WARN_PATTERN_FALLBACK_FLAT,
            chart=chart_id,
            path=f"charts.{chart_id}.pattern",
            message=WARN_PATTERN_FALLBACK_FLAT.message_template.format(
                chart_id=chart_id, count=params["count"]
            ),
            fix=WARN_PATTERN_FALLBACK_FLAT.fix_template,
        )
        for chart_id, params in engine_warnings(ctx, WARN_PATTERN_FALLBACK_FLAT)
    ]
