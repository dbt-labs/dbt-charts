"""Detector: WARN_POINT_MAP_NEGATIVE_SIZE_VALUES — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

A bubble_map's `size:` measure drives mark AREA, which cannot be negative. The
engine drops rows with a negative size before drawing and reports how many;
this detector turns that report into the author-facing warning. Zero is not
negative: a zero-valued row has a legitimate area of nothing and is neither
dropped nor counted.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_POINT_MAP_NEGATIVE_SIZE_VALUES, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one warning per point_map/bubble_map chart with negative `size:` rows."""
    return [
        Diagnostic.from_code(
            WARN_POINT_MAP_NEGATIVE_SIZE_VALUES,
            chart=chart_id,
            path=f"charts.{chart_id}.size",
            field=params["size_field"],
            message=WARN_POINT_MAP_NEGATIVE_SIZE_VALUES.message_template.format(
                **params
            ),
            fix=WARN_POINT_MAP_NEGATIVE_SIZE_VALUES.fix_template.format(
                size_field=params["size_field"]
            ),
        )
        for chart_id, params in engine_warnings(
            ctx, WARN_POINT_MAP_NEGATIVE_SIZE_VALUES
        )
    ]
