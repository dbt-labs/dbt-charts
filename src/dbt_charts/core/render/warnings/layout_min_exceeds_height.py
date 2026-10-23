"""Detector: WARN_LAYOUT_MIN_EXCEEDS_HEIGHT — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

The engine raises it for a horizontal bar whose category floor exceeds the
literal height an ancestor authored. The authored height comes from
``ctx.authored_chart_heights``, a snapshot of each chart's real, assigned px
slot height taken while it is still intact: after the sizing pass assigns it,
before cols-alignment can re-expand it (see ``layout_sizing.py``'s
``_snapshot_authored_slot_heights``). The warning pass hands it to the engine.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_LAYOUT_MIN_EXCEEDS_HEIGHT, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per horizontal bar whose category floor exceeds
    its nearest ancestor's authored height."""
    return [
        Diagnostic.from_code(
            WARN_LAYOUT_MIN_EXCEEDS_HEIGHT,
            chart=chart_id,
            message=WARN_LAYOUT_MIN_EXCEEDS_HEIGHT.message_template.format(**params),
            fix=WARN_LAYOUT_MIN_EXCEEDS_HEIGHT.fix_template,
        )
        for chart_id, params in engine_warnings(ctx, WARN_LAYOUT_MIN_EXCEEDS_HEIGHT)
    ]
