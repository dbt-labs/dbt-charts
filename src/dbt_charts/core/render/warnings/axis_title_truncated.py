"""Detector: WARN_AXIS_TITLE_TRUNCATED — see its ``doc`` in
``core/diagnostics/codes_render.py`` for what this fires on.

Detection rule:
  ``WarningContext.text_truncations`` contains records with
  ``surface == "axis_title"`` — the single wrap site
  (the engine's axis-title wrap) recorded that at least one
  axis title was cut with an ellipsis.

One ``Diagnostic`` fires per (chart, authored_field) pair.  The path points at
the authored YAML field ("x_label" or "y_label") so the editor squiggle lands on
the right line — horizontal bars swap VL channels but the record always carries
the authored field name.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_AXIS_TITLE_TRUNCATED, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per (chart, authored_field) whose axis title was truncated."""
    warnings: list[Diagnostic] = []
    for chart_id, truncations in ctx.text_truncations.items():
        for trunc in truncations:
            if trunc.surface != "axis_title":
                continue
            warnings.append(
                Diagnostic.from_code(
                    WARN_AXIS_TITLE_TRUNCATED,
                    chart=chart_id,
                    field=None,
                    path=f"charts.{chart_id}.{trunc.authored_field}",
                    message=WARN_AXIS_TITLE_TRUNCATED.message_template.format(
                        chart_id=chart_id,
                        authored_field=trunc.authored_field,
                        authored_text=trunc.authored_text,
                    ),
                    fix=WARN_AXIS_TITLE_TRUNCATED.fix_template,
                )
            )
    return warnings
