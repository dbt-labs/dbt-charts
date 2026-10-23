"""Detector: WARN_BAR_BAND_WIDTH_TOO_NARROW — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

The engine raises it in two shapes. Over a band scale (a nominal or ordinal
category axis), the per-bar pixel width, the panel's extent over distinct
categories and series, falls under a readability floor: the theme's default bar
border stroke is 1px and the fill occupies most of each band, so below the floor
the borders consume the band and the fill reads as a hairline or vanishes. Over
a numeric x (no band), the bar is wider than the closest two x values are apart
and bars overlap their neighbors; that is a collision rather than an unreadable
fill, so it carries its own wording and the engine marks it ``continuous``, with
the closest-x step in ``min_band_width``.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.diagnostics import WARN_BAR_BAND_WIDTH_TOO_NARROW, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings

CONTINUOUS_MESSAGE_TEMPLATE = (
    "Chart {chart_id!r} draws {distinct} bars ~{bar_width:.2f}px wide across "
    "{render_width:.0f}px, but the closest two x values are only "
    "~{step_px:.2f}px apart — bars will overlap their neighbors."
)


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per bar chart whose bars are too narrow for their
    category spacing."""
    warnings: list[Diagnostic] = []
    for chart_id, params in engine_warnings(ctx, WARN_BAR_BAND_WIDTH_TOO_NARROW):
        template = (
            CONTINUOUS_MESSAGE_TEMPLATE.format(
                step_px=params["min_band_width"], **params
            )
            if params.get("continuous")
            else WARN_BAR_BAND_WIDTH_TOO_NARROW.message_template.format(**params)
        )
        chart = ctx.board_spec.charts[chart_id]
        assert isinstance(chart, ResolvedBarChart)
        field = chart.x
        warnings.append(
            Diagnostic.from_code(
                WARN_BAR_BAND_WIDTH_TOO_NARROW,
                chart=chart_id,
                path=f"charts.{chart_id}.x",
                field=field,
                message=template,
                fix=WARN_BAR_BAND_WIDTH_TOO_NARROW.fix_template,
            )
        )
    return warnings
