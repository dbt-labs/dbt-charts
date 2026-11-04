"""Detector: WARN_TABLE_HEADER_EXTRA_WRAP — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

The engine measures how many lines a header needs where it lays the header out
and raises one warning per column; this detector turns each into a Diagnostic
at the column's `style.columns` entry.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_TABLE_HEADER_EXTRA_WRAP, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per column whose header needs over two lines."""
    return [
        Diagnostic.from_code(
            WARN_TABLE_HEADER_EXTRA_WRAP,
            chart=chart_id,
            path=f"charts.{chart_id}.style.columns.{params['column']}",
            message=WARN_TABLE_HEADER_EXTRA_WRAP.message_template.format(**params),
            fix=WARN_TABLE_HEADER_EXTRA_WRAP.fix_template.format(**params),
        )
        for chart_id, params in engine_warnings(ctx, WARN_TABLE_HEADER_EXTRA_WRAP)
    ]
