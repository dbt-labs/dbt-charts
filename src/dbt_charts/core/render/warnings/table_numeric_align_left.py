"""Detector: WARN_TABLE_NUMERIC_ALIGN_LEFT — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Tables are a custom SVG renderer, not Vega-Lite, so there is no spec to
inspect. Instead the renderer records a ragged-width left-aligned numeric
column at the exact point it measures the number lane, into
``WarningContext.table_numeric_align_lefts`` (see
``render/chart/table_align.py``). This detector is policy-only: it reads that
captured state, one warning per offending column.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_TABLE_NUMERIC_ALIGN_LEFT, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one warning per numeric column authored align: left whose
    rendered values are not all the same width."""
    return [
        Diagnostic.from_code(
            WARN_TABLE_NUMERIC_ALIGN_LEFT,
            chart=chart_id,
            path=f"charts.{chart_id}.style.columns.{column}",
            message=WARN_TABLE_NUMERIC_ALIGN_LEFT.message_template.format(
                chart_id=chart_id, column=column
            ),
            fix=WARN_TABLE_NUMERIC_ALIGN_LEFT.fix_template,
        )
        for chart_id, columns in ctx.table_numeric_align_lefts.items()
        for column in columns
    ]
