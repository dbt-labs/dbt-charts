"""Detector: WARN_TABLE_ALIGN_NO_VISIBLE_EFFECT — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Tables are a custom SVG renderer, not Vega-Lite, so there is no spec to
inspect. Instead the renderer records a no-op left-aligned numeric column at
the exact point it compares the column's resolved width against its content
demand, into ``WarningContext.table_align_no_visible_effects`` (see
``render/chart/table_align.py``). This detector is policy-only: it reads that
captured state, one warning per offending column.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_TABLE_ALIGN_NO_VISIBLE_EFFECT, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one warning per numeric column authored align: left that paints
    identically to decimal alignment."""
    return [
        Diagnostic.from_code(
            WARN_TABLE_ALIGN_NO_VISIBLE_EFFECT,
            chart=chart_id,
            path=f"charts.{chart_id}.style.columns.{column}",
            message=WARN_TABLE_ALIGN_NO_VISIBLE_EFFECT.message_template.format(
                chart_id=chart_id, column=column
            ),
            fix=WARN_TABLE_ALIGN_NO_VISIBLE_EFFECT.fix_template,
        )
        for chart_id, columns in ctx.table_align_no_visible_effects.items()
        for column in columns
    ]
