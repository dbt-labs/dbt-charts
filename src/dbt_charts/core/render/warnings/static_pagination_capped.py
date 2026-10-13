"""Detector: WARN_STATIC_PAGINATION_CAPPED — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Tables are a custom SVG renderer, not Vega-Lite, so there is no spec to
inspect. Instead the renderer records how it paginated each table into
``WarningContext.static_paginations`` (see
``render/chart/table_static_pagination.py``), including whether the note that
states the cap fit in the slot. This detector is policy-only: it reads that
captured state, so the warning fires on exactly what rendered — no second
page-count computation to drift from the renderer's.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_STATIC_PAGINATION_CAPPED, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one warning per capped table whose file does not state the cap."""
    return [
        Diagnostic.from_code(
            WARN_STATIC_PAGINATION_CAPPED,
            chart=chart_id,
            message=WARN_STATIC_PAGINATION_CAPPED.message_template.format(
                chart_id=chart_id,
                page_rows=pagination.page_rows,
                total_rows=pagination.total_rows,
                rendered_pages=pagination.rendered_pages,
                total_pages=pagination.total_pages,
            ),
            fix=WARN_STATIC_PAGINATION_CAPPED.fix_template,
        )
        for chart_id, pagination in ctx.static_paginations.items()
        if pagination.cap_note_dropped
    ]
