"""Detector: WARN_DUAL_AXIS_COMPETING_SCALES — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

The engine makes y independent whenever any layer pins ``axis_y.position``,
even one without a ``y`` (it is skipped when drawn). The named layer is the
first pinned layer with a ``y``, else the first layer with a ``y``; a chart
where no layer has a ``y`` draws nothing on a second scale and stays silent.
One diagnostic per chart, anchored on the first authored pin; ``related``
marks the base chart's authored y and, when it differs, the named layer's y.

Diagnostic.field is None — the issue is cross-column, not column-scoped.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved._layer import LayeredResolvedChart
from dbt_charts.core.diagnostics import (
    WARN_DUAL_AXIS_COMPETING_SCALES,
    Diagnostic,
    RelatedLocation,
)
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per layered chart with a layer on its own y scale."""
    warnings: list[Diagnostic] = []

    for chart_id, chart in ctx.board_spec.charts.items():
        if not isinstance(chart, LayeredResolvedChart) or not chart.layers:
            continue
        pinned_idx = next(
            (
                i
                for i, layer in enumerate(chart.layers)
                if layer.axis_y.position is not None
            ),
            None,
        )
        if pinned_idx is None:
            continue

        with_y = [(idx, layer) for idx, layer in enumerate(chart.layers) if layer.y]
        named = next(
            (iv for iv in with_y if iv[1].axis_y.position is not None),
            with_y[0] if with_y else None,
        )
        if named is None:
            continue
        idx, layer = named

        # Resolved y is a single column or unset; unset means the engine infers
        # it, so there is no authored column to point at.
        related: list[RelatedLocation] = []
        if chart.y is None:
            base_label = "the base chart's measure"
        else:
            base_label = repr(chart.y)
            related.append(
                RelatedLocation(
                    path=f"charts.{chart_id}.y",
                    message=f"{chart.y!r} is on the other scale",
                )
            )
        if idx != pinned_idx:
            related.append(
                RelatedLocation(
                    path=f"charts.{chart_id}.layers.{idx}.y",
                    message=f"{layer.y!r} is drawn on the independent scale",
                )
            )
        warnings.append(
            Diagnostic.from_code(
                WARN_DUAL_AXIS_COMPETING_SCALES,
                chart=chart_id,
                field=None,
                path=f"charts.{chart_id}.layers.{pinned_idx}.axis_y.position",
                related=tuple(related),
                message=WARN_DUAL_AXIS_COMPETING_SCALES.message_template.format(
                    chart_id=chart_id, layer_col=layer.y, base_col=base_label
                ),
                fix=WARN_DUAL_AXIS_COMPETING_SCALES.fix_template,
            )
        )

    return warnings
