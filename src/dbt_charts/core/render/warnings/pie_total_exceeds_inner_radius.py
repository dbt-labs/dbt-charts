"""Detector: WARN_PIE_TOTAL_EXCEEDS_INNER_RADIUS — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

The engine measures the formatted total against the hole the wheel actually
draws and records the overflow into ``WarningContext.pie_total_overflows``
(``render/chart/pie_total_overflow.py``). This detector is policy-only: it
reads that record, so the warning fires on exactly what rendered.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_PIE_TOTAL_EXCEEDS_INNER_RADIUS, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per donut whose center total overflows the hole."""
    return [
        Diagnostic.from_code(
            WARN_PIE_TOTAL_EXCEEDS_INNER_RADIUS,
            chart=chart_id,
            path=f"charts.{chart_id}.style.total.value.format",
            field="format",
            message=WARN_PIE_TOTAL_EXCEEDS_INNER_RADIUS.message_template.format(
                chart_id=chart_id,
                formatted_value=overflow.formatted_value,
                text_width=overflow.text_width,
                hole_diameter=overflow.hole_diameter,
                slot_width=overflow.slot_width,
                slot_height=overflow.slot_height,
            ),
            fix=WARN_PIE_TOTAL_EXCEEDS_INNER_RADIUS.fix_template,
        )
        for chart_id, overflow in ctx.pie_total_overflows.items()
    ]
