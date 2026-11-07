"""Detector: WARN_TABLE_SCROLL_ROWS_CAPPED — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

The engine decides a `pagination.mode: scroll` table is too long to scroll and
pages it instead; this detector turns its report into the author-facing warning.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_TABLE_SCROLL_ROWS_CAPPED, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one warning per scrolling table that pages for its length."""
    return [
        Diagnostic.from_code(
            WARN_TABLE_SCROLL_ROWS_CAPPED,
            chart=chart_id,
            path=f"charts.{chart_id}.style.pagination",
            message=WARN_TABLE_SCROLL_ROWS_CAPPED.message_template.format(**params),
            fix=WARN_TABLE_SCROLL_ROWS_CAPPED.fix_template.format(**params),
        )
        for chart_id, params in engine_warnings(ctx, WARN_TABLE_SCROLL_ROWS_CAPPED)
    ]
