"""Detector: WARN_LEGEND_HIDDEN_UNDER_EDGE -- see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule:
  a cartesian chart has ``legend.hidden_under_edge`` set -- at resolve time
  (``cartesian_series_naming()`` in ``_axes.py``), where the chart's own cascaded
  legend decides rail vs. legend: the author hid the legend under a side that
  also keeps the endpoint labels off, on a chart whose ``auto`` would have
  printed them.

The detector reads the baked fact off ``ctx.layout_charts``, the
placement-matched chart instances, since the same chart id placed under two
boards can resolve differently.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved._base import (
    _CartesianResolvedChartFields,
)
from dbt_charts.core.diagnostics import WARN_LEGEND_HIDDEN_UNDER_EDGE, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per chart that names its series nowhere."""
    return [
        Diagnostic.from_code(
            WARN_LEGEND_HIDDEN_UNDER_EDGE,
            chart=chart_id,
            path=f"charts.{chart_id}.style.legend",
            field="legend",
            message=WARN_LEGEND_HIDDEN_UNDER_EDGE.message_template.format(
                chart_id=chart_id, edge=chart.legend.hidden_under_edge
            ),
            fix=WARN_LEGEND_HIDDEN_UNDER_EDGE.fix_template,
        )
        for chart_id, chart in ctx.layout_charts.items()
        if isinstance(chart, _CartesianResolvedChartFields)
        and chart.legend.hidden_under_edge is not None
    ]
