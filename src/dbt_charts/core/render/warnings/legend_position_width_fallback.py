"""Detector: WARN_LEGEND_POSITION_WIDTH_FALLBACK — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule:
  a cartesian family (bar, line, area, scatter, heatmap) has
  chart.legend.position_overridden_by_width set — at resolve time
  (``cartesian_series_naming()`` in ``_axes.py``), when the card's width forced
  the legend off the edge the author wrote — or a pie's sized mode draws a
  companion table whose attachment carries an ``overridden_edge``, the edge the
  author wrote and the card was too narrow to hold.

The detector itself does no width math: the width tier decision already
happened at resolve, in the same function that has both the authored patch
and the width in scope. This is a placement-sensitive fact — a chart id
placed at two different widths can be tiny in one and not the other — so
this walks ``ctx.layout_charts`` (each chart's real, placement-matched
instance) rather than ``ctx.board_spec.charts`` (the catalog, which can hold
a different placement's resolution for the same id). See
``pie_total_exceeds_inner_radius.py`` for the same width-accuracy reasoning.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved import ResolvedPieChart
from dbt_charts.core.compile.models.chart.resolved._base import (
    _CartesianResolvedChartFields,
)
from dbt_charts.core.diagnostics import WARN_LEGEND_POSITION_WIDTH_FALLBACK, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per chart whose width forced its legend off the authored edge."""
    warnings: list[Diagnostic] = []

    for chart_id, chart in ctx.layout_charts.items():
        if isinstance(chart, ResolvedPieChart):
            # The width moves a pie's table, so only the layout the size stage
            # sized it to can have moved off the authored edge. Sizing records a
            # mode for every placed pie (one it cannot lay out is an error tile,
            # not a layout chart), so a missing one is a sizing bug.
            mode = ctx.pie_modes.get(chart_id)
            if mode is None:
                raise ValueError(
                    f"pie {chart_id!r} reached the warning detectors without its sized mode"
                )
            attachment = chart.attachment_in(mode)
            if attachment is None or attachment.overridden_edge is None:
                continue
            authored_edge = attachment.overridden_edge
            resolved_edge = attachment.placement
        elif isinstance(chart, _CartesianResolvedChartFields):
            if chart.legend.position_overridden_by_width is None:
                continue
            authored_edge = chart.legend.position_overridden_by_width
            resolved_edge = chart.legend.position.edge
        else:
            continue
        warnings.append(
            Diagnostic.from_code(
                WARN_LEGEND_POSITION_WIDTH_FALLBACK,
                chart=chart_id,
                path=f"charts.{chart_id}.style.legend.position.edge",
                field="legend",
                message=WARN_LEGEND_POSITION_WIDTH_FALLBACK.message_template.format(
                    chart_id=chart_id,
                    authored_edge=authored_edge,
                    resolved_edge=resolved_edge,
                ),
                fix=WARN_LEGEND_POSITION_WIDTH_FALLBACK.fix_template,
            )
        )

    return warnings
