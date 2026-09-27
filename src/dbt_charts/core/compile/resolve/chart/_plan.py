"""The cartesian prelude and postlude every family resolver composes explicitly.

``plan_cartesian`` runs the shared setup (channels, axis cascade bake) every
cartesian family needs before its own stack/zero/domain math.
``build_cartesian_axes`` runs the shared teardown (the two
``ResolvedAxisStyle`` builds, the style tail, and the tooltip-format merge)
once each family has finished mutating its own axis pair; the caller passes
its final, post-mutation axes directly, not a ``CartesianPlan``.
Tick/zero/domain resolution stays in each family's own file — this module
only shares what every family assembles identically.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from dbt_charts.core.compile.models.chart.authored import MultiplesConfig
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    HeatmapChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedAxisStyle
from dbt_charts.core.compile.models.style.theme import AxisXStyle, AxisYStyle
from dbt_charts.core.compile.resolve.chart._axes import (
    _bake_cartesian_axes,
    _edge_or_none,
    _extract_axis_overrides,
)
from dbt_charts.core.compile.resolve.chart._channels import (
    _channels_for,
    _column_numeric_values,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import ChartRows
from dbt_charts.core.compile.resolve.chart._domain import (
    _CartesianTickResolution,
    _y_gridline_caps_bottom,
)
from dbt_charts.core.compile.resolve.chart._palette import (
    _cartesian_style_tail,
    _with_color_tokens,
)
from dbt_charts.core.compile.resolve.style.axis_cascade import build_resolved_axis

__all__ = [
    "CartesianPlan",
    "build_cartesian_axes",
    "plan_cartesian",
    "quantitative_channel_values",
]


def quantitative_channel_values(
    data: ChartRows, y: str | list[str] | None
) -> list[float]:
    """Every value a cartesian family's own y measure(s) will paint.

    Feeds the per-chart sub-$1 format vote (resolve_format_for_values) at
    both tooltip-format candidates: the chart-authored one
    (_measure_tooltip_format) and the board-default fallback
    (_cartesian_style_tail). A multi-metric line's list of y fields votes as
    one set, same as the donut's theta-plus-total vote — mirrors bar's own
    "y is always the measure, regardless of orientation" contract, so a
    horizontal bar's category/measure swap never needs a special case here.
    Called by bar/line/area/scatter, whose tooltip formats y (and, for
    scatter, x too — see scatter.py's own vote). Heatmap and histogram never
    call this: their tooltip's own measure is not y (heatmap's color
    channel, histogram's VL-computed count), so each votes independently at
    its own call site instead.
    """
    if y is None:
        return []
    fields = [y] if isinstance(y, str) else y
    values: list[float] = []
    for field in fields:
        values.extend(_column_numeric_values(data, field))
    return values


# The five families plan_cartesian serves (histogram reuses BarChart). Not
# ``Chart`` — that wider union also covers non-cartesian families (kpi,
# table, callout, ...) that inherit BaseModel directly rather than the
# shared _CartesianChartFields base _channels_for requires. Every member
# here declares its own ``style``/``x_label``/``y_label`` field, so plan_cartesian
# reads them straight off ``normalized`` instead of taking them as separate
# parameters.
_PlanChart = BarChart | LineChart | AreaChart | ScatterChart | HeatmapChart


class _StyleTail(TypedDict):
    """The ``ResolvedXxxStyle`` kwargs every cartesian family splats in verbatim."""

    tooltip_format: str
    axis_x: ResolvedAxisStyle
    axis_y: ResolvedAxisStyle


@dataclass(frozen=True)
class CartesianPlan:
    """Output of the shared cartesian prelude, before family-specific work.

    ``primary`` stays ``Any``, deliberately not narrowed to
    ``_CartesianChartStyle | None`` (the type ``_extract_axis_overrides``
    already uses for this same value): four shared helpers every family
    calls with ``primary``
    (``_effective_palette``, ``_effective_requested_alias_palette``,
    ``_resolved_series_label``, ``_effective_single_series_fill``) instead
    take ``_HasColor | None``, and pyright's Protocol matching for a mutable
    attribute is invariant — ``_CartesianChartStyle.color: ColorStyle | None``
    does not satisfy ``_HasColor.color: ColorStyle | StaticGradientColorStyle
    | None``, even though every real value trivially would. Narrowing
    ``_HasColor`` would under-type pie/geo's own callers (which need the
    wider union); widening ``_CartesianChartStyle.color`` reverses a
    deliberate, documented narrowing on the theme model itself (see the
    ``color`` field override comment on ``_chart_base.py``'s
    ``_PaintedChartStyleBaseAllOptional`` stub). Both are theme/style-model changes
    outside this struct's one field.
    """

    primary: Any
    channels: dict[str, ResolvedStyleChannel]
    ax_merged: AxisXStyle
    ay_merged: AxisYStyle
    ax_band_position: float | None
    ay_band_position: float | None
    ay_format_authored: bool
    ay_format_is_alias: bool
    ay_format_raw: str | None


def plan_cartesian(
    normalized: _PlanChart,
    data: ChartRows,
    chart_style_context: ChartStyleContext,
    chart_type: str,
    x_channel_type: str,
    y_channel_type: str,
    multiples: MultiplesConfig | None,
    y: str | list[str] | None,
    *,
    has_quantitative_axis: bool,
) -> CartesianPlan:
    """Run the shared cartesian prelude: channels, axis bake.

    ``x_channel_type``/``y_channel_type``/``multiples``/``y`` are
    family-supplied rather than read off ``normalized`` here: heatmap
    hardcodes both channel types to "nominal" instead of classifying the
    data, and histogram passes ``multiples=None, y=None`` regardless of what
    its (reused ``BarChart``-typed) ``normalized.multiples``/``.y`` carry —
    both are family decisions, not branches inside this function.

    ``has_quantitative_axis`` is likewise family-supplied: heatmap passes
    False because both its axes are nominal, so its style patch structurally
    has no ``axis_quantitative`` field to read (see
    ``_QuantitativeAxisChartStyleMixin`` in ``compile/models/style/theme/
    _chart_base.py``). Every other cartesian family keeps the default.
    """
    primary = _with_color_tokens(normalized.style, chart_style_context)
    channels = _channels_for(normalized, data)
    quantitative = (
        primary.axis_quantitative
        if has_quantitative_axis and primary is not None
        else None
    )
    axis_overrides = _extract_axis_overrides(primary, quantitative)
    (
        ax_merged,
        ay_merged,
        ax_band_position,
        ay_band_position,
        ay_format_authored,
        ay_format_is_alias,
        ay_format_raw,
    ) = _bake_cartesian_axes(
        chart_style_context,
        normalized,
        chart_type,
        x_channel_type,
        y_channel_type,
        axis_overrides,
        multiples=multiples,
        y=y,
    )
    return CartesianPlan(
        primary=primary,
        channels=channels,
        ax_merged=ax_merged,
        ay_merged=ay_merged,
        ax_band_position=ax_band_position,
        ay_band_position=ay_band_position,
        ay_format_authored=ay_format_authored,
        ay_format_is_alias=ay_format_is_alias,
        ay_format_raw=ay_format_raw,
    )


def build_cartesian_axes(
    chart_id: str,
    chart_style_context: ChartStyleContext,
    ax_merged: AxisXStyle,
    ay_merged: AxisYStyle,
    ax_band_position: float | None,
    ay_band_position: float | None,
    ax_edge: Literal["left", "right"] | None,
    ay_format_authored: bool,
    ay_format_is_alias: bool,
    ticks: _CartesianTickResolution,
    column_forming: bool,
    measure_tooltip_format: str | None,
    tooltip_format_values: Iterable[float | None],
    zero_anchor: bool,
    endpoint_rail_may_discard_domain: bool,
    ax_is_quantitative: bool,
    ay_is_quantitative: bool,
    ay_quantitative_for_alignment: bool | None = None,
    ay_format_raw: str | None = None,
) -> tuple[ResolvedAxisStyle, _StyleTail]:
    """Run the shared cartesian postlude: the resolved y-axis + the style tail.

    ``ax`` (the resolved x-axis) is not returned: it only ever feeds
    ``style_tail["axis_x"]`` below, and every call site already reads that
    off the tail rather than the axis object directly.

    ``ax_merged``/``ay_merged`` are the family's own local variables: each
    family's final axes, after whatever family-specific mutation
    (orientation, zero-anchor, log-domain) it ran. This function takes no
    ``CartesianPlan``, so no frozen-struct attribute is in reach here. The
    caller's ``plan`` is still live at the call site, though, so passing
    ``plan.ay_merged`` stays expressible and type-clean: each family unpacks
    the merged axes once below ``plan_cartesian()`` and reads them from that
    local thereafter. That single unpack is a convention, not a guarantee the
    signature can enforce.

    ``ax_band_position``/``ay_band_position`` come straight off
    ``CartesianPlan`` at every call site: no family mutates them after the
    bake, so there is no local variable guarding a stale read the way
    ``ax_merged``/``ay_merged`` need one.

    ``ay_format_authored``/``ay_format_is_alias`` are parameters rather than
    read off a shared struct directly: heatmap and histogram discard the
    cascade's real answer and hardcode ``True``/``False`` (neither axis
    carries a real format), while bar/line/area/scatter pass the cascade's
    own values through.

    ``measure_tooltip_format`` folds the two spellings every family used to
    apply separately (a conditional override in bar/line/area, an
    unconditional override with a fallback in scatter) into the one formula
    below — verified to already produce the same value either way, since
    ``_cartesian_style_tail`` bakes that same fallback as its own default.

    ``tooltip_format_values`` is every value the chart's own quantitative
    measure(s) will paint (empty for heatmap/histogram, whose y is nominal
    or has no real column) — passed to ``_cartesian_style_tail`` so its
    fallback tooltip format floors sub-$1 money the same way the
    chart-authored candidate does.

    This is the one place both axes of a cartesian pair are known at once,
    which is why it — not ``build_resolved_axis`` itself — computes
    ``y_gridline_caps_bottom`` for ``ax``: resolving ``axis_x.ticks.visible:
    "auto"`` needs the y-axis's own baked ladder/domain, which a single-axis
    builder structurally can't see.

    ``zero_anchor`` is the same bool each family already computed to call
    ``_resolve_cartesian_ticks`` with (bar's ``bar_zero``, line's
    ``zero_anchored_line``, ...) — passed again here rather than re-derived,
    because ``ay_merged.scale.continuous.zero`` does not always carry it: a
    single-metric line/area/bar anchors at zero via ``BaselineFeature``'s
    render-time ``datum: 0`` rule, not a resolve-time bake, so the scale
    field alone would under-detect the zero-anchored case.

    ``endpoint_rail_may_discard_domain`` says whether this axis's baked
    ``domain_min`` might be silently dropped at render time by the
    endpoint-label rail's shared-scale composition (see
    ``_y_domain_floor``'s own docstring) — only area's multi-series,
    non-stacked path can be True; every other family/shape passes False.

    ``ax_is_quantitative``/``ay_is_quantitative`` are the real channel-type
    fact for each axis — is the field it draws numeric or categorical —
    published verbatim onto ``ResolvedAxisStyle.is_quantitative``. Every
    family must state both explicitly: heatmap's are both False (nominal
    x and y), a plain bar/line/area/histogram's y is always True (the
    measure field, regardless of bar's ``orientation``), and scatter's
    mirror its own per-axis channel classification.

    ``ay_quantitative_for_alignment`` overrides ``ay_is_quantitative`` for
    the digit-alignment gate only (``build_resolved_axis``'s
    ``_force_right``) — needed exactly once, by a horizontal bar: its
    measure axis is quantitative by channel type (``ay_is_quantitative=
    True``) but renders on VL's x channel rather than the column-forming
    axis the alignment gate is about, so bar passes the orientation-derived
    bool here instead. Every other family leaves this ``None`` — the two
    questions coincide there, so ``ay_is_quantitative`` alone answers both.

    ``ay_format_raw`` is the cascade's raw pre-resolve value-axis format
    (``CartesianPlan.ay_format_raw``, see ``_bake_cartesian_axes``), passed
    straight to ``build_resolved_axis``. Heatmap/histogram leave it at the
    default ``None`` along with their hardcoded ``ay_format_authored`` —
    neither axis carries a real cascade-authored format for this to describe.
    """
    ax = build_resolved_axis(
        ax_merged,
        band_position=ax_band_position,
        edge=ax_edge,
        format_authored=True,
        format_is_alias=False,
        is_quantitative=ax_is_quantitative,
        chart_id=chart_id,
        y_gridline_caps_bottom=_y_gridline_caps_bottom(
            ay_merged, ticks, zero_anchor, endpoint_rail_may_discard_domain
        ),
    )
    ay = build_resolved_axis(
        ay_merged,
        band_position=ay_band_position,
        edge=_edge_or_none(ay_merged.position),
        tick_values=ticks.ticks,
        format_authored=ay_format_authored,
        format_is_alias=ay_format_is_alias,
        format_authored_raw=ay_format_raw,
        is_quantitative=ay_is_quantitative,
        quantitative_for_alignment=ay_quantitative_for_alignment,
        zero_anchored=zero_anchor,
        chart_id=chart_id,
        column_forming=column_forming,
        domain_max=ticks.domain_max if ticks.domain_max is not None else ...,
        domain_min=ticks.domain_min if ticks.domain_min is not None else ...,
        # The tick-stub geometry rule resolves only on the x-axis (see the
        # `ax` build above) -- axis_y's own `ticks.visible` is never "auto".
        y_gridline_caps_bottom=None,
    )
    tail = _cartesian_style_tail(chart_style_context, ax, ay, tooltip_format_values)
    style_tail: _StyleTail = {
        "tooltip_format": measure_tooltip_format or tail["tooltip_format"],
        "axis_x": tail["axis_x"],
        "axis_y": tail["axis_y"],
    }
    return ay, style_tail
