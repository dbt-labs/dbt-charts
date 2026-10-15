"""Detector: WARN_TOO_MANY_COLOR_CATEGORIES — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule:
  the chart binds a categorical color channel (``categorical_color_encoding``
  over the type its rows infer, or nominal for a wide chart's fold) with a
  non-empty palette
  AND distinct color values in chart_results > max(len(chart.palette), 8)

The threshold is the larger of the chart's resolved palette length and a floor
of eight: a reader tells about eight categorical hues apart, so a short palette
that recycles colors across a handful of series is not by itself unreadable
(`spatial_color_scale` in render/chart/emitters/_cartesian.py assigns
`palette[i % len(palette)]`). An empty palette is a legal authored value, and no
emit path recycles from one -- cartesian/wide/heatmap emitters skip the color
scale outright, layered and pie charts raise -- so it must not trip this
detector at any series count. A pie has no Vega spec; its color is categorical
by construction.

A ``gradient`` or ``conditional`` channel, or a ``series`` channel over numeric
or temporal data, draws a continuous ramp rather than a legend, so no count of
distinct values makes it unreadable.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved import effective_color_field
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    WIDE_MEASURE_FAMILIES,
    raw_wide_series_names,
)
from dbt_charts.core.diagnostics import WARN_TOO_MANY_COLOR_CATEGORIES, Diagnostic
from dbt_charts.core.render.chart.emitters._channels import categorical_color_encoding
from dbt_charts.core.render.chart.type_inference import infer_vega_type_from_data
from dbt_charts.core.render.warnings.base import WarningContext

_READABLE_CATEGORIES = 8


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per chart with too many categorical color values."""
    warnings: list[Diagnostic] = []

    for chart_id, chart in ctx.board_spec.charts.items():
        color_field = effective_color_field(chart)
        if color_field is None:
            continue
        if chart_id not in ctx.chart_results:
            continue
        # A pie renders in the engine (no Vega spec) and its slices are
        # categorical by construction.
        rows = ctx.chart_results[chart_id]
        wide = (
            chart
            if isinstance(chart, WIDE_MEASURE_FAMILIES) and chart.wide_measures
            else None
        )
        # A wide fold's series field exists only post-fold, and is nominal.
        color_type = "nominal" if wide else infer_vega_type_from_data(rows, color_field)
        if not categorical_color_encoding(chart.resolved_channels["color"], color_type):
            continue

        # The authored key the series come from — what the message names.
        authored_key, authored_field = "color", color_field
        if wide is not None:
            # The fold's series field exists only post-fold: count the pinned
            # domain (measures × dimension values — an all-null measure still
            # holds its palette slot), not the cells that carry a value.
            distinct = len(raw_wide_series_names(wide.wide_measures, wide.color, rows))
            if wide.color is None:
                authored_key = authored_field = "y"
            else:
                authored_field = wide.color
        else:
            distinct = len({row[color_field] for row in rows if color_field in row})
        if not chart.palette:
            continue
        max_categories = max(len(chart.palette), _READABLE_CATEGORIES)
        if distinct <= max_categories:
            continue

        warnings.append(
            Diagnostic.from_code(
                WARN_TOO_MANY_COLOR_CATEGORIES,
                chart=chart_id,
                path=f"charts.{chart_id}.{authored_key}",
                field=authored_field,
                message=WARN_TOO_MANY_COLOR_CATEGORIES.message_template.format(
                    chart_id=chart_id,
                    field=authored_field,
                    count=distinct,
                    max_categories=max_categories,
                ),
                fix=WARN_TOO_MANY_COLOR_CATEGORIES.fix_template,
            )
        )

    return warnings
