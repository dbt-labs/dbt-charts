"""Detector: WARN_CHART_TITLE_TRUNCATED — fires when a chart title or subtitle
was cut with an ellipsis during the spec title-overflow pass.

Detection rule: ``WarningContext.text_truncations`` contains records with
``surface == "chart_title"``.  One ``Diagnostic`` fires per (chart, authored_field)
pair so title and subtitle each get a squiggle on their own YAML line.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_CHART_TITLE_TRUNCATED, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per (chart, authored_field) whose title was truncated."""
    warnings: list[Diagnostic] = []
    for chart_id, truncations in ctx.text_truncations.items():
        # A chart is rendered once to size its slot and again to paint it, and each
        # render records its own cut.
        cuts = {
            trunc.authored_field: trunc
            for trunc in truncations
            if trunc.surface == "chart_title"
        }
        for trunc in cuts.values():
            warnings.append(
                Diagnostic.from_code(
                    WARN_CHART_TITLE_TRUNCATED,
                    chart=chart_id,
                    field=None,
                    path=f"charts.{chart_id}.{trunc.authored_field}",
                    message=WARN_CHART_TITLE_TRUNCATED.message_template.format(
                        chart_id=chart_id,
                        authored_field=trunc.authored_field,
                        authored_text=trunc.authored_text,
                    ),
                    fix=WARN_CHART_TITLE_TRUNCATED.fix_template,
                )
            )
    return warnings
