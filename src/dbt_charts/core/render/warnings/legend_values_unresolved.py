"""Detector: WARN_LEGEND_VALUES_UNRESOLVED -- see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Recomputes the legend domain from the resolved chart and query rows,
independently of what the emitters already built, so a bug in one
chart's domain build can't suppress every other chart's warning.

An unresolved entry is always dropped, never a hard failure, so this
detector is the only signal an author gets. It must cover every family
that can drop an entry: a plain `color:` chart, a wide `y: [...]`
chart, a layered chart's shared color scale, and a geoshape
choropleth.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.resolved._layer import LayeredResolvedChart
from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.models.chart.resolved.geoshape import ResolvedGeoshapeChart
from dbt_charts.core.compile.models.chart.resolved.heatmap import ResolvedHeatmapChart
from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
from dbt_charts.core.compile.models.chart.resolved.pie import ResolvedPieChart
from dbt_charts.core.compile.models.chart.resolved.scatter import ResolvedScatterChart
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    wide_legend_aliases,
    wide_measure_fields,
    wide_measure_labels_for,
    wide_series_names,
)
from dbt_charts.core.diagnostics import WARN_LEGEND_VALUES_UNRESOLVED, Diagnostic
from dbt_charts.core.render.chart.emitters._cartesian import distinct_series_values
from dbt_charts.core.render.chart.emitters._channels import (
    categorical_color_encoding,
    layer_label_and_aliases,
    resolve_legend_entries,
)
from dbt_charts.core.render.chart.type_inference import infer_vega_type_from_data
from dbt_charts.core.render.utils import normalize_data_types
from dbt_charts.core.render.warnings.base import WarningContext

_CARTESIAN_WIDE_FAMILIES = (ResolvedBarChart, ResolvedAreaChart, ResolvedLineChart)
_CHANNEL_COLOR_FAMILIES = (
    *_CARTESIAN_WIDE_FAMILIES,
    ResolvedScatterChart,
    ResolvedHeatmapChart,
    ResolvedPieChart,
)
_CheckedChart = (
    ResolvedBarChart
    | ResolvedAreaChart
    | ResolvedLineChart
    | ResolvedScatterChart
    | ResolvedHeatmapChart
    | ResolvedPieChart
    | ResolvedGeoshapeChart
)


def _layer_rows(
    ctx: WarningContext,
    chart_id: str,
    layer_query_name: str | None,
    base_rows: list[dict[str, Any]],  # type-state: explicit_any — query rows
) -> list[dict[str, Any]]:  # type-state: explicit_any — query rows
    """This layer's own rows, or the base's when its query didn't diverge."""
    if layer_query_name is None:
        return base_rows
    chart_layer_results = ctx.layer_results.get(
        chart_id, {}
    )  # type-state: silent_fallback — sparse map, no per-chart entry is a legal "no diverging layer" state
    return chart_layer_results.get(
        layer_query_name, base_rows
    )  # type-state: silent_fallback — this layer's own query never diverged from the base's, so its rows are the base's


def _layered_domain_and_aliases(
    ctx: WarningContext,
    chart_id: str,
    chart: LayeredResolvedChart,
    base_domain: list[str],
    base_aliases: dict[str, frozenset[str]],
    base_rows: list[dict[str, Any]],  # type-state: explicit_any — query rows
    base_y_is_quantitative: bool,
) -> tuple[list[str], dict[str, frozenset[str]]]:
    """Domain + alias map for an overlay's shared scale.

    Seeds with the base's own contribution, then adds each layer: a
    layer with its own nominal/ordinal `color:` contributes its distinct
    values; a colorless layer contributes its label, aliased by its
    y-column name. Must mirror the emitter's shared-scale gate exactly --
    ``base_y_is_quantitative`` skips both contributions the same way the
    emitter does when the base's y isn't quantitative, or the domain here
    would resolve an entry the emitter actually dropped.
    """
    domain = list(base_domain)
    aliases = dict(base_aliases)
    if not base_y_is_quantitative:
        return domain, aliases
    for layer in chart.layers:
        if layer.y is None or not layer.legend:
            continue
        rows = _layer_rows(ctx, chart_id, layer.query_name, base_rows)
        layer_color_type = (
            infer_vega_type_from_data(rows, layer.color)
            if layer.color is not None
            else None
        )
        if layer.color is not None and layer_color_type in ("nominal", "ordinal"):
            for value in distinct_series_values(rows, layer.color):
                if value not in domain:
                    domain.append(value)
            continue
        # Pure config-to-config -- shared with _overlay.py's identical
        # layer-label rule via layer_label_and_aliases. Only the domain
        # membership check above (layer.color's distinct values) reads
        # real rows and stays independent.
        label, layer_aliases = layer_label_and_aliases(
            layer.label, layer.y, chart.field_labels
        )
        if label not in domain:
            domain.append(label)
        aliases[label] = layer_aliases
    return domain, aliases


def _base_y_is_quantitative(
    chart: ResolvedBarChart
    | ResolvedAreaChart
    | ResolvedLineChart
    | ResolvedScatterChart,
    rows: list[dict[str, Any]],  # type-state: explicit_any — query rows
) -> bool:
    """Whether the base's y-channel is quantitative.

    Bar/area/line's y is always a numeric measure, so this returns True
    for them directly rather than re-inferring from data -- a zero-row
    or numeric-looking-string measure would infer wrong and produce a
    false "unresolved". Scatter's y type is genuinely data-dependent, so
    it is the only family this asks the data about.
    """
    if isinstance(chart, _CARTESIAN_WIDE_FAMILIES):
        return True
    return (
        isinstance(chart.y, str)
        and infer_vega_type_from_data(rows, chart.y) == "quantitative"
    )


def _color_field_and_domain(
    ctx: WarningContext,
    chart_id: str,
    chart: _CheckedChart,
    rows: list[dict[str, Any]],  # type-state: explicit_any — query rows
) -> tuple[str | None, list[str], dict[str, frozenset[str]] | None] | None:
    """(color_field, domain, aliases) for a chart's own legend, or None when
    this chart/family has nothing for this detector to check."""
    if isinstance(chart, (ResolvedPieChart, ResolvedGeoshapeChart)):
        # Both families normalize rows (Decimal -> float, date -> str)
        # before building their own domain, unlike every other family
        # here. Match that, or a Decimal/date color value folds
        # differently on each side.
        rows = normalize_data_types(rows)
    if isinstance(chart, ResolvedGeoshapeChart):
        # Without this gate the emitter falls back to a neutral-fill map
        # with no legend, so there would be nothing to check against.
        value_field = chart.value_field
        color_ch = chart.resolved_channels.get("color")
        if not (rows and chart.lookup_field and (value_field or color_ch is not None)):
            return None
        if value_field is None:
            return None
        if infer_vega_type_from_data(rows, value_field) == "quantitative":
            return None
        # Keeps only str values, matching what the emitter actually
        # paints: a non-str key doesn't render, and distinct_series_values
        # would str()-cast it into the domain instead.
        seen: list[str] = []
        for row in rows:
            v = row.get(value_field)
            if isinstance(v, str) and v not in seen:
                seen.append(v)
        return value_field, seen, None

    if measures := wide_measure_fields(chart):
        # A wide chart can't also author layers:, so no layer loop is
        # needed here. It can cross its measures with a color: dimension;
        # the domain is the humanized measure list (or the dimension
        # composite when one is set).
        dimension = chart.color
        wide_labels = wide_measure_labels_for(measures, chart.field_labels)
        domain = wide_series_names(measures, dimension, rows, wide_labels)
        aliases = wide_legend_aliases(measures, dimension, rows, wide_labels)
        # No color field: the domain is the measure list, so there is no
        # data column to name.
        return None, domain, aliases
    color_ch = chart.resolved_channels.get("color")
    if color_ch is None or color_ch.mode != "series" or not color_ch.data_field:
        # No color: on the base -- still worth checking a layer's own
        # color: via the overlay's shared base_label scale. Only fires
        # when the base has NO color channel at all: a literal/gradient/
        # conditional color still populates encoding.color without a
        # shared scale existing.
        if (
            color_ch is None
            and isinstance(chart, LayeredResolvedChart)
            and chart.layers
            and isinstance(chart.y, str)
            and _base_y_is_quantitative(chart, rows)
        ):
            # Mirrors use_shared_scale in full: a non-quantitative base y
            # means no shared scale gets built at all, so this must return
            # nothing too, not just skip layer contributions -- base_label
            # alone is not a domain the emitter ever shows.
            base_label, base_alias_set = layer_label_and_aliases(
                chart.y_label, chart.y, chart.field_labels
            )
            base_aliases = {base_label: base_alias_set}
            domain, aliases = _layered_domain_and_aliases(
                ctx,
                chart_id,
                chart,
                [base_label],
                base_aliases,
                rows,
                _base_y_is_quantitative(chart, rows),
            )
            return chart.y, domain, aliases
        return None

    # A quantitative color still resolves to mode="series", but its
    # legend is a continuous gradient tick ladder, not a categorical
    # list -- skip it here, matching the nominal/ordinal gate every
    # emit site uses (mode and field are already established true by
    # the guard above).
    if not categorical_color_encoding(
        color_ch, infer_vega_type_from_data(rows, color_ch.data_field)
    ):
        return None

    if isinstance(chart, LayeredResolvedChart) and chart.layers:
        domain, aliases = _layered_domain_and_aliases(
            ctx,
            chart_id,
            chart,
            list(distinct_series_values(rows, color_ch.data_field)),
            {},
            rows,
            _base_y_is_quantitative(chart, rows),
        )
        return color_ch.data_field, domain, aliases
    return color_ch.data_field, distinct_series_values(rows, color_ch.data_field), None


_CHECKED_FAMILIES = (*_CHANNEL_COLOR_FAMILIES, ResolvedGeoshapeChart)


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per chart with an authored legend entry that
    matches no series in this render's actual data."""
    warnings: list[Diagnostic] = []

    for chart_id, chart in ctx.board_spec.charts.items():
        # Every family in _CHECKED_FAMILIES declares `.legend` -- the
        # narrowed access below is guaranteed, not defensive.
        if not isinstance(chart, _CHECKED_FAMILIES):
            continue
        authored = chart.legend.values
        if authored is None:
            continue
        if chart_id not in ctx.chart_results:
            continue
        rows = ctx.chart_results[chart_id]
        # A suppressed legend normally has nothing to warn about.
        # Exception: a non-wide, non-histogram grouped (stack: "none")
        # bar with a nominal series color still reads legend.values for
        # its paint/xOffset order regardless of visibility.
        is_grouped_bar = (
            isinstance(chart, ResolvedBarChart)
            and chart.chart_type != "histogram"
            and chart.stack == "none"
            and not chart.wide_measures
        )
        if is_grouped_bar:
            color_ch = chart.resolved_channels.get("color")
            color_type = (
                infer_vega_type_from_data(rows, color_ch.data_field)
                if color_ch is not None and color_ch.data_field
                else None
            )
            is_grouped_bar = (
                categorical_color_encoding(color_ch, color_type)
                and color_type == "nominal"
            )
        if not chart.legend.visible and not is_grouped_bar:
            continue

        found = _color_field_and_domain(ctx, chart_id, chart, rows)
        if found is None:
            continue
        color_field, domain, aliases = found

        _, unmatched = resolve_legend_entries(authored, domain, aliases)
        if not unmatched:
            continue

        warnings.append(
            Diagnostic.from_code(
                WARN_LEGEND_VALUES_UNRESOLVED,
                chart=chart_id,
                path=f"charts.{chart_id}.style.legend.values",
                field=color_field,
                message=WARN_LEGEND_VALUES_UNRESOLVED.message_template.format(
                    chart_id=chart_id,
                    values=", ".join(repr(v) for v in unmatched),
                    domain=", ".join(domain) if domain else "none configured",
                ),
                fix=WARN_LEGEND_VALUES_UNRESOLVED.fix_template,
            )
        )

    return warnings
