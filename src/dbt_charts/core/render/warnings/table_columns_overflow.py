"""Detector: WARN_TABLE_COLUMNS_OVERFLOW — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Tables are a custom SVG renderer, not Vega-Lite, so there is no spec to inspect.
Instead the renderer records the overflow at the exact point it decides the
columns don't fit into ``WarningContext.table_overflows`` (see
``render/chart/table_overflow.py``). This detector is policy-only: it reads that
captured state, so the warning fires on exactly what rendered — no second width
computation to drift from the renderer's, and no magnitude threshold. The
renderer's own clip boundary is the line: the table either fits its slot or it
doesn't.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_TABLE_COLUMNS_OVERFLOW, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one warning per table that could not fit the width it was given."""
    warnings: list[Diagnostic] = []

    for chart_id, overflow in ctx.table_overflows.items():
        warnings.append(
            Diagnostic.from_code(
                WARN_TABLE_COLUMNS_OVERFLOW,
                chart=chart_id,
                message=WARN_TABLE_COLUMNS_OVERFLOW.message_template.format(
                    chart_id=chart_id,
                    needed_width=overflow.required_width,
                    available_width=overflow.available_width,
                ),
                fix=WARN_TABLE_COLUMNS_OVERFLOW.fix_template,
            )
        )

    return warnings
