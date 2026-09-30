"""Detector: WARN_TABLE_PAGE_SQUEEZED — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Tables are a custom SVG renderer, not Vega-Lite, so there is no spec to
inspect. Instead the renderer records the squeeze at the exact point the slot
overrides the table's own page size, into
``WarningContext.table_page_squeezes`` (see
``render/chart/table_page_squeeze.py``). This detector is policy-only: it reads
that captured state, so the warning fires on exactly what rendered — no second
row-fitting computation to drift from the paginator's.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_TABLE_PAGE_SQUEEZED, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one warning per table whose slot cut its page down."""
    return [
        Diagnostic.from_code(
            WARN_TABLE_PAGE_SQUEEZED,
            chart=chart_id,
            message=WARN_TABLE_PAGE_SQUEEZED.message_template.format(
                chart_id=chart_id,
                drawn_rows=squeeze.drawn_rows,
                page_rows=squeeze.page_rows,
                total_rows=squeeze.total_rows,
            ),
            fix=WARN_TABLE_PAGE_SQUEEZED.fix_template.format(
                drawn_rows=squeeze.drawn_rows
            ),
        )
        for chart_id, squeeze in ctx.table_page_squeezes.items()
    ]
