"""Detector: WARN_VALUE_LABELS_THINNED — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

The engine places `auto` value labels and records how many it left undrawn; this
detector words it on the chart's label style.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved import ResolvedScatterChart
from dbt_charts.core.diagnostics import WARN_VALUE_LABELS_THINNED, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings
from dbt_charts.core.render.warnings.value_labels_crowd_width import painted_labels


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """One Diagnostic per chart whose auto value labels were thinned."""
    warnings: list[Diagnostic] = []
    for chart_id, params in engine_warnings(ctx, WARN_VALUE_LABELS_THINNED):
        chart = ctx.board_spec.charts[chart_id]
        mark_key = (
            "point"
            if isinstance(chart, ResolvedScatterChart)
            else painted_labels(chart, None)[0]
        )
        warnings.append(
            Diagnostic.from_code(
                WARN_VALUE_LABELS_THINNED,
                chart=chart_id,
                path=f"charts.{chart_id}.style.marks.{mark_key}.labels",
                message=WARN_VALUE_LABELS_THINNED.message_template.format(**params),
                fix=WARN_VALUE_LABELS_THINNED.fix_template,
            )
        )
    return warnings
