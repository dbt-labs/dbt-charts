"""Detector: WARN_DIRECT_OVERLAY_COLLIDES — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

The engine places the names of an authored `key.direct.position: overlay` and
records the ones it could not give a clear spot; this detector words it on that key.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_DIRECT_OVERLAY_COLLIDES, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """One Diagnostic per chart with series names that overlap the plot."""
    return [
        Diagnostic.from_code(
            WARN_DIRECT_OVERLAY_COLLIDES,
            chart=chart_id,
            path=f"charts.{chart_id}.style.key.direct.position",
            message=WARN_DIRECT_OVERLAY_COLLIDES.message_template.format(**params),
            fix=WARN_DIRECT_OVERLAY_COLLIDES.fix_template,
        )
        for chart_id, params in engine_warnings(ctx, WARN_DIRECT_OVERLAY_COLLIDES)
    ]
