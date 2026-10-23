"""Detector: WARN_AXIS_LABEL_COLLISION — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Policy-only, mirroring ``table_cramped.py``: the fact is captured at render
time (``render/chart/axis_label_collision.py``, from the engine's own skip/tilt
pick) — this
detector only reads what was recorded, it does not re-measure anything.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_AXIS_LABEL_COLLISION, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per chart whose x-axis labels never fit."""
    warnings: list[Diagnostic] = []

    for chart_id, collision in ctx.axis_label_collisions.items():
        warnings.append(
            Diagnostic.from_code(
                WARN_AXIS_LABEL_COLLISION,
                chart=chart_id,
                path=f"charts.{chart_id}.x",
                field=collision.field,
                message=WARN_AXIS_LABEL_COLLISION.message_template.format(
                    chart_id=chart_id,
                    field=collision.field,
                    label_count=collision.label_count,
                ),
                fix=WARN_AXIS_LABEL_COLLISION.fix_template,
            )
        )

    return warnings
