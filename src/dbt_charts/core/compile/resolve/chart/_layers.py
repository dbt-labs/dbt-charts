"""Layer resolution for layered cartesian charts."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.format import (
    check_anchored_sign,
    column_format,
    declared_column_format,
    resolve_label_format,
)
from dbt_charts.core.compile.models.chart.authored._layer import (
    AreaLayer,
    BarLayer,
    CartesianLayer,
    LayerAxisYStyle,
    LineLayer,
    ScatterLayer,
)
from dbt_charts.core.compile.models.chart.resolved._channel import ResolvedStyleChannel
from dbt_charts.core.compile.models.chart.resolved._layer import (
    ResolvedAreaLayer,
    ResolvedBarLayer,
    ResolvedLayer,
    ResolvedLayerAxisY,
    ResolvedLayerAxisYLabels,
    ResolvedLineLayer,
    ResolvedScatterLayer,
)
from dbt_charts.core.compile.models.primitives import ResolvedFormat, resolved_as
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedBarLabels,
    ResolvedBarMarkStyle,
    ResolvedBarTotalLabel,
    ResolvedPointLabels,
    ResolvedPointMarkStyle,
)
from dbt_charts.core.compile.models.style.resolved._base import ResolvedTickLabel
from dbt_charts.core.compile.models.style.theme.area import AreaChartStyle
from dbt_charts.core.compile.models.style.theme.bar import BarChartStyle
from dbt_charts.core.compile.models.style.theme.category_colors import (
    category_scale_for,
    series_colors,
)
from dbt_charts.core.compile.models.style.theme.line import LineChartStyle
from dbt_charts.core.compile.models.style.theme.scatter import ScatterChartStyle
from dbt_charts.core.compile.resolve.chart._channels import _column_numeric_values
from dbt_charts.core.compile.resolve.chart._chart_rows import (
    CartesianChart,
    ChartRows,
    LayerDatasets,
)
from dbt_charts.core.compile.resolve.chart._kwargs import _bound_scales
from dbt_charts.core.compile.resolve.chart._layer_ink import (
    LayerBase,
    LayerInk,
    layer_inks,
)
from dbt_charts.core.compile.resolve.chart._marks import (
    _apply_line_width,
    _build_resolved_area_line,
    _build_resolved_area_mark,
    _build_resolved_line_mark,
    _resolve_authored_label_format,
    _resolved_point_mark,
    _with_label_format,
)
from dbt_charts.core.compile.resolve.chart._palette import (
    _effective_palette,
    _HasColor,
    _with_color_tokens,
)
from dbt_charts.core.compile.resolve.chart.adaptive_stroke import (
    bake_point_companions,
)
from dbt_charts.core.compile.resolve.style.axis_cascade import (
    affix_tick_label,
    ladderless_tick_label,
)
from dbt_charts.core.compile.resolve.style.inherit_resolver import merge_unset
from dbt_charts.core.constants.marks import STROKE_FALLBACK_WIDTH
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_LAYER_AXIS_TITLE_WITHOUT_POSITION,
    ERR_LAYERS_AMBIGUOUS_Y_DOMAIN,
)
from dbt_charts.core.text.format_d3 import is_d3_si_spec

__all__ = [
    "_check_layers_y_domain",
    "_resolve_layer_inks",
    "_resolve_layer_list",
    "_resolve_one_layer",
]


def _layer_rows(
    layer: CartesianLayer,
    data: ChartRows,
    base_query_name: str | None,
    datasets: LayerDatasets,
) -> ChartRows:
    """The rows a layer's own y measure paints, for its format vote.

    A layer on the base query reads ``data``; one with its own ``query:`` reads
    ``datasets`` (``...`` means none supplied; an absent name raises).
    """
    if layer.query is None or layer.query == base_query_name:
        return data
    if datasets is ...:
        return []
    if layer.query not in datasets:
        raise ChartDataError(f"Missing rows for layer query {layer.query!r}")
    return datasets[layer.query]


def _authored_ink(
    layer: CartesianLayer,
    chart_style_context: ChartStyleContext,
    base_family_style: BarChartStyle
    | LineChartStyle
    | AreaChartStyle
    | ScatterChartStyle,
) -> str | None:
    """The one color a layer authors for its series: a line's stroke, a
    scatter's point. An area's fill and a bar's always come from the palette."""
    patch = _with_color_tokens(layer.style, chart_style_context)
    marks = patch.marks if patch is not None else None
    if isinstance(layer, LineLayer):
        parent = (
            base_family_style
            if isinstance(base_family_style, LineChartStyle)
            else chart_style_context.line
        )
        stroke = merge_unset(
            parent.marks.line, marks.line if marks is not None else None
        ).stroke
        # An authored `stroke: null` is refused when the layer's marks resolve.
        return None if stroke is None else stroke.color
    if isinstance(layer, ScatterLayer):
        parent = (
            base_family_style
            if isinstance(base_family_style, ScatterChartStyle)
            else chart_style_context.scatter
        )
        return merge_unset(
            parent.marks.point, marks.point if marks is not None else None
        ).color
    return None


def _resolve_layer_inks(
    normalized: CartesianChart,
    chart_style_context: ChartStyleContext,
    base_family_style: BarChartStyle
    | LineChartStyle
    | AreaChartStyle
    | ScatterChartStyle,
    primary: _HasColor | None,
    channels: Mapping[str, ResolvedStyleChannel],
    data: ChartRows,
    datasets: LayerDatasets,
    single_series_fill: str,
    *,
    measure_quantitative: bool,
) -> tuple[LayerInk, ...]:
    """Each layer's ink on the chart's shared color scale (`_layer_ink.layer_inks`).

    The base claims the scale first: one color per value of its categorical
    `color`, else its one single-series fill. Layers share that scale only
    where the base's measure is quantitative and its color, if any, is a
    categorical field.
    """
    if not normalized.layers:
        return ()
    palette = _effective_palette(chart_style_context, primary)
    board_scales = _bound_scales(normalized, chart_style_context, palette)
    color = channels["color"] if normalized.color is not None else None
    if color is None:
        base = LayerBase(
            measure_quantitative,
            {str(normalized.y): single_series_fill} if measure_quantitative else {},
        )
    elif color.mode == "series" and not color.quantitative_data:
        values = sorted(
            {
                str(row[color.data_field])
                for row in data
                if row.get(color.data_field) is not None
            }
        )
        base = LayerBase(
            measure_quantitative,
            series_colors(
                values, palette, category_scale_for(board_scales, color.data_field)
            ),
        )
    else:
        base = LayerBase(False, {})
    return layer_inks(
        normalized.layers,
        [
            _authored_ink(layer, chart_style_context, base_family_style)
            for layer in normalized.layers
        ],
        base,
        chart_id=normalized.id,
        palette=palette,
        board_scales=board_scales,
        single_series_fill=single_series_fill,
        rows_of=lambda layer: _layer_rows(layer, data, normalized.query_name, datasets),
    )


def _resolve_one_layer(
    layer: CartesianLayer,
    chart_style_context: ChartStyleContext,
    base_type: str,
    base_family_style: Any,
    base_query_name: str | None,
    line_adaptive_stroke: float,
    line_px_per_point: float,
    data: ChartRows,
    datasets: LayerDatasets,
    chart_id: str,
    index: int,
    query_formats: Mapping[str, str] | None,
    layer_ink: LayerInk,
    layer_query_formats: Mapping[str, Mapping[str, str]],
) -> ResolvedLayer:
    """Resolve a single typed authored layer into its resolved counterpart.

    Each mark in the layer's marks group is merged independently onto its
    family's base mark, mirroring exactly the family's resolved style mark
    set. When the layer's type matches the chart's OWN base mark family
    (e.g. a line-type layer on a line chart), that base mark is the chart's
    own already-resolved family style (``base_family_style`` — board theme
    merged with the chart-root ``style:`` patch), not the raw board theme —
    a same-type layer must inherit chart-level style overrides exactly like
    the base series it overlays. A cross-family layer (e.g. a line layer on
    a bar chart) has no matching chart-level override to inherit — bar
    chart style carries no ``line`` mark fields — so it anchors on the
    board theme's own family default instead.

    ``query_name`` resolves to the layer's own authored ``query:`` when
    present, else ``base_query_name`` — a layer with no override shares the
    base chart's query, so this is never an ambiguous ``None`` at render
    time (barring the rare inline-unnamed-query chart, whose own
    ``query_name`` is itself None).

    The layer's own ``style`` patch is run through ``_with_color_tokens``
    exactly like the chart-root style patch — otherwise a palette token
    authored on a per-layer style (e.g. ``layers[].style.marks.line.stroke.
    color``) leaks through to the renderer as a literal token string instead
    of the resolved hex value.

    ``line_adaptive_stroke``: the base chart's already-computed density-adaptive
    stroke (> 0 when applicable, 0.0 when not).  Applied to line/area layers
    that have no explicit authored stroke — same x-axis, same density, same
    stroke width.  Layers with an authored stroke.width carry their own pin.

    ``line_px_per_point``: the base chart's density signal (same x-axis, so
    the same value applies here), or ``0.0`` wherever none was measured — on a
    non-line base, and on a line base with no x channel, no rows, or no
    width.  A line-type layer's own point companions are baked from it
    exactly like the base series — otherwise a layered line chart would show
    dots on the base series and never on its overlays.

    Register is decided by alias-membership only: a layer's own authored format
    string that is a theme alias → house narrative; a literal d3 spec → native Vega.
    Unformatted overlay labels stay unformatted — no axis fallback for layers.

    ``data``/``datasets`` feed the sub-$1 vote of every label built below:
    Vega bakes one spec per label, so it must vote over the layer's own rows.
    """

    def _layer_values() -> Iterator[float]:
        # Lazy: only a voting format reads the rows.
        yield from _column_numeric_values(
            _layer_rows(layer, data, base_query_name, datasets), layer.y
        )

    authored_axis_y = layer.axis_y if layer.axis_y is not None else LayerAxisYStyle()
    axis_format: ResolvedFormat | None = None
    tick_label: ResolvedTickLabel | None = None
    tooltip_format: ResolvedFormat | None = None
    raw_format = (
        authored_axis_y.labels.format if authored_axis_y.labels is not None else None
    )
    own_query_formats = (
        query_formats
        if layer.query is None
        # Only a query that declares `formats:` has an entry.
        else layer_query_formats.get(layer.query)
    )
    if raw_format is not None or (
        declared_column_format(
            own_query_formats, layer.y, chart_style_context.formats, time=False
        )
        is not None
    ):
        # A spec-less affix takes the ``number`` default so the painter has digits.
        axis_format = column_format(
            "number",
            chart_style_context.formats,
            raw_format,
            query_formats=own_query_formats,
            columns=layer.y,
        )
        if is_d3_si_spec(axis_format.spec) and axis_format.is_house:
            tick_label = ladderless_tick_label(axis_format, list(_layer_values()))
        else:
            tick_label = affix_tick_label(axis_format, ())
        if tick_label is not None and tick_label.anchor_at_start is not None:
            check_anchored_sign(
                axis_format, f"charts.{chart_id}.layers[{index}].axis_y.labels.format"
            )
        voted = column_format(
            "number",
            chart_style_context.formats,
            raw_format,
            query_formats=own_query_formats,
            columns=layer.y,
            values=_layer_values(),
        )
        if voted.spec != axis_format.spec:
            tooltip_format = voted
    axis_y = ResolvedLayerAxisY(
        position=authored_axis_y.position,
        title=authored_axis_y.title,
        scale=authored_axis_y.scale,
        ticks=authored_axis_y.ticks,
        grid=authored_axis_y.grid,
        labels=(
            ResolvedLayerAxisYLabels(format=axis_format)
            if axis_format is not None
            else None
        ),
        tick_label=tick_label,
        tooltip_format=tooltip_format,
    )
    query_name = layer.query if layer.query is not None else base_query_name
    layer_style = _with_color_tokens(layer.style, chart_style_context)
    if isinstance(layer, LineLayer):
        line_marks = layer_style.marks if layer_style is not None else None
        line_parent = (
            base_family_style if base_type == "line" else chart_style_context.line
        )
        _layer_line_patch = line_marks.line if line_marks is not None else None
        line_merged = merge_unset(line_parent.marks.line, _layer_line_patch)
        pre_fallback_point_mark = merge_unset(
            line_parent.marks.point,
            line_marks.point if line_marks is not None else None,
        )
        # Register by alias-gate: no axis fallback for overlay layers.
        line_fmt, line_label_is_house = resolve_label_format(
            line_merged.labels.format, chart_style_context.formats, _layer_values()
        )
        point_fmt, point_label_is_house = resolve_label_format(
            pre_fallback_point_mark.labels.format,
            chart_style_context.formats,
            _layer_values(),
        )
        line_labels = _with_label_format(
            line_merged.labels, ResolvedPointLabels, line_fmt
        )
        point_labels = _with_label_format(
            pre_fallback_point_mark.labels, ResolvedPointLabels, point_fmt
        )
        # Mirror _build_layer_label_specs: line_mark.labels used when visible,
        # else point_mark.labels — pick the matching is_house.
        layer_label_is_house = (
            line_label_is_house if line_labels.visible is True else point_label_is_house
        )
        # A layer is always its own trend line, never a separator — the
        # stacked width is base-chart-recipe-specific and does not apply here.
        line_merged = _apply_line_width(
            line_merged,
            line_adaptive_stroke,
            STROKE_FALLBACK_WIDTH,
        )
        resolved_line_layer_mark = _build_resolved_line_mark(line_merged, line_labels)
        line_layer_point_mark = resolved_as(
            ResolvedPointMarkStyle, pre_fallback_point_mark, labels=point_labels
        )
        # Same density signal, same gate, same companion derivation as the
        # base series (bake_point_companions) — otherwise a layered line
        # chart shows dots on the base series and none on its overlays. On a
        # non-line base, line_px_per_point is 0.0: the size half is skipped
        # and the ring still tracks this layer's own stroke. A reference line
        # (one constant value) marks a level, not a series of observations, so
        # it takes no density-derived dots; an authored point size is honored.
        if resolved_line_layer_mark.stroke.width > 0:
            size_authored = line_layer_point_mark.size is not None
            line_layer_point_mark = bake_point_companions(
                line_layer_point_mark,
                resolved_line_layer_mark.stroke.width,
                line_px_per_point,
            )
            # Only an automatic size needs the rows: judge them lazily.
            auto_size = line_layer_point_mark.size
            if not size_authored and auto_size is not None and auto_size > 0:
                layer_values = list(_layer_values())
                if (
                    layer.color is None
                    and len(layer_values) > 1
                    and len(set(layer_values)) == 1
                ):
                    line_layer_point_mark = line_layer_point_mark.model_copy(
                        update={"size": 0.0}
                    )
        return ResolvedLineLayer(
            type="line",
            line_mark=resolved_line_layer_mark,
            point_mark=line_layer_point_mark,
            ink=layer_ink.ink,
            series_inks=layer_ink.series,
            axis_y=axis_y,
            x=layer.x,
            y=layer.y,
            label=layer.label,
            color=layer.color,
            query_name=query_name,
            label_is_house=layer_label_is_house,
            legend=layer.legend,
        )
    if isinstance(layer, AreaLayer):
        area_marks = layer_style.marks if layer_style is not None else None
        area_parent = (
            base_family_style if base_type == "area" else chart_style_context.area
        )
        _layer_area_patch = area_marks.line if area_marks is not None else None
        area_line_merged = merge_unset(area_parent.marks.line, _layer_area_patch)
        area_fmt, area_label_is_house = resolve_label_format(
            area_line_merged.labels.format, chart_style_context.formats, _layer_values()
        )
        area_line_labels = _with_label_format(
            area_line_merged.labels, ResolvedPointLabels, area_fmt
        )
        area_line_merged = _apply_line_width(
            area_line_merged,
            line_adaptive_stroke,
            STROKE_FALLBACK_WIDTH,
        )
        return ResolvedAreaLayer(
            type="area",
            area_mark=_build_resolved_area_mark(
                merge_unset(
                    area_parent.marks.area,
                    area_marks.area if area_marks is not None else None,
                )
            ),
            line_mark=_build_resolved_area_line(area_line_merged, area_line_labels),
            point_mark=_resolved_point_mark(
                merge_unset(
                    area_parent.marks.point,
                    area_marks.point if area_marks is not None else None,
                ),
                chart_style_context.formats,
                _layer_values(),
            ),
            ink=layer_ink.ink,
            series_inks=layer_ink.series,
            axis_y=axis_y,
            x=layer.x,
            y=layer.y,
            label=layer.label,
            color=layer.color,
            query_name=query_name,
            label_is_house=area_label_is_house,
            legend=layer.legend,
        )
    if isinstance(layer, BarLayer):
        bar_marks = layer_style.marks if layer_style is not None else None
        bar_parent = (
            base_family_style if base_type == "bar" else chart_style_context.bar
        )
        pre_fallback_bar_mark = merge_unset(
            bar_parent.marks.bar,
            bar_marks.bar if bar_marks is not None else None,
        )
        bar_fmt, bar_label_is_house = resolve_label_format(
            pre_fallback_bar_mark.labels.format,
            chart_style_context.formats,
            _layer_values(),
        )
        bar_labels = _with_label_format(
            pre_fallback_bar_mark.labels, ResolvedBarLabels, bar_fmt
        )
        return ResolvedBarLayer(
            type="bar",
            bar_mark=resolved_as(
                ResolvedBarMarkStyle,
                pre_fallback_bar_mark,
                labels=bar_labels,
                total_label=_resolve_authored_label_format(
                    pre_fallback_bar_mark.total_label,
                    ResolvedBarTotalLabel,
                    chart_style_context.formats,
                ),
            ),
            ink=layer_ink.ink,
            series_inks=layer_ink.series,
            axis_y=axis_y,
            x=layer.x,
            y=layer.y,
            y_start=layer.y_start,
            label=layer.label,
            color=layer.color,
            query_name=query_name,
            label_is_house=bar_label_is_house,
            legend=layer.legend,
        )
    # ScatterLayer
    scatter_marks = layer_style.marks if layer_style is not None else None
    scatter_parent = (
        base_family_style if base_type == "scatter" else chart_style_context.scatter
    )
    pre_fallback_scatter_point = merge_unset(
        scatter_parent.marks.point,
        scatter_marks.point if scatter_marks is not None else None,
    )
    scatter_fmt, scatter_label_is_house = resolve_label_format(
        pre_fallback_scatter_point.labels.format,
        chart_style_context.formats,
        _layer_values(),
    )
    scatter_labels = _with_label_format(
        pre_fallback_scatter_point.labels, ResolvedPointLabels, scatter_fmt
    )
    return ResolvedScatterLayer(
        type="scatter",
        point_mark=resolved_as(
            ResolvedPointMarkStyle, pre_fallback_scatter_point, labels=scatter_labels
        ),
        ink=layer_ink.ink,
        series_inks=layer_ink.series,
        axis_y=axis_y,
        x=layer.x,
        y=layer.y,
        label=layer.label,
        color=layer.color,
        query_name=query_name,
        label_is_house=scatter_label_is_house,
        legend=layer.legend,
    )


def _check_layers_y_domain(
    chart_id: str,
    layers: tuple[ResolvedLayer, ...],
    chart_domain: tuple[int | float | str, int | float | str],
) -> None:
    """Raise CompilationError when a chart-level y domain is set on a split dual-axis chart.

    A chart-level axis_y.scale.domain is ambiguous when layers use independent
    left/right y scales — each side would need its own domain.  One pinned
    layer is enough to split the axis: the base takes the opposite side.  Catch
    this at compile time so the error carries a compile-domain code, not a
    render-time one.
    """
    # Pinning any layer to either side moves the base to the opposite side, so
    # base and overlay land on different scales — same logic as
    # _resolved_layer_y_orients.
    if any(layer.axis_y.position is not None for layer in layers):
        raise CompilationError.from_code(
            ERR_LAYERS_AMBIGUOUS_Y_DOMAIN,
            chart_id=chart_id,
            domain=list(chart_domain),
        )


def _resolve_layer_list(
    layers: Sequence[CartesianLayer],
    chart_style_context: ChartStyleContext,
    base_type: str,
    base_family_style: Any,
    base_query_name: str | None,
    line_adaptive_stroke: float,
    line_px_per_point: float,
    data: ChartRows,
    datasets: LayerDatasets,
    chart_id: str,
    query_formats: Mapping[str, str] | None,
    inks: Sequence[LayerInk],
    layer_query_formats: Mapping[str, Mapping[str, str]],
) -> tuple[ResolvedLayer, ...]:
    """Resolve a list of authored typed layers to their resolved counterparts.

    ``base_type``/``base_family_style`` identify the chart's own already-
    resolved family style (board theme + chart-root ``style:`` patch) — see
    ``_resolve_one_layer`` for why a same-type layer inherits from it instead
    of the raw board theme.

    ``line_adaptive_stroke``: the base chart's density-adaptive stroke (> 0
    when applicable, 0.0 when not).  Threaded to each layer so line/area
    layers without an authored stroke width inherit the same density-computed
    value as the base chart (same x-axis → same density).

    ``line_px_per_point``: the base chart's density signal where one was
    measured, and ``0.0`` where none was — bar, scatter and area all pass the
    literal, so on those bases it means only "skip the size half". A
    line-type layer still gets its ring there, baked off its own stroke (see
    ``_resolve_one_layer``); only the density-driven dot size is withheld.

    ``data``/``datasets``: the base rows and layer-query map, for each layer's
    label format vote.

    ``query_formats`` is the base query's ``formats:``; a layer naming its own
    query reads that query's from ``layer_query_formats`` instead.
    """
    if not layers:
        return ()
    # A layer's own y-axis title only has an axis to land on once some layer
    # pins `axis_y.position` and splits the scales.
    if not any(
        layer.axis_y is not None and layer.axis_y.position is not None
        for layer in layers
    ):
        for index, layer in enumerate(layers):
            if layer.axis_y is not None and layer.axis_y.title is not None:
                raise CompilationError.from_code(
                    ERR_LAYER_AXIS_TITLE_WITHOUT_POSITION,
                    field_path=f"charts.{chart_id}.layers[{index}].axis_y.title",
                )
    return tuple(
        _resolve_one_layer(
            layer,
            chart_style_context,
            base_type,
            base_family_style,
            base_query_name,
            line_adaptive_stroke,
            line_px_per_point,
            data,
            datasets,
            chart_id,
            index,
            query_formats,
            inks[index],
            layer_query_formats,
        )
        for index, layer in enumerate(layers)
    )
