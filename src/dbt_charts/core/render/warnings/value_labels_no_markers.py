"""Detector: WARN_VALUE_LABELS_NO_MARKERS — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

The engine places `auto` value labels on a line or area and records one that
cannot be traced to a point; this detector words it on the mark's label style.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_VALUE_LABELS_NO_MARKERS, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings
from dbt_charts.core.render.warnings.value_labels_crowd_width import painted_labels


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """One Diagnostic per line or area chart whose value labels cannot be traced to points."""
    return [
        Diagnostic.from_code(
            WARN_VALUE_LABELS_NO_MARKERS,
            chart=chart_id,
            path=f"charts.{chart_id}.style.marks.{painted_labels(ctx.board_spec.charts[chart_id], None)[0]}.labels",
            message=WARN_VALUE_LABELS_NO_MARKERS.message_template.format(**params),
            fix=WARN_VALUE_LABELS_NO_MARKERS.fix_template,
        )
        for chart_id, params in engine_warnings(ctx, WARN_VALUE_LABELS_NO_MARKERS)
    ]
