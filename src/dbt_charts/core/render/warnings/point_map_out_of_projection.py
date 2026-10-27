"""Detector: WARN_POINT_MAP_OUT_OF_PROJECTION — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

albersUsa covers CONUS, Alaska and Hawaii; d3-geo places coordinates outside
them at an undefined position, so the engine drops those rows before drawing
and reports how many. This detector turns that report into the author-facing
warning.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_POINT_MAP_OUT_OF_PROJECTION, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one warning per point_map chart with out-of-projection points."""
    return [
        Diagnostic.from_code(
            WARN_POINT_MAP_OUT_OF_PROJECTION,
            chart=chart_id,
            path=f"charts.{chart_id}.projection",
            message=WARN_POINT_MAP_OUT_OF_PROJECTION.message_template.format(**params),
            fix=WARN_POINT_MAP_OUT_OF_PROJECTION.fix_template,
        )
        for chart_id, params in engine_warnings(ctx, WARN_POINT_MAP_OUT_OF_PROJECTION)
    ]
