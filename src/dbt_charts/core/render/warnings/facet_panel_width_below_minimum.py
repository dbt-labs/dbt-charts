"""Detector: WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule:
  chart has ``multiples`` set AND compiles to a faceted Vega-Lite spec
  (``"facet"`` present in ``vega_specs[chart_id]``) AND the inner unit
  spec's ``width`` — the same per-panel width ``facet_panel_width()``
  computes at resolve and render — is below
  ``FACET_MIN_PANEL_PX``.

``facet_panel_width()`` never floors panels back up to that minimum (the
card boundary always wins — see its docstring): a high column-facet
cardinality against a narrow card silently produces sub-floor panels rather
than push painted content past the card's edge. This is the author's only
signal that happened.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved._base import (
    _CartesianResolvedChartFields,
)
from dbt_charts.core.compile.resolve.chart.adaptive_stroke import (
    panel_axis_cardinality,
)
from dbt_charts.core.constants.layout import FACET_MIN_PANEL_PX
from dbt_charts.core.diagnostics import WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per faceted chart whose panel width undercuts the floor."""
    warnings: list[Diagnostic] = []
    for chart_id, chart in ctx.board_spec.charts.items():
        if not isinstance(chart, _CartesianResolvedChartFields):
            continue
        if chart.multiples is None:
            continue
        if chart_id not in ctx.vega_specs:
            continue

        spec = ctx.vega_specs[chart_id]
        if "facet" not in spec:
            continue
        unit = spec["spec"]
        panel_width = unit.get("width")
        if not isinstance(panel_width, int | float):
            continue
        if panel_width >= FACET_MIN_PANEL_PX:
            continue

        panel_cols = panel_axis_cardinality(chart.panel_axes, chart.multiples.columns)

        warnings.append(
            Diagnostic.from_code(
                WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM,
                chart=chart_id,
                path=f"charts.{chart_id}.multiples",
                message=WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM.message_template.format(
                    chart_id=chart_id,
                    panel_cols=panel_cols,
                    panel_width=panel_width,
                    min_panel_px=FACET_MIN_PANEL_PX,
                ),
                fix=WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM.fix_template,
            )
        )

    return warnings
