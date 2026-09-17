"""Detector: WARN_TOO_MANY_COLOR_CATEGORIES — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule:
  some mark unit's effective color encoding has type in {"nominal", "ordinal"}
  AND chart.palette is non-empty
  AND distinct color values in chart_results > len(chart.palette)

The threshold is the chart's own resolved palette length: `spatial_color_scale`
(render/chart/emitters/_cartesian.py) assigns colors via
`palette[i % len(palette)]`, so two series collide as soon as distinct values
exceed however many colors that chart's palette actually has. An empty
palette is a legal authored value, and no emit path recycles from one --
cartesian/wide/heatmap emitters skip the color scale outright, layered and pie
charts raise -- so `max_categories == 0` must not trip this detector.

The color channel is walked via `iter_mark_units`, not read off the spec's
top-level `encoding`. A plain bar or line chart's own `color` channel sits on
the mark's own `layer` entry, which the top-level read already finds fine --
the gap is specifically the endpoint-label rail (`_wrap_hconcat_label_pane` /
`_wrap_vconcat_label_rail` in `render/chart/translate.py`), which wraps the
whole chart in an independent `hconcat`/`vconcat` that the top level never
inherits into. A `text`-mark unit is excluded from the walk for the opposite
reason: that same rail's label pane (`_label_color_encoding`) hardcodes
`"type": "nominal"` on its own color channel regardless of the chart's real
color type, so trusting it would false-fire on a quantitative color ramp.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved import effective_color_field
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    WIDE_MEASURE_FAMILIES,
    raw_wide_series_names,
)
from dbt_charts.core.diagnostics import WARN_TOO_MANY_COLOR_CATEGORIES, Diagnostic
from dbt_charts.core.render.warnings.base import (
    WarningContext,
    iter_mark_units,
    unit_mark_type,
)

_CATEGORICAL_TYPES = frozenset({"nominal", "ordinal"})


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per chart with too many categorical color values."""
    warnings: list[Diagnostic] = []

    for chart_id, chart in ctx.board_spec.charts.items():
        color_field = effective_color_field(chart)
        if color_field is None:
            continue
        if chart_id not in ctx.vega_specs or chart_id not in ctx.chart_results:
            continue

        has_categorical_color = any(
            unit_mark_type(unit) != "text"
            and isinstance(color_def := encoding.get("color"), dict)
            and color_def.get("type") in _CATEGORICAL_TYPES
            for unit, encoding in iter_mark_units(ctx.vega_specs[chart_id])
        )
        if not has_categorical_color:
            continue

        rows = ctx.chart_results[chart_id]
        # The authored key the series come from — what the message names.
        authored_key, authored_field = "color", color_field
        if isinstance(chart, WIDE_MEASURE_FAMILIES) and chart.wide_measures:
            # The fold's series field exists only post-fold: count the pinned
            # domain (measures × dimension values — an all-null measure still
            # holds its palette slot), not the cells that carry a value.
            distinct = len(
                raw_wide_series_names(chart.wide_measures, chart.color, rows)
            )
            if chart.color is None:
                authored_key = authored_field = "y"
            else:
                authored_field = chart.color
        else:
            distinct = len({row[color_field] for row in rows if color_field in row})
        max_categories = len(chart.palette)
        if max_categories == 0 or distinct <= max_categories:
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
