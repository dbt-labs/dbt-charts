"""Detector: WARN_SUPPORT_TABLE_CELL_OVERFLOW — fires when a support_table
row's cells are wider than their category band, so neighbors collide.

Detection rule: ``WarningContext.text_truncations`` contains a record with
``surface == "support_table_cell_overflow"``. One ``Diagnostic`` fires per
chart.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_SUPPORT_TABLE_CELL_OVERFLOW, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per chart whose strip cells outgrew their band."""
    warnings: list[Diagnostic] = []
    for chart_id, truncations in ctx.text_truncations.items():
        record = next(
            (t for t in truncations if t.surface == "support_table_cell_overflow"),
            None,
        )
        if record is None:
            continue
        warnings.append(
            Diagnostic.from_code(
                WARN_SUPPORT_TABLE_CELL_OVERFLOW,
                chart=chart_id,
                path=f"charts.{chart_id}.support_table",
                message=WARN_SUPPORT_TABLE_CELL_OVERFLOW.message_template.format(
                    chart_id=chart_id, detail=record.authored_text
                ),
                fix=WARN_SUPPORT_TABLE_CELL_OVERFLOW.fix_template,
            )
        )
    return warnings
