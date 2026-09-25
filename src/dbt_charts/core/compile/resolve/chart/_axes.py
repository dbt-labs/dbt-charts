"""Cartesian axis baking: tick math, orientation, mirroring, endpoint-label suppression."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import ceil
from typing import Any, Literal, NamedTuple

from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.format import resolve_format
from dbt_charts.core.compile.models.chart.authored import (
    BarChartLayer,
    CartesianLayer,
    MultiplesConfig,
)
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    Chart,
    LineChart,
)
from dbt_charts.core.compile.models.chart.normalized._base import (
    _CartesianChartFields,
)
from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
from dbt_charts.core.compile.models.style.authored import (
    EndpointLabelsConfig,
    LegendStylePatch,
    QuantitativeAxisStylePatch,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedLegendStyle
from dbt_charts.core.compile.models.style.theme import (
    AxisXStyle,
    AxisYStyle,
    LegendPosition,
    _CartesianChartStyle,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import ChartDataset
from dbt_charts.core.compile.resolve.chart.plot_height_floor import (
    estimate_plot_height,
    plot_height_floor_px,
)
from dbt_charts.core.compile.resolve.style.axis_cascade import (
    AxisOverrides,
    _merge_axis_cascade,
    chart_type_axis_patch as _chart_type_axis_patch,
)
from dbt_charts.core.compile.resolve.style.chart_context import (
    chart_authored_axis_format,
)
from dbt_charts.core.compile.resolve.style.typography import width_tier
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_TICKS_INTERVAL_MEASURE_AXIS,
)
from dbt_charts.core.diagnostics.codes_render import (
    ERR_LAYER_AXIS_POSITION_ENDPOINT_LABELS,
    ERR_MULTIPLES_INDEPENDENT_SCALE_MIRROR,
)
from dbt_charts.core.font_measure import get_font_measurer
from dbt_charts.core.numeric import aspect_ratio_height
from dbt_charts.core.text.case import default_axis_title
from dbt_charts.core.text.predefined_formats import ALL_PREDEFINED_NAMES

__all__ = [
    "SeriesNaming",
    "_NO_RAIL_ENDPOINT_LABELS",
    "_author_asked_for_endpoint_labels",
    "_authored_legend",
    "_bake_ay_orient",
    "_bake_ay_position_left",
    "_bake_ay_position_right",
    "_bake_cartesian_axes",
    "_edge_or_none",
    "_endpoint_labels_off_for_layers",
    "_endpoint_labels_off_for_multiples",
    "_extract_axis_overrides",
    "_reject_dual_axis_layered_endpoint_labels",
    "cartesian_color_domain_values",
    "cartesian_series_naming",
    "cartesian_top_legend_entries",
    "estimate_cartesian_plot_height",
    "estimate_left_axis_reserve_px",
    "legend_row_fits",
    "legend_wrap_fits_height_budget",
    "legend_wrap_marginal_height_px",
    "legend_wrap_required_height_px",
]


def _extract_axis_overrides(
    primary: _CartesianChartStyle | None,
    quantitative: QuantitativeAxisStylePatch | None,
) -> AxisOverrides:
    """Extract chart-local axis patches from a family-level primary patch.

    Mirrors the axis_overrides_* extraction in build_chart_style_context
    (layers 11-13) so the resolve path can pass them directly to
    resolved_axis_style without building a per-chart ChartStyleContext. The
    x_label/y_label title-visible default is NOT applied here — it is
    injected directly into the axis cascade (``_merge_axis_cascade``'s
    ``label_authored`` parameter), the one place that already knows the full
    chart-local layer order, so every chart-local slot (global ``axis``,
    ``axis_quantitative``/``axis_band``, ``axis_x``/``axis_y``) beats the
    forcing default with no separate guard.

    ``quantitative`` is threaded in by the caller (``plan_cartesian``) rather
    than read off ``primary`` here: heatmap's per-family style patch has no
    ``axis_quantitative`` field at all — both its axes are nominal, styled
    via ``axis_band`` instead (see ``_QuantitativeAxisChartStyleMixin`` in
    ``compile/models/style/theme/_chart_base.py``) — so ``primary`` is not
    guaranteed to carry it the way it carries ``axis``/``axis_x``/``axis_y``/
    ``axis_band``.
    """
    if primary is None:
        return AxisOverrides()
    return AxisOverrides(
        global_=primary.axis,
        x=primary.axis_x,
        y=primary.axis_y,
        quantitative=quantitative,
        band=primary.axis_band,
    )


def _bake_cartesian_axes(
    chart_style_context: ChartStyleContext,
    chart: Chart,
    chart_type: str,
    x_channel_type: str,
    y_channel_type: str,
    axis_overrides: AxisOverrides,
    multiples: MultiplesConfig | None = None,
    y: str | list[str] | None = None,
) -> tuple[AxisXStyle, AxisYStyle, float | None, float | None, bool, bool]:
    """Walk the 13-layer axis cascade for axis_x and axis_y, returning the
    merged, theme-typed axis pair, each axis's band_position (None unless
    that channel classified ordinal/nominal and a band override authored it),
    and axis_y's format_authored/format_is_alias flags (see
    ``_merge_axis_cascade``) — NOT yet built into the frozen
    ``ResolvedAxisStyle``. axis_x's format_authored/format_is_alias are
    discarded — no caller threads them into ``build_resolved_axis`` today,
    since no caller passes ``tick_values`` for axis_x.

    ``ay_format_is_alias`` returned here is NOT just ``_merge_axis_cascade``'s
    own value verbatim: that function only ever sees an EXPLICITLY authored
    layer (chart-level fallback or chart-local override), so a bare axis that
    just inherits the theme's own baseline format
    (``axis_quantitative.labels.format: number``, itself a predefined
    name) would otherwise never count as alias-derived — the overwhelming
    common case (no format authored at all) would silently miss
    ``build_resolved_axis``'s forced-right-align treatment. This function
    ORs in a direct ``ay.labels.format in ALL_PREDEFINED_NAMES`` check
    (below, right before resolving the format) against the final winner of
    the whole cascade, which is correct regardless of which layer set it.

    axis_x = categorical axis (normalized.x column, dbt charts semantics).
    axis_y = measure axis (normalized.y column, dbt charts semantics).
    These semantics hold regardless of orientation — axis routing is a
    render-layer concern; the cascade slots are fixed.

    axis_overrides carries the chart-local patches (layers 11-13) extracted from
    the family patch — passed explicitly so no per-chart ChartStyleContext
    is required.

    ``chart`` supplies the per-chart format fallback (``chart.format`` /
    ``style.number_format`` / ``style.time_format``) fed into each axis's
    Layer 10 via ``chart_authored_axis_format()``. axis_x is always the
    dimension axis (never the measure, even when the channel type happens to
    be quantitative — e.g. a scatter's x column), so its fallback is
    temporal-only (``style.time_format``); the quantitative measure fallback
    (``style.number_format`` / ``chart.format``) only ever reaches axis_y.

    ``multiples`` / ``y`` are the small-multiples inputs. This is the ONE home
    for the multiples↔mirror interaction: it (a) supplies a default value for
    the shipped ``axis_y.mirror`` flag on a wide grid, and (b) rejects the
    scale-independent-plus-mirror contradiction. It never introduces a second
    mirror mechanism — explicit author/theme mirror always wins. Whether that
    default collides with an endpoint-label rail is decided later, in
    ``MirrorAxisFeature`` — the render feature that composes the rail is the
    only place that fact is actually known (see its module docstring).

    Callers must still: (1) resolve ``ay.position`` from ``auto`` via
    ``_bake_ay_orient``/``_bake_ay_position_left``/``_bake_ay_position_right``
    (2) derive each axis's final left/right edge, if any, and (3) call
    ``build_resolved_axis(merged, edge=...)`` to get the render-ready axis —
    this is what lets ``label.align``/``title.align``'s inward/outward
    resolve against a known edge in the same construction that narrows the
    type, instead of losing the directive to a premature build.
    """
    # x_label/y_label are structurally absent on some Chart union members
    # (e.g. kpi, pie), so read via getattr like chart_authored_axis_format's
    # own isinstance-guarded access — _bake_cartesian_axes is only ever
    # called with a cartesian family in practice, but its declared parameter
    # type is the full Chart union.
    ax, ax_band_position, _, _ = _merge_axis_cascade(
        chart_style_context,
        "axis_x",
        x_channel_type,
        _chart_type_axis_patch(chart_style_context, chart_type, "axis_x"),
        chart_fallback_format=(
            chart_authored_axis_format(chart, "temporal")
            if x_channel_type == "temporal"
            else None
        ),
        axis_overrides=axis_overrides,
        chart_type=chart_type,
        label_authored=bool(getattr(chart, "x_label", None)),
    )
    ay, ay_band_position, ay_format_authored, ay_format_is_alias = _merge_axis_cascade(
        chart_style_context,
        "axis_y",
        y_channel_type,
        _chart_type_axis_patch(chart_style_context, chart_type, "axis_y"),
        chart_fallback_format=chart_authored_axis_format(chart, y_channel_type),
        axis_overrides=axis_overrides,
        chart_type=chart_type,
        label_authored=bool(getattr(chart, "y_label", None)),
    )
    # labels.values (the label-cadence filter) lives only on DimensionLabelStyle,
    # used solely by AxisXStyle.labels — axis_y structurally cannot author it,
    # so no runtime check is needed here (Pydantic rejects it at the authoring
    # boundary instead). ticks.time_unit is likewise structurally x-only
    # (DimensionTicksStyle) and rejected the same way; only `step` still
    # exists on the universal AxisTicksStyle base, so it's the only field
    # left to guard here.
    if ay.ticks.step is not None:
        # Same measure-axis rationale as labels.values above — axis_y is never
        # temporal, so step-anchored cadence never applies there.
        raise CompilationError.from_code(ERR_TICKS_INTERVAL_MEASURE_AXIS)
    # time_unit+count is enforced in the model itself
    # (DimensionTicksStyle._validate_cadence) — no compile-pass check needed.
    # step-without-time_unit is not a model rule: a bare step is the
    # quantitative-axis cadence lever, so the check needs the resolved axis
    # type and lives in render (apply_x_tick_cadence).
    # Resolve any format alias (e.g. "currency_whole" → "$,.0f") at bake time
    # so axis_to_vl() emits valid d3-format strings, not internal alias names.
    # No `if fmts:` gate: resolve_format() resolves predefined names to their
    # round-aware specs (trim already baked) and aliases to native d3; fmts
    # empty/None still resolves predefined names on the first path.
    # The spec itself is already guaranteed resolvable by
    # validate/formats.py's compile()-time pass (the per-chart authored walk
    # plus the four theme-baked global axis slots) -- this function trusts
    # that guarantee rather than re-checking it, per core/AGENTS.md's
    # validation-boundary rule.
    fmts = chart_style_context.formats
    if ax.labels.format is not None:
        resolved_ax_format = resolve_format(ax.labels.format, fmts)
        ax = ax.model_copy(
            update={
                "labels": ax.labels.model_copy(update={"format": resolved_ax_format})
            }
        )
    if ay.labels.format is not None:
        # ay.labels.format is still the raw pre-resolve string here -- the
        # final winner of the whole 13-layer cascade, whichever layer set it
        # last (authored or theme-default). _merge_axis_cascade's own
        # format_is_alias only ever sees an EXPLICITLY authored layer (board
        # 6-9, chart-format fallback 10, or chart-local 11-13); a bare axis
        # inheriting the theme's own baseline
        # (axis_quantitative.labels.format: number, itself a
        # predefined name) never touches that tracking. Checking the final
        # raw value directly against ALL_PREDEFINED_NAMES covers both cases
        # uniformly -- it doesn't matter which layer won, only that the
        # winner is a predefined name.
        ay_format_is_alias = ay_format_is_alias or (
            ay.labels.format in ALL_PREDEFINED_NAMES
        )
        resolved_ay_format = resolve_format(ay.labels.format, fmts)
        ay = ay.model_copy(
            update={
                "labels": ay.labels.model_copy(update={"format": resolved_ay_format})
            }
        )
    # This getattr is load-bearing, not typing ceremony like x_label/y_label
    # above: HeatmapChart has no `layers` field and DOES resolve through
    # plan_cartesian, so an attribute read here would raise on every heatmap.
    ay = _apply_multiples_mirror(
        ay,
        multiples,
        y,
        y_channel_type,
        chart.id,
        has_layers=bool(
            getattr(
                chart, "layers", []
            )  # type-state: silent_fallback — layers is structurally absent on HeatmapChart, which does resolve through plan_cartesian; absent means "no overlays", not missing data
        ),
    )
    return (
        ax,
        ay,
        ax_band_position,
        ay_band_position,
        ay_format_authored,
        ay_format_is_alias,
    )


def _edge_or_none(value: str | None) -> Literal["left", "right"] | None:
    """Narrow a free-form position/orient string to a left/right edge, or
    None when it isn't one (bottom/top/auto/unset) — the axis has no
    left/right edge to resolve inward/outward align against."""
    if value == "left":
        return "left"
    if value == "right":
        return "right"
    return None


def _endpoint_label_rail_fires(
    channels: Mapping[str, ResolvedStyleChannel],
    endpoint_labels_visible: bool,
    *,
    wide_measure_series: bool = False,
    has_layers: bool = False,
) -> bool:
    """Return True when a rail of endpoint labels will render.

    Needs a series color channel (a folded wide area measure series, or a
    layered chart naming each layer's own endpoint — see
    ``EndpointLabelFeature._apply_layered_single_series``) to have anything
    to name, plus endpoint labels switched on. Callers must gate
    ``has_layers`` on ``dbt_charts.core.utils.layered_endpoint_rail_fires``
    themselves — this function trusts whatever it's handed.

    The layered term only fires when there is NO base color channel at all
    (``color_ch is None``): a gradient/literal/conditional color channel
    puts a non-series ``color`` encoding on the base spec, so
    ``emitters/_overlay.py``'s ``use_shared_scale`` never builds the shared
    color scale the layered rail reads (``_layer_color_scale``) — this must
    agree with ``EndpointLabelFeature.applies_to()``, which returns False for
    exactly that color-channel shape.
    """
    color_ch = channels.get("color")
    return endpoint_labels_visible and (
        wide_measure_series
        or (color_ch is not None and color_ch.mode == "series")
        or (has_layers and color_ch is None)
    )


def _suppress_legend_for_endpoint_labels(
    channels: Mapping[str, ResolvedStyleChannel],
    endpoint_labels_visible: bool,
    layered_rail_fires: bool,
    *,
    wide_measure_series: bool = False,
    has_layers: bool = False,
) -> bool:
    """Return True when endpoint labels will replace the color legend.

    Two disjoint shapes retire the legend, each matched to what
    ``EndpointLabelFeature.apply()`` actually names — deliberately narrower
    than "the rail fires at all":

    - No base color channel, only layers (the layered rail —
      ``_apply_layered_single_series``): ``layered_rail_fires`` (callers pass
      ``dbt_charts.core.utils.layered_endpoint_rail_fires``'s result) is
      already gated on every layer lacking its own color field, so once
      it's True the rail names every series the legend would otherwise
      carry — nothing left for the legend to do.
    - A base color-series channel, or a folded wide-area measure series:
      the rail (``_resolve_endpoint_label_positions`` / ``_apply_wide_area``)
      only ever names the base's own series, never an overlay layer's — see
      ``EndpointLabelFeature.apply()``'s branch on ``has_series_color``, which
      takes priority over the layered path regardless of ``chart.layers``.
      Retiring the legend here is only safe when there is no layer left
      unnamed — this must be the *raw* ``chart.layers`` fact (``has_layers``),
      not ``layered_rail_fires``: an overlay strands regardless of whether
      shape/orientation/color gates keep the layered rail itself from
      firing.
    """
    if not endpoint_labels_visible:
        return False
    color_ch = channels.get("color")
    if layered_rail_fires and color_ch is None:
        return True
    non_layered_rail_fires = wide_measure_series or (
        color_ch is not None and color_ch.mode == "series"
    )
    return non_layered_rail_fires and not has_layers


def _reject_dual_axis_layered_endpoint_labels(
    chart_id: str,
    layers: Sequence[CartesianLayer | BarChartLayer],
    layered_rail_fires: bool,
) -> None:
    """Refuse a layered endpoint-label rail across a dual-axis layer.

    The rail anchors on one shared y-scale (``_apply_layered_single_series``);
    a layer pinning its own ``axis_y.position`` renders on a different one.
    The trigger — an authored ``axis_y.position`` on a layer, on a chart
    whose rail would otherwise fire — is fully known at resolve, so it is
    refused here rather than at render: compile has already flipped the axis
    left and suppressed the legend for a rail that would never render.
    """
    if not layered_rail_fires:
        return
    if any(
        layer.axis_y is not None and layer.axis_y.position is not None
        for layer in layers
    ):
        raise ChartDataError.from_code(
            ERR_LAYER_AXIS_POSITION_ENDPOINT_LABELS, chart_id=chart_id
        )


def _endpoint_labels_off_for_layers(
    endpoint_labels: EndpointLabelsConfig,
    normalized: BarChart | LineChart | AreaChart,
    author_opted_in: bool,
) -> EndpointLabelsConfig:
    """A layered chart's default is a legend, not the endpoint-label rail.

    Every family here defaults endpoint labels on; without this disqualifier
    an un-opted-in layered chart would flip straight to the rail and leave a
    single-series chart with an overlay looking like the multi-series case it
    isn't.

    An author who writes ``style.endpoint_labels.visible: true`` opts in: the
    rail then names the base series and every overlay layer's own endpoint
    (see ``EndpointLabelFeature._apply_layered_single_series``), and
    ``_suppress_legend_for_endpoint_labels`` retires the legend so nothing is
    named twice.
    """
    if author_opted_in or not normalized.layers:
        return endpoint_labels
    return endpoint_labels.model_copy(update={"visible": False})


def _endpoint_labels_off_for_multiples(
    endpoint_labels: EndpointLabelsConfig,
    normalized: BarChart | LineChart | AreaChart,
    author_opted_in: bool,
) -> EndpointLabelsConfig:
    """Each small multiple is its own panel; the rail names series for one panel
    and a faceted chart has no single panel for it to sit beside. The shared
    top legend takes over the naming (``_multiples_wants_top_legend``).

    Like its sibling above it steers the default only: an author who wrote
    ``endpoint_labels.visible: true`` on a faceted chart still reaches the
    render-layer refusal, with a diagnostic naming both fields.
    """
    if author_opted_in or normalized.multiples is None:
        return endpoint_labels
    return endpoint_labels.model_copy(update={"visible": False})


def _multiples_wants_top_legend(
    normalized: _CartesianChartFields,
    channels: Mapping[str, ResolvedStyleChannel],
    wide_measure_series: bool = False,
) -> bool:
    """True when a faceted chart has series the legend must name from above.

    One legend above the panels reads across the whole grid; a side rail sits
    beside whichever panel it lands next to and reads as that panel's. Shares
    ``_endpoint_label_rail_fires`` with the rail it replaces so the two can't
    disagree about what counts as a series to name — passing ``True`` for the
    rail's visibility asks it the unconditional form of that question, since
    ``_endpoint_labels_off_for_multiples`` has just switched the real rail off.
    """
    return normalized.multiples is not None and _endpoint_label_rail_fires(
        channels, True, wide_measure_series=wide_measure_series
    )


def _authored_legend(
    primary: Any,  # type-state: explicit_any — Any at its source
) -> LegendStylePatch | None:
    """This chart's own ``legend:`` patch, or None when it authored no legend.

    ``primary`` stays ``Any`` here and in its sibling below because it's
    ``Any`` at its source (``CartesianPlan.primary`` — see that field's own
    docstring in ``_plan.py`` for why it isn't narrowed) and neither function
    does anything else with it. A local ``_HasColor``-style Protocol would
    still need the same ``None`` guard below, since the value genuinely is
    ``None`` for an unstyled chart — narrowing buys nothing here. The return
    is narrowed because the patch model does declare that field's type; only
    reaching it goes through ``Any``.

    The chart-local patch, not the merged cascade, is the only place the
    author's own say-so survives: a theme that hides legends board-wide
    (editorial does) leaves the same ``visible: False`` on the merged value,
    and a family default puts a ``position`` there for every chart that never
    named one. The width-tier, grouped-bar and endpoint-rail policies exist
    precisely to place a legend where the author left the choice open — so
    they read this patch to know when the choice was not left open.
    """
    return None if primary is None else primary.legend


def _author_asked_for_endpoint_labels(style: Any) -> bool:
    """True when this chart's own style asks for a rail of endpoint labels.

    Read from the authored patch for the same reason as ``_authored_legend``:
    every theme now switches the rail on, so the merged value says ``True`` for
    charts that never asked. Only this chart's own say-so overrides the
    shape-based disqualifiers. style stays Any: the patch model's
    TYPE_CHECKING stub declares this field non-optional while the runtime value
    really is None when unauthored, so a typed signature would let the type
    checker delete the None guard that every unstyled chart depends on.
    """
    if style is None:
        return False
    endpoint_labels = style.endpoint_labels
    return endpoint_labels is not None and endpoint_labels.visible is True


def _series_label_layout_for_width(
    endpoint_labels: EndpointLabelsConfig,
    width: float,
) -> tuple[EndpointLabelsConfig, bool]:
    """Make the top legend the series-naming mechanism for every tiny chart."""
    if width_tier(width) != "tiny":
        return endpoint_labels, False
    return (
        endpoint_labels.model_copy(update={"visible": False}),
        True,
    )


def _bake_ay_orient(
    ay: AxisYStyle,
    channels: Mapping[str, ResolvedStyleChannel],
    endpoint_labels_visible: bool,
    *,
    wide_measure_series: bool = False,
    has_layers: bool = False,
) -> AxisYStyle:
    """Resolve 'auto' y-axis position for line/area/bar at compile time.

    'auto' flips to 'left' when endpoint labels take the right rail (series
    color channel present, a layered chart, or endpoint_labels.visible on a
    folded wide area); 'right' otherwise. Non-'auto' positions pass through
    unchanged so authored explicit sides win.

    Operates on the merged, pre-build ``AxisYStyle`` (not the frozen
    ``ResolvedAxisStyle``) — called before ``build_resolved_axis`` so the
    axis's final position is known in time to resolve inward/outward align
    against it in the same build call.
    """
    if ay.position != "auto":
        return ay
    fires = _endpoint_label_rail_fires(
        channels,
        endpoint_labels_visible,
        wide_measure_series=wide_measure_series,
        has_layers=has_layers,
    )
    return ay.model_copy(update={"position": "left" if fires else "right"})


def _bake_ay_position_left(ay: AxisYStyle) -> AxisYStyle:
    """Resolve 'auto' y-axis position to 'left' (VL default) for chart types
    that never placed the y-axis on the right: heatmap (categorical y) and
    histogram. Non-'auto' positions pass through unchanged.

    Operates on the merged, pre-build ``AxisYStyle`` — see ``_bake_ay_orient``.
    """
    if ay.position != "auto":
        return ay
    return ay.model_copy(update={"position": "left"})


def _bake_ay_position_right(ay: AxisYStyle) -> AxisYStyle:
    """Resolve 'auto' y-axis position to 'right' for chart types that have no
    endpoint-label rail to yield the right side to (scatter). Non-'auto'
    positions pass through unchanged.

    Operates on the merged, pre-build ``AxisYStyle`` — see ``_bake_ay_orient``.
    """
    if ay.position != "auto":
        return ay
    return ay.model_copy(update={"position": "right"})


def _apply_multiples_mirror(
    ay: AxisYStyle,
    multiples: MultiplesConfig | None,
    y: str | list[str] | None,
    y_channel_type: str,
    chart_id: str,
    has_layers: bool,
) -> AxisYStyle:
    """Resolve the both-edge y-axis for a small-multiples chart.

    Both-edge (mirror) reuses the single shipped ``resolved_axis_y.mirror``
    flag. ``ay.mirror is None`` means neither author nor theme set it — only
    then does the orthogonal-count>1 rule supply ``True``. An explicit True/
    False/AxisMirrorStyle (author or theme) is left untouched. ``scale:
    independent`` with mirror on — bool True or an AxisMirrorStyle
    format/expr override, both truthy — is a contradiction and raises.

    Whether this auto-default collides with an endpoint-label rail is NOT
    decided here: that fact (the rail's final visibility, after every
    render-layer disqualifier — width tier, horizontal-stack shape, ...) is
    only known once ``EndpointLabelFeature`` has run, which is after resolve
    is long done. ``MirrorAxisFeature`` decides the collision at render time
    instead, from the composed spec it already holds.

    Operates on the merged, pre-build ``AxisYStyle`` — see ``_bake_ay_orient``.
    """
    if multiples is None:
        return ay
    # The measure axis (y) spreads horizontally across panel COLUMNS, so a
    # declared `columns` field is the "orthogonal panel count > 1" signal: the
    # far columns sit away from the single left axis and want a mirrored edge.
    # A rows-only stack has one column and never auto-mirrors.
    has_columns = multiples.columns is not None
    if multiples.scale == "independent" and ay.mirror:
        raise ChartDataError.from_code(
            ERR_MULTIPLES_INDEPENDENT_SCALE_MIRROR, chart_id=chart_id
        )
    # Auto-default: multiple columns + shared scale + unset mirror + single
    # QUANTITATIVE measure axis + no overlay layers. Mirroring is only
    # meaningful for a shared numeric scale — a categorical y (heatmap) or
    # multi-series y has no both-edge meaning, so never auto-enable there.
    # `isinstance(y, str)` rather than `not isinstance(y, list)`:
    # y_channel_type is the family's SEMANTIC channel type (line/area/bar
    # hardcode "quantitative"), passed even when no y is authored at all —
    # and with no y there is no y encoding, so a defaulted mirror would give
    # MirrorAxisFeature nothing to reflect. `layers:` likewise backs the
    # default off: the overlay assembly moves every y encoding onto the
    # layers, leaving no shared y encoding for the ghost to bind — an
    # AUTHORED mirror on that shape is refused with ERR-MIRROR-LAYERS at
    # render, and the engine's own default must never author its way into a
    # refusal.
    if (
        has_columns
        and multiples.scale == "shared"
        and ay.mirror is None
        and y_channel_type == "quantitative"
        and isinstance(y, str)
        and not has_layers
    ):
        return ay.model_copy(update={"mirror": True})
    return ay


def _series_display_name(field: str | None, label: str | None) -> str | None:
    """A series' own legend name: an authored label, else the field's default
    title. Mirrors ``XYTitles.y_plain``'s exact formula
    (``render/chart/emitters/_cartesian.py``) and the "Engine-derived layer
    name" rule (``render/chart/emitters/_overlay.py``) -- the same value the
    render layer will actually put in the color scale domain, so measuring
    against it here is measuring the real entry, not a guess.
    """
    return label or (default_axis_title(field) if field else None)


def cartesian_color_domain_values(
    dataset: ChartDataset, color: str | None
) -> tuple[str, ...]:
    """The chart's real, distinct color-channel values, read off the
    executed dataset -- empty when the chart has no color channel.

    Null values are dropped: render's ``distinct_series_values``
    (``render/chart/emitters/_cartesian.py``) documents why a null paints no
    series -- admitting it would charge the fit predicate for a phantom
    ``"None"`` legend entry nothing ever draws. Mirrors the ``is not None``
    filter ``_distinct_series_count`` (``resolve/chart/bar.py``) already
    applies to the same column for the same reason.
    """
    if color is None:
        return ()
    return tuple(str(v) for v in dataset.column_values(color) if v is not None)


def cartesian_top_legend_entries(
    y: str | None,
    y_label: str | None,
    layers: Sequence[CartesianLayer],
    color_domain_values: tuple[str, ...],
    *,
    has_color: bool,
) -> tuple[str, ...]:
    """The real legend entries a top strip would have to name for this chart.

    Two sources, both literal and known before the query runs: the base
    series' own display name and each overlay layer's, present only when the
    chart actually has layers (an unlayered chart's own series is not a
    legend entry -- there is nothing else to distinguish it from). Unioned
    with ``color_domain_values`` -- the chart's real, distinct color-channel
    values, already read off the executed dataset by the caller
    (``ChartDataset.column_values``), empty when the chart has no color
    channel. That is the one source genuinely unknowable before this point;
    everything else here is authored, not guessed.

    ``has_color`` mirrors render's own ``field_color_base`` fork
    (``render/chart/emitters/_overlay.py``): a base with its own color field
    never gets its y-title added as a separate legend entry there -- its
    color-domain values (``color_domain_values``) stand in for it instead.
    Passing the base name in on top of that domain would charge the fit
    predicate for an entry render never draws. A layer with no ``y`` is
    likewise skipped entirely by render (it has nothing to plot), so it is
    excluded here rather than borrowing the base's field as a stand-in name.
    """
    layer_entries: tuple[str, ...] = ()
    if layers:
        base_name = None if has_color else _series_display_name(y, y_label)
        names = [base_name] + [
            _series_display_name(layer.y, layer.label)
            for layer in layers
            if layer.y is not None
        ]
        layer_entries = tuple(name for name in names if name is not None)
    return layer_entries + color_domain_values


def legend_row_fits(
    entries: Sequence[str], width: float, legend: ResolvedLegendStyle
) -> bool:
    """True when one horizontal row naming every one of ``entries`` fits
    within ``width``.

    This is the single source of truth for "does this legend fit" --
    ``cartesian_series_naming`` calls it to decide top vs. right at compile
    time from the entries it can already know (see
    ``cartesian_top_legend_entries``); the render-time fix for a top legend
    that silently clips or drops entries once it stops fitting (a separate,
    already-filed defect) should call this same function with the chart's
    real, executed entries -- the two must never drift into disagreeing about
    what "fits" means.

    The swatch box and the gap to the next entry are fixed rendering chrome
    (``chart_rendering.legend``), constant regardless of mark size or entry
    count -- a legend swatch stays legible-sized even naming a thousand tiny
    marks; scaling it to the marks it names is explicitly
    rejected. Only the label TEXT is measured, against the legend's own real
    resolved font -- never estimated, never a fixed per-entry count (measured
    with realistic mixed-length names, capacity swings from 7 entries at
    640px to 5 at 420px, so a hardcoded count is wrong at any single width).
    """
    if not entries:
        return True
    cfg = get_chart_rendering().legend
    measurer = get_font_measurer(legend.label.font.family)
    font_size = legend.label.font.size
    chrome = cfg.row_swatch_width_px + legend.label.padding
    total = sum(chrome + measurer.measure(entry, font_size) for entry in entries)
    total += cfg.row_entry_gap_px * (len(entries) - 1)
    return total <= width


def estimate_left_axis_reserve_px(
    dimension_values: Sequence[str] | None, legend: ResolvedLegendStyle
) -> float:
    """Estimated horizontal space this chart's own left-side axis reserves
    before a top legend's single row can begin -- ``legend_row_fits`` must
    compare against ``width`` minus this reserve, not ``width`` alone.

    Vega-Lite's ``autosize: fit`` anchors a top-oriented legend to the
    plot's own local coordinate origin, not the card's left edge, so a real
    left-side axis eats directly into the row's budget. Measured directly
    (a real render hard-clipped a single-row legend by exactly this much
    before this fix existed): every family reached by the top-legend fit
    rule defaults its quantitative measure axis to the RIGHT ordinarily
    (``_bake_ay_orient``'s default branch), so in the ordinary case nothing
    of substance sits to the legend's left --
    ``chart_rendering.legend.min_reserve_px`` is that small, fixed floor
    (generic card/frame chrome, not axis content). Not an unconditional
    guarantee for line/area: with endpoint labels visible,
    ``_bake_ay_orient`` can still flip their own measure axis to the left
    when the rail fires (``_endpoint_label_rail_fires``) -- a wide-measure
    fold, or a ``color:`` channel in series mode, or an unlabeled
    (``color`` absent) ``layers:`` overlay, each alone sufficient -- this
    reserve does not currently account for that real, content-driven left
    axis (known gap, not fixed here).

    The one shape that puts a real axis on the left UNCONDITIONALLY is a
    horizontal bar: its *dimension* field (``normalized.x``) becomes
    Vega-Lite's y-channel, drawn on the left with its own real category
    labels via ``_bake_ay_position_left`` (always left, no right branch --
    the horizontal rail this orients for sits above the plot, never beside
    it). ``dimension_values`` is that field's real, distinct values (bar.py
    passes them only for the horizontal case; every other caller passes
    ``None``) -- measured with the legend's own resolved font as a stand-in
    for the axis's own (not yet built at this point in resolve; the two
    share this theme's muted body-text tier). ``None`` means the axis
    reserving space here isn't dimension-driven -- the fixed floor is the
    whole estimate.
    """
    cfg = get_chart_rendering().legend
    if not dimension_values:
        return cfg.min_reserve_px
    measurer = get_font_measurer(legend.label.font.family)
    widest = max(measurer.measure(v, legend.label.font.size) for v in dimension_values)
    return max(cfg.min_reserve_px, widest + cfg.dimension_axis_reserve_chrome_px)


def legend_wrapped_row_count(
    entry_count: int, symbol_limit: int | None, columns: int
) -> int:
    """Row count a wrapped, multi-column legend needs to name ``entry_count``
    series.

    Caps at ``symbol_limit`` before dividing: Vega-Lite draws at most
    ``symbol_limit`` entries (plus its own synthetic "...N entries" row), so
    the row count must reflect what actually renders, not the raw column
    cardinality -- a 30-series chart with ``symbol_limit: 20`` needs the same
    row count as a 20-series one. Called from this module's own
    ``legend_wrap_required_height_px`` (the legend's total footprint at
    collapse -- bar.py's ``_stack_legend_should_yield``) and
    ``legend_wrap_marginal_height_px`` (the legend's marginal cost against a
    plot-height floor that charges its own fixed chrome separately -- the
    floor's own compact-legend charge and ``legend_wrap_fits_height_budget``)
    -- the row count itself is one calculation regardless of which height
    question a caller goes on to ask of it.
    """
    capped = min(entry_count, symbol_limit) if symbol_limit else entry_count
    return ceil(capped / columns)


def legend_wrap_required_height_px(
    entry_count: int, symbol_limit: int | None, columns: int
) -> float:
    """Pixel height a wrapped, multi-column legend needs to name
    ``entry_count`` series: row count times a fixed per-row height, plus
    fixed title/padding chrome.

    This is the legend's *total* footprint at collapse -- correct only
    where nothing else is subtracted alongside it. Its one caller is
    bar.py's own ``_stack_legend_should_yield`` (does this legend alone
    squeeze a stacked bar's own segments to zero height? ``required >
    plot_height``, nothing else in the comparison). A caller instead
    feeding a plot-height *floor* that already charges its own fixed chrome
    (``estimate_plot_height``'s ``irreducible_height_px`` /
    ``axis_titles_height_px``) wants ``legend_wrap_marginal_height_px``
    below, not this -- summing both fixed terms double-counts the
    non-legend chrome baked into this one's ``chrome_height_px``.
    ``chart_rendering.legend.row_height_px`` / ``chrome_height_px`` are the
    calibrated constants (see default_config.yml).
    """
    rows = legend_wrapped_row_count(entry_count, symbol_limit, columns)
    cfg = get_chart_rendering().legend
    return rows * cfg.row_height_px + cfg.chrome_height_px


def legend_wrap_marginal_height_px(
    entry_count: int, symbol_limit: int | None, columns: int
) -> float:
    """Marginal pixel height a wrapped, multi-column legend adds against a
    plot-height floor: row count times a flat per-row cost, no fixed chrome
    term of its own.

    Distinct from ``legend_wrap_required_height_px`` (that legend's *total*
    footprint at collapse, fixed chrome included) -- this is the number a
    caller that feeds ``estimate_plot_height`` wants, since that function
    already subtracts its own fixed chrome
    (``chart_rendering.plot_height_floor.irreducible_height_px`` /
    ``axis_titles_height_px``) alongside whatever this returns. Summing the
    *total* footprint on top of that fixed chrome double-counts the
    non-legend chrome the total's own fixed term carries. Shared by bar.py's
    own compact-legend floor charge and this module's
    ``legend_wrap_fits_height_budget`` -- both ask the same floor about the
    same wrapped legend, so this arithmetic is one calculation, not two.
    ``chart_rendering.plot_height_floor.compact_legend_row_px`` is the
    calibrated constant (see default_config.yml).
    """
    rows = legend_wrapped_row_count(entry_count, symbol_limit, columns)
    cfg = get_chart_rendering().plot_height_floor
    return rows * cfg.compact_legend_row_px


def legend_wrap_fits_height_budget(
    entries: Sequence[str],
    legend: ResolvedLegendStyle,
    plot_height_estimate: float,
    *,
    card_padding_px: float,
    subtitle_present: bool,
    axis_title_costs_height: bool,
) -> bool:
    """True when putting a wrapped, multi-column top legend naming
    ``entries`` above the plot still leaves the plot clearing
    ``chart_rendering.plot_height_floor.ratio`` of the card's own height.

    Rung 2 of the top-legend fallback ladder, between a single-row fit
    (``legend_row_fits``) and giving up on top placement. Asks the same
    plot-height floor ``bar.py``'s own ``WARN-PLOT-HEIGHT-BELOW-MINIMUM``
    measurement asks (``plot_height_floor.py``) -- "if I put this candidate
    legend on top, does the plot still clear the floor?" -- rather than
    carrying a second, independently-calibrated share: two mechanisms
    measuring "how much height can a legend take before the plot suffers"
    against different constants could silently disagree about the same
    chart -- rejecting first an earlier fixed ``max_wrap_rows`` row cap,
    then the ``max_wrap_height_share`` share-of-body check this replaced
    once the floor's own estimate landed on main.

    ``plot_height_estimate`` is every caller's own
    ``estimate_cartesian_plot_height(normalized, style, width)`` -- the same
    CARD content-height estimate the floor itself is measured against.
    ``card_padding_px``, ``subtitle_present`` and ``axis_title_costs_height``
    are the floor's other required inputs (``style.frame.card_padding``,
    whether this chart authored a subtitle, and whether the axis on the
    horizontal rail shows its title) -- each caller already has these in
    hand for its own floor-adjacent logic or axis cascade.
    """
    marginal = legend_wrap_marginal_height_px(
        len(entries), legend.symbol_limit, legend.compact_columns
    )
    card_height = plot_height_estimate + 2 * card_padding_px
    estimated_plot_height = estimate_plot_height(
        card_height,
        card_padding_px=card_padding_px,
        legend_height_px=marginal,
        subtitle_present=subtitle_present,
        axis_title_costs_height=axis_title_costs_height,
    )
    return estimated_plot_height >= plot_height_floor_px(card_height)


def estimate_cartesian_plot_height(
    normalized: _CartesianChartFields, style: _CartesianChartStyle, width: float
) -> float:
    """Aspect-ratio-driven plot-height estimate, needing no query data.

    Shared by every cartesian family (bar, line, area, scatter, heatmap): all
    of them inherit the same ``height``/``aspect_ratio``/``min_height``/
    ``max_height`` fields, both on the normalized chart
    (``_CartesianChartFields``) and on the cascade-resolved per-family style
    (``_CartesianChartStyle``), so the arithmetic does not vary by family --
    only the two inputs bar.py, line.py, area.py, scatter.py, and heatmap.py
    each already have in hand (their own normalized chart and resolved
    style).

    Shares its clamp arithmetic with render/sizing.py's ``get_chart_content_height``
    via ``numeric.aspect_ratio_height`` -- the two differ only in which min/max
    height they clamp to (this resolve step's per-family, cascade-resolved
    style vs. render's board-global ``resolved_style.chart_defaults``), a
    cascade-position difference each caller's own inputs express.

    This is a CARD content-height estimate (the full Vega-Lite ``autosize:
    fit`` budget a title, legend, and plot rectangle all draw from), not a
    net plot-rectangle height -- it does not subtract this chart's own
    title/subtitle chrome, which renders inside that same budget. See
    ``legend_wrap_fits_height_budget``'s docstring for the consequence.
    """
    if normalized.height is not None:
        return float(normalized.height)
    aspect = (
        normalized.aspect_ratio
        if normalized.aspect_ratio is not None
        else style.aspect_ratio
    )
    if width <= 0 or aspect <= 0:
        return style.min_height
    min_h = (
        normalized.min_height if normalized.min_height is not None else style.min_height
    )
    max_h = (
        normalized.max_height if normalized.max_height is not None else style.max_height
    )
    return aspect_ratio_height(width, aspect, min_h, max_h)


class SeriesNaming(NamedTuple):
    """The legend-vs-rail decision every cartesian family resolves with.

    ``endpoint_labels`` is the tiny-width-adjusted rail config. Families with
    no rail (scatter, heatmap) pass ``_NO_RAIL_ENDPOINT_LABELS`` in and have
    no field on their own resolved style to put the output in — "no rail" is
    the explicit input, the returned copy is simply unused, never a skipped
    call to this function.

    ``legend_position_overridden_by_width`` carries the author's own
    ``position`` when ``tiny_top_legend`` overrode it back to top — the one
    route ``author_moved_legend_off_top`` does not outrank (see this
    function's docstring). ``None`` otherwise, including when the author
    placed no position at all.

    ``force_legend_visible`` is true both when the author placed a position
    and when the chart's own shape wants a legend (``top_legend_series is
    not None``) — the latter regardless of whether ``top_legend`` actually
    came out "row" or fell back to "off": a shape that wants a legend but
    whose row doesn't fit the card still needs one, just not at the top, and
    a theme that hides this family's legend by default (line, area) must not
    silently delete it once "off" stops meaning "top didn't fit".
    """

    endpoint_labels: EndpointLabelsConfig
    suppress_legend: bool
    top_legend: Literal["compact", "row", "off"]
    force_legend_visible: bool
    legend_position_overridden_by_width: LegendPosition | None


_NO_RAIL_ENDPOINT_LABELS = EndpointLabelsConfig(
    visible=False, label_offset=0.0, height=0.0
)


def cartesian_series_naming(
    normalized: _CartesianChartFields,
    channels: Mapping[str, ResolvedStyleChannel],
    authored_legend: LegendStylePatch | None,
    legend: ResolvedLegendStyle,
    width: float,
    endpoint_labels: EndpointLabelsConfig,
    endpoint_label_has_layers: bool,
    has_layers: bool,
    rail_eligible_for_suppression: bool,
    suppress_wide_measure_series: bool,
    multiples_wide_measure_series: bool,
    top_legend_series: tuple[str, ...] | None,
    plot_height_estimate: float,
    left_axis_reserve_px: float,
    card_padding_px: float,
    subtitle_present: bool,
    axis_title_costs_height: bool,
) -> SeriesNaming:
    """Decide how a cartesian chart's series get named: rail, top legend, or plain legend.

    Owns the composition every family previously hand-assembled: the
    tiny-width fallback to a compact top legend
    (``_series_label_layout_for_width``), whether endpoint labels retire the
    color legend (``_suppress_legend_for_endpoint_labels``), whether a
    small-multiples grid wants one legend above the panels
    (``_multiples_wants_top_legend``), and the top_legend/off ternary that
    reads those together with the author's own ``legend:`` keys.

    ``authored_legend`` is this chart's own legend patch — every caller passes
    ``_authored_legend(primary)`` — and it outranks all three policies,
    because each of them only ever answers a question the author left open:

    - ``visible: true`` keeps the endpoint rail from retiring the legend. The
      rail replaces a legend nobody asked for, never one that was asked for;
      it goes on rendering beside it.
    - a ``position`` is a legend the author placed, so the automatic top strip
      stands down and (unless ``visible: false`` is authored alongside) the
      legend is shown — naming where a legend goes cannot resolve to no legend.
      The one policy it does not outrank is the tiny-width tier: a card that
      narrow physically cannot hold a side legend, so ``tiny_top_legend`` wins
      over an authored ``position: right`` the way it already wins over the
      rail.
    - ``visible: false`` still wins over both, and over the width-tier and
      grouped-bar policies that would otherwise bring a legend back.

    ``endpoint_labels`` arrives already family-disqualified (bar's stack
    chain, or line/area's layers-then-multiples chain already applied) —
    this function only applies the tiny-width fold on top.

    ``rail_eligible_for_suppression`` ANDs into the tiny-adjusted
    ``endpoint_labels.visible`` right before the suppression check, and only
    there: bar's horizontal grouped shape never earns a rail even when
    ``endpoint_labels.visible`` is true (``_horizontal_series_rail_fires``);
    every other family passes ``True``.

    ``suppress_wide_measure_series`` and ``multiples_wide_measure_series`` are
    separate parameters for the families that ever need them to differ, even
    though bar.py, line.py, area.py, and scatter.py all currently pass the
    same real ``resolve_wide_measure_channels`` flag to both (a wide ``y:``
    list folds into a legend the same way it folds into a suppression signal
    for all four). heatmap.py has no wide-measure fold (its own, different
    one-rect-layer-per-measure path) and passes ``False`` to both.

    ``top_legend_series`` is the one chart-shape trigger every family reads
    the same way, replacing what used to be two family-specific opt-in
    booleans (``layers_route_to_top_legend``, ``unconditional_top_legend``).
    ``None`` means this family never routes to a top legend through this
    mechanism -- the mark-similarity carve-out (scatter, bubble, point map:
    their legend swatch reads as one more data mark, so it stays right-hand
    and vertical) and heatmap (a gradient legend is a different shape that
    does not wrap into a row). A tuple -- built by the caller from
    ``cartesian_top_legend_entries``, empty when this shape has no known
    entries yet still wants to try -- means this family follows the
    three-rung fallback ladder: top, single row, while those
    entries fit ``width`` (``legend_row_fits``); failing that, top, wrapped
    at ``legend.compact_columns``, while putting that wrap on top still
    leaves the plot clearing ``chart_rendering.plot_height_floor.ratio``
    (``legend_wrap_fits_height_budget``, asking the same floor
    ``bar.py``'s own starved-plot warning asks); right, the family's own
    fallback position, once even the wrap would sink the plot below the
    floor. Bar passes a tuple
    whenever it is not stacked (its own long-standing unconditional
    default -- a stacked bar's segments read better against a side legend,
    so a stacked bar with an overlay layer still stays out of this
    mechanism); area and line pass one whenever they have layers. So the two
    previous per-family conditions collapse into one question, answered the
    same way by every family that asks it: "does this shape have entries it
    wants named at top".

    ``plot_height_estimate`` is every caller's own
    ``estimate_cartesian_plot_height(normalized, style, width)`` -- computed
    once by the caller (bar.py also reuses it for its own
    ``_stack_legend_should_yield`` check) since it needs no query data.

    ``left_axis_reserve_px`` is every caller's own
    ``estimate_left_axis_reserve_px(...)`` -- the row-fit check
    (``legend_row_fits``) must compare the row against ``width`` minus this
    reserve, not ``width`` alone, or a row that only fits the card's full
    canvas width still hard-clips against the plot's own (narrower) local
    origin once the axis reserving that difference is real.

    ``card_padding_px``, ``subtitle_present`` and ``axis_title_costs_height``
    feed rung 2's floor check (``legend_wrap_fits_height_budget``) alone --
    ``style.frame.card_padding``, whether this chart authored a subtitle, and
    whether the axis on the horizontal rail shows its title, matching the
    same three facts ``bar.py``'s own ``WARN-PLOT-HEIGHT-BELOW-MINIMUM``
    measurement feeds to ``estimate_plot_height``. A family whose
    ``top_legend_series`` is always ``None`` (scatter, heatmap) never reaches
    rung 2, so these three are inert for it -- pass ``False``/``0.0`` rather
    than a real measurement, the same convention ``left_axis_reserve_px``
    already uses there.

    A gradient color channel is excluded from the two ladder rungs that
    measure rows, here, once, for every family -- not re-derived per call
    site. ``channels["color"]`` is the real, resolved channel
    (``style.color.gradient`` is authorable on bar, line, and area alike,
    via the shared ``_PaintedChartStyleBase.color`` field), and a gradient
    is a continuous ramp: it has no row to wrap, so it must never drive
    rung 1 or rung 2, regardless of what ``top_legend_series`` a caller
    computed. This does NOT null the parameter itself -- ``top_legend_series``
    is also ``wants_top_legend_shape``'s "this shape has entries to name"
    signal, which forces the legend visible even when the row doesn't fit;
    nulling it would silence a gradient-colored line/area's legend
    entirely on a theme that hides it by default, trading a placement bug
    for a worse one.
    """
    color_channel = channels.get("color")
    gradient_color = color_channel is not None and color_channel.mode == "gradient"
    endpoint_labels, tiny_top_legend = _series_label_layout_for_width(
        endpoint_labels, width
    )
    author_hid_legend = authored_legend is not None and authored_legend.visible is False
    author_showed_legend = (
        authored_legend is not None and authored_legend.visible is True
    )
    # Read the position out before testing it. `LegendStylePatch`'s TYPE_CHECKING
    # stub subclasses `LegendStyle`, where `position` is required, so mypy sees
    # `authored_legend.position is not None` as always-true and rejects it -- but
    # at runtime `build_patch_model` makes every field optional, and an author
    # who wrote only `visible: true` really does leave this None. Going through a
    # local gives mypy the `| None` the stub can't express, and the check stays.
    authored_position = (
        authored_legend.position if authored_legend is not None else None
    )
    author_placed_legend = authored_position is not None and not author_hid_legend
    suppress_legend = not (
        author_showed_legend or author_placed_legend
    ) and _suppress_legend_for_endpoint_labels(
        channels,
        endpoint_labels.visible and rail_eligible_for_suppression,
        endpoint_label_has_layers,
        wide_measure_series=suppress_wide_measure_series,
        has_layers=has_layers,
    )
    # An authored `position: top` agrees with the automatic top strip -- only a
    # position that moves the legend off the top is a conflict to resolve. Short-
    # circuiting on agreement would drop the horizontal/columns-0/titleless
    # layout and render a worse legend than authoring nothing.
    author_moved_legend_off_top = author_placed_legend and authored_position != "top"
    legend_position_overridden_by_width: LegendPosition | None = (
        authored_position if tiny_top_legend and author_moved_legend_off_top else None
    )
    # The chart's shape wants a legend named at all, independent of whether a
    # single top row happens to fit: `top_legend_series is not None` is the
    # family's own "this shape has entries to name" signal (see the docstring
    # above), gated the same way the "row" route below gates it -- an
    # explicit `visible: false` or an endpoint rail retiring the legend both
    # still win. When the row does NOT fit, the fit rule falls the position
    # back to the theme's default (right, for the families that reach here),
    # not to no-legend-at-all -- so this must force the same `visible: True`
    # the "row" route forces, or a theme that hides this family's legend by
    # default (line, area) silently deletes the only naming this chart had.
    wants_top_legend_shape = (
        top_legend_series is not None and not suppress_legend and not author_hid_legend
    )
    top_legend_series_row_fits = (
        top_legend_series is not None
        and not suppress_legend
        and not gradient_color
        and legend_row_fits(top_legend_series, width - left_axis_reserve_px, legend)
    )
    # Rung 2 of the fallback ladder: a legend whose single
    # row overflows the card can still wrap into a top, multi-column legend
    # as long as putting it on top still leaves the plot clearing the same
    # plot-height floor bar.py's own starved-plot warning checks -- named
    # entries at the top beat a right-hand legend that costs real plot
    # width, up to that floor. Past it (rung 3) the wrapped legend would
    # sink the plot below the floor instead, and the chart falls back to
    # the family's own right-hand legend, same as every overflow did before
    # this rung existed.
    top_legend_series_wrap_fits = (
        top_legend_series is not None
        and not suppress_legend
        and not gradient_color
        and not top_legend_series_row_fits
        and legend_wrap_fits_height_budget(
            top_legend_series,
            legend,
            plot_height_estimate,
            card_padding_px=card_padding_px,
            subtitle_present=subtitle_present,
            axis_title_costs_height=axis_title_costs_height,
        )
    )
    multiples_wants_top_legend = _multiples_wants_top_legend(
        normalized, channels, multiples_wide_measure_series
    )
    top_legend: Literal["compact", "row", "off"] = (
        "off"
        if author_hid_legend
        else (
            "compact"
            if tiny_top_legend
            else (
                "off"
                if author_moved_legend_off_top
                else (
                    "row"
                    if top_legend_series_row_fits or multiples_wants_top_legend
                    else "compact"
                    if top_legend_series_wrap_fits
                    else "off"
                )
            )
        )
    )
    return SeriesNaming(
        endpoint_labels,
        suppress_legend,
        top_legend,
        author_placed_legend or wants_top_legend_shape,
        legend_position_overridden_by_width,
    )
