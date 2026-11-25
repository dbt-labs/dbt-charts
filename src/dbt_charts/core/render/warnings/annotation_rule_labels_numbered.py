"""Detector: WARN_ANNOTATION_RULE_LABELS_NUMBERED: see the code's `doc` in
core/diagnostics/codes_render.py.

The engine replaces crowded rule labels with circled numbers and a key and
records it once per chart; this detector words it on the chart.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_ANNOTATION_RULE_LABELS_NUMBERED, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """One Diagnostic per chart whose rule labels were numbered."""
    return [
        Diagnostic.from_code(
            WARN_ANNOTATION_RULE_LABELS_NUMBERED,
            chart=chart_id,
            path=f"charts.{chart_id}.annotations",
            message=WARN_ANNOTATION_RULE_LABELS_NUMBERED.message_template.format(
                **params
            ),
            fix=WARN_ANNOTATION_RULE_LABELS_NUMBERED.fix_template,
        )
        for chart_id, params in engine_warnings(
            ctx, WARN_ANNOTATION_RULE_LABELS_NUMBERED
        )
    ]
