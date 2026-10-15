"""Detector: WARN_QUERY_RETURNED_ZERO_ROWS — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule:
  len(ctx.chart_results[chart_id]) == 0

It gates on nothing but the rows: zero rows is meaningful for every family,
e.g. a KPI with no data is a silent failure.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_QUERY_RETURNED_ZERO_ROWS, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per chart whose query returned zero rows."""
    warnings: list[Diagnostic] = []

    for chart_id in ctx.board_spec.charts:
        # Charts absent from chart_results failed to execute — not a zero-row result.
        if chart_id not in ctx.chart_results:
            continue

        # A result truncated down to zero rows (a single row alone exceeded
        # max_result_bytes) is not the "honest empty case" this detector
        # covers — WARN_QUERY_RESULT_TRUNCATED already tells that story.
        if chart_id in ctx.chart_truncations:
            continue

        if len(ctx.chart_results[chart_id]) == 0:
            warnings.append(
                Diagnostic.from_code(
                    WARN_QUERY_RETURNED_ZERO_ROWS,
                    chart=chart_id,
                    path=f"charts.{chart_id}.query",
                    field=None,
                    message=WARN_QUERY_RETURNED_ZERO_ROWS.message_template.format(
                        chart_id=chart_id
                    ),
                    fix=WARN_QUERY_RETURNED_ZERO_ROWS.fix_template,
                )
            )

    return warnings
