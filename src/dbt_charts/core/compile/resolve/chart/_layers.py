"""Layer resolution for layered cartesian charts."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.format import resolve_format, resolve_label_format
from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.chart.authored._layer import (
    AreaLayer,
    BarChartBarLayer,
    BarChartLayer,
    BarLayer,
    CartesianLayer,
    LayerAxisYStyle,
    LineLayer,
)
from dbt_charts.core.compile.models.chart.resolved._layer import (
    ResolvedAreaLayer,
    ResolvedBarLayer,
    ResolvedLayer,
    ResolvedLineLayer,
    ResolvedScatterLayer,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve.chart._marks import (
    _build_resolved_area_line,
    _build_resolved_area_mark,
    _build_resolved_line_mark,
)
from dbt_charts.core.compile.resolve.chart._palette import _with_color_tokens
from dbt_charts.core.compile.resolve.chart.adaptive_stroke import (
    bake_line_stroke,
    bake_point_companions,
)
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_LAYERS_AMBIGUOUS_Y_DOMAIN,
)

__all__ = [
    "_check_layers_y_domain",
    "_resolve_layer_list",
    "_resolve_one_layer",
]


def _resolve_one_layer(
    layer: CartesianLayer,
    chart_style_context: ChartStyleContext,
    base_type: str,
    base_family_style: Any,
    base_query_name: str | None,
    line_adaptive_stroke: float,
    line_px_per_point: float,
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
    """
    axis_y = layer.axis_y if layer.axis_y is not None else LayerAxisYStyle()
    # axis_y.labels.format is an authored spec that _overlay.py hands to Vega
    # verbatim for axis ticks — it needs alias lookup + round-aware trim so the
    # overlay axis agrees on digits with the base chart's axis for the same spec.
    if axis_y.labels is not None and axis_y.labels.format is not None:
        axis_y = axis_y.model_copy(
            update={
                "labels": axis_y.labels.model_copy(
                    update={
                        "format": resolve_format(
                            axis_y.labels.format, chart_style_context.formats
                        )
                    }
                )
            }
        )
    query_name = layer.query if layer.query is not None else base_query_name
    layer_style = _with_color_tokens(layer.style, chart_style_context)
    if isinstance(layer, LineLayer):
        line_marks = layer_style.marks if layer_style is not None else None
        line_parent = (
            base_family_style if base_type == "line" else chart_style_context.line
        )
        _layer_line_patch = line_marks.line if line_marks is not None else None
        line_merged = merge_onto_base(line_parent.marks.line, _layer_line_patch)
        # Bake adaptive only when the layer author left stroke unset.
        # bake_line_stroke handles the zero-sentinel (stroke.width==0.0) guard.
        if (
            _layer_line_patch is None
            or _layer_line_patch.stroke is None
            or _layer_line_patch.stroke.width is None
        ):
            line_merged = bake_line_stroke(line_merged, line_adaptive_stroke)
        pre_fallback_point_mark = merge_onto_base(
            line_parent.marks.point,
            line_marks.point if line_marks is not None else None,
        )
        # Register by alias-gate: no axis fallback for overlay layers.
        line_fmt, line_label_is_house = resolve_label_format(
            line_merged.labels.format, chart_style_context.formats
        )
        point_fmt, point_label_is_house = resolve_label_format(
            pre_fallback_point_mark.labels.format, chart_style_context.formats
        )
        line_labels = line_merged.labels.model_copy(update={"format": line_fmt})
        point_labels = pre_fallback_point_mark.labels.model_copy(
            update={"format": point_fmt}
        )
        # Mirror _build_layer_label_specs: line_mark.labels used when visible,
        # else point_mark.labels — pick the matching is_house.
        layer_label_is_house = (
            line_label_is_house if line_labels.visible is True else point_label_is_house
        )
        line_merged_final = line_merged.model_copy(update={"labels": line_labels})
        resolved_line_layer_mark = _build_resolved_line_mark(line_merged_final)
        line_layer_point_mark = pre_fallback_point_mark.model_copy(
            update={"labels": point_labels}
        )
        # Same density signal, same gate, same companion derivation as the
        # base series (bake_point_companions) — otherwise a layered line
        # chart shows dots on the base series and none on its overlays. On a
        # non-line base, line_px_per_point is 0.0: the size half is skipped
        # and the ring still tracks this layer's own stroke.
        if resolved_line_layer_mark.stroke.width > 0:
            line_layer_point_mark = bake_point_companions(
                line_layer_point_mark,
                resolved_line_layer_mark.stroke.width,
                line_px_per_point,
                size_authored=line_layer_point_mark.size is not None,
                ring_authored=line_layer_point_mark.stroke_width is not None,
            )
        return ResolvedLineLayer(
            type="line",
            line_mark=resolved_line_layer_mark,
            point_mark=line_layer_point_mark,
            axis_y=axis_y,
            x=layer.x,
            y=layer.y,
            label=layer.label,
            color=layer.color,
            query_name=query_name,
            label_is_house=layer_label_is_house,
        )
    if isinstance(layer, AreaLayer):
        area_marks = layer_style.marks if layer_style is not None else None
        area_parent = (
            base_family_style if base_type == "area" else chart_style_context.area
        )
        _layer_area_patch = area_marks.line if area_marks is not None else None
        area_line_merged = merge_onto_base(area_parent.marks.line, _layer_area_patch)
        # Bake adaptive only when the layer author left stroke unset.
        if (
            _layer_area_patch is None
            or _layer_area_patch.stroke is None
            or _layer_area_patch.stroke.width is None
        ):
            area_line_merged = bake_line_stroke(area_line_merged, line_adaptive_stroke)
        area_fmt, area_label_is_house = resolve_label_format(
            area_line_merged.labels.format, chart_style_context.formats
        )
        area_line_labels = area_line_merged.labels.model_copy(
            update={"format": area_fmt}
        )
        area_line_merged_final = area_line_merged.model_copy(
            update={"labels": area_line_labels}
        )
        return ResolvedAreaLayer(
            type="area",
            area_mark=_build_resolved_area_mark(
                merge_onto_base(
                    area_parent.marks.area,
                    area_marks.area if area_marks is not None else None,
                )
            ),
            line_mark=_build_resolved_area_line(area_line_merged_final),
            point_mark=merge_onto_base(
                area_parent.marks.point,
                area_marks.point if area_marks is not None else None,
            ),
            axis_y=axis_y,
            x=layer.x,
            y=layer.y,
            label=layer.label,
            color=layer.color,
            query_name=query_name,
            label_is_house=area_label_is_house,
        )
    if isinstance(layer, BarLayer):
        bar_marks = layer_style.marks if layer_style is not None else None
        bar_parent = (
            base_family_style if base_type == "bar" else chart_style_context.bar
        )
        pre_fallback_bar_mark = merge_onto_base(
            bar_parent.marks.bar,
            bar_marks.bar if bar_marks is not None else None,
        )
        bar_fmt, bar_label_is_house = resolve_label_format(
            pre_fallback_bar_mark.labels.format, chart_style_context.formats
        )
        bar_labels = pre_fallback_bar_mark.labels.model_copy(update={"format": bar_fmt})
        return ResolvedBarLayer(
            type="bar",
            bar_mark=pre_fallback_bar_mark.model_copy(update={"labels": bar_labels}),
            axis_y=axis_y,
            x=layer.x,
            y=layer.y,
            y_start=layer.y_start if isinstance(layer, BarChartBarLayer) else None,
            label=layer.label,
            color=layer.color,
            query_name=query_name,
            label_is_house=bar_label_is_house,
        )
    # ScatterLayer
    scatter_marks = layer_style.marks if layer_style is not None else None
    scatter_parent = (
        base_family_style if base_type == "scatter" else chart_style_context.scatter
    )
    pre_fallback_scatter_point = merge_onto_base(
        scatter_parent.marks.point,
        scatter_marks.point if scatter_marks is not None else None,
    )
    scatter_fmt, scatter_label_is_house = resolve_label_format(
        pre_fallback_scatter_point.labels.format, chart_style_context.formats
    )
    scatter_labels = pre_fallback_scatter_point.labels.model_copy(
        update={"format": scatter_fmt}
    )
    return ResolvedScatterLayer(
        type="scatter",
        point_mark=pre_fallback_scatter_point.model_copy(
            update={"labels": scatter_labels}
        ),
        axis_y=axis_y,
        x=layer.x,
        y=layer.y,
        label=layer.label,
        color=layer.color,
        query_name=query_name,
        label_is_house=scatter_label_is_house,
    )


def _check_layers_y_domain(
    chart_id: str,
    layers: tuple[ResolvedLayer, ...],
    chart_domain: tuple[int | float | str, int | float | str],
) -> None:
    """Raise CompilationError when a chart-level y domain is set on a split dual-axis chart.

    A chart-level axis_y.scale.domain is ambiguous when layers use independent
    left/right y scales — each side would need its own domain.  Catch this at
    compile time so the error carries a compile-domain code, not a render-time one.
    """
    if not layers:
        return
    positions = {
        layer.axis_y.position for layer in layers if layer.axis_y.position is not None
    }
    # The base chart implicitly occupies the left side; a right-pinned overlay
    # triggers an independent scale — same logic as _resolved_layer_y_orients.
    independent_y = bool(positions & {"right"})
    if independent_y:
        raise CompilationError.from_code(
            ERR_LAYERS_AMBIGUOUS_Y_DOMAIN,
            chart_id=chart_id,
            domain=list(chart_domain),
        )


def _resolve_layer_list(
    layers: Sequence[CartesianLayer | BarChartLayer],
    chart_style_context: ChartStyleContext,
    base_type: str,
    base_family_style: Any,
    base_query_name: str | None,
    line_adaptive_stroke: float,
    line_px_per_point: float,
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
    """
    if not layers:
        return ()
    return tuple(
        _resolve_one_layer(
            layer,
            chart_style_context,
            base_type,
            base_family_style,
            base_query_name,
            line_adaptive_stroke,
            line_px_per_point,
        )
        for layer in layers
    )
