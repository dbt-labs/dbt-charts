"""Detector: WARN_CHART_HEIGHT_BELOW_CATEGORY_FLOOR — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

The engine raises it for a horizontal bar whose category floor exceeds the
cap the chart itself authored (its own `height`, else its own `style.max_height`);
a theme or board default never reaches it.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import (
    WARN_CHART_HEIGHT_BELOW_CATEGORY_FLOOR,
    Diagnostic,
)
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per horizontal bar whose category floor exceeds
    a cap its author wrote on the chart."""
    return [
        Diagnostic.from_code(
            WARN_CHART_HEIGHT_BELOW_CATEGORY_FLOOR,
            chart=chart_id,
            message=WARN_CHART_HEIGHT_BELOW_CATEGORY_FLOOR.message_template.format(
                **params
            ),
            fix=WARN_CHART_HEIGHT_BELOW_CATEGORY_FLOOR.fix_template,
        )
        for chart_id, params in engine_warnings(
            ctx, WARN_CHART_HEIGHT_BELOW_CATEGORY_FLOOR
        )
    ]
