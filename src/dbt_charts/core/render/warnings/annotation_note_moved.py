"""Detector: WARN_ANNOTATION_NOTE_MOVED: see the code's `doc` in
core/diagnostics/codes_render.py.

The engine places annotation text and records a note whose `auto` text left
its point's side with no connector; this detector words it on the annotation.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_ANNOTATION_NOTE_MOVED, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """One Diagnostic per note whose text moved off its point's side."""
    return [
        Diagnostic.from_code(
            WARN_ANNOTATION_NOTE_MOVED,
            chart=chart_id,
            path=f"charts.{chart_id}.annotations[{params['index']}]",
            message=WARN_ANNOTATION_NOTE_MOVED.message_template.format(**params),
            fix=WARN_ANNOTATION_NOTE_MOVED.fix_template,
        )
        for chart_id, params in engine_warnings(ctx, WARN_ANNOTATION_NOTE_MOVED)
    ]
