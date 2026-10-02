"""Theme-stage style classes: axis, scale, tooltip."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from dbt_charts.core.compile.models.markers import (
    Color,
    Format,
    InheritSlot,
    Merge,
    SkipInheritSlots,
    Strategy,
)
from dbt_charts.core.compile.models.schema_names import FormatAlias

_TIME_UNIT_LITERAL = Literal[
    "auto",
    "year",
    "yearquarter",
    "yearmonth",
    "yearweek",
    "yearmonthdate",
    "monthofyear",
    "dayofweek",
    "dayofmonth",
    "dayofyear",
    "hourofday",
    "none",
]


class AxisLabelOverlapConfig(BaseModel):
    """Two-bool overlap strategy enablement for x-axis labels.

    Each bool enables (True) or disables (False) a strategy step.
    None means "not authored — inherit from the theme cascade."
    The resolver applies enabled strategies in fixed order: skip → tilt.
    On bucketed temporal axes, skip advances label visibility by one meaningful
    calendar period while preserving the encoding and label format time units.
    Other temporal axes use parity skip. Categorical axes never skip because
    removing a category label loses domain information; they only tilt.

    Attributes:
        tilt: Walk tilt_increments and apply the first angle that fits.
        skip: Thin temporal labels; ignored on categorical axes.

    Example YAML:
        overlap:
          tilt: true
          skip: true
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    tilt: bool | None = Field(
        default=None,
        description="Enable label tilt strategy; None inherits from parent axis.",
    )
    skip: bool | None = Field(
        default=None,
        description="Enable temporal label thinning; ignored on categorical axes; None inherits from parent axis.",
    )


from dbt_charts.core.compile.models.primitives import (
    FontStyle,
    FormatConfig,
)
from dbt_charts.core.compile.models.style.theme.board import (
    PaddingStyle,
)


# Nested under the parent grid style's `threshold` field.
class AxisGridThresholdStyle(BaseModel):
    """Threshold-rule color, width, and visibility overrides.

    Styles the heavy rule drawn at a quantitative axis's meaningful crossing
    point — zero on most quantitative axes, 1.0 on a ratio/percent axis — the
    rule shared by the zero-anchor line, the unity (100%) line, and the
    normalize-stack top lines.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # All fields nullable — cascade fills from parent axis into axis_x/y/quantitative.
    visible: bool | None = Field(
        default=None,
        description="Show the threshold rule; None inherits from parent axis.",
    )
    color: Annotated[str | None, Color()] = Field(
        default=None,
        description="Color of the threshold rule; None inherits from parent axis.",
    )
    width: float | None = Field(
        default=None,
        description="Width of the threshold rule in pixels; None inherits from parent axis.",
    )


# `threshold` has no visible effect on an axis that never ticks at the
# threshold (a band axis, or a categorical x-axis), but the model can't
# reject that combination statically — the x slot is authored the same way
# whether x classifies categorical or quantitative, and that classification
# is only known later, at resolve time.
class BaseAxisGridStyle(BaseModel):
    """Grid line style for all axis variants.

    ``threshold`` styles the heavy rule at a quantitative axis's meaningful
    crossing point — zero on most quantitative axes, 1.0 on a ratio/percent
    axis. It applies to any quantitative axis and has no visible effect on a
    categorical or band axis.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # All fields are nullable so the cascade can fill missing values from parent
    # `axis` into `axis_x` / `axis_y` / `axis_quantitative`.
    visible: bool | None = Field(
        default=None, description="Show grid lines; None inherits from parent axis."
    )
    opacity: float | None = Field(
        default=None, description="Grid line opacity; None uses Vega-Lite's default."
    )
    width: float | None = Field(
        default=None,
        description="Grid line width in pixels; None uses Vega-Lite's default.",
    )
    color: Annotated[str | None, Color()] = Field(
        default=None, description="Grid line color; None uses Vega-Lite's default."
    )
    # SkipInheritSlots: dash is not inherited — axis family variants each own their dash independently.
    dash: Annotated[
        list[float] | None, SkipInheritSlots(), Merge(Strategy.OVERRIDE)
    ] = Field(
        default=None,
        description="Dash pattern for grid lines; None renders a solid line.",
    )
    # Threshold sub-block. None means "no override at this level"; the
    # cascade fills individual threshold.* fields from the parent axis.
    # SkipInheritSlots(cascade=True): apply_inherit copies the entire
    # threshold object when the child axis has threshold=None; fills
    # individual None fields when partial.
    threshold: Annotated[
        AxisGridThresholdStyle | None, SkipInheritSlots(cascade=True)
    ] = Field(
        default=None,
        description="Threshold-rule gridline style; None inherits from parent axis.",
    )


class AxisLineStyle(BaseModel):
    """Axis domain/baseline line style. Renamed from ``AxisDomainStyle``:
    same 3 fields, no shape change; ``domain`` collided too easily with
    scale-domain terminology."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Nullable so the cascade can fill missing values from parent `axis`.
    visible: bool | None = Field(
        default=None,
        description="Show the axis domain line; None inherits from parent axis.",
    )
    width: float | None = Field(
        default=None,
        description="Domain line width in pixels; None uses Vega-Lite's default.",
    )
    color: Annotated[str | None, Color()] = Field(
        default=None, description="Domain line color; None uses Vega-Lite's default."
    )


# Declared on BaseAxisStyle.ticks, so this is the tick config for axis_y,
# axis_quantitative and axis_band as well as the `axis` baseline; only axis_x
# narrows it (to DimensionTicksStyle, which adds time_unit). Cadence authored
# through any of those slots is resolved and gated per axis at render:
# render/chart/type_inference.py's apply_x_tick_cadence for x, and
# _bake_cartesian_axes for the axis_y rejection.
class AxisTicksStyle(BaseModel):
    """Tick marks on an axis: visibility, color, size, and cadence.

    Every axis slot uses this shape except axis_x, which adds ticks.time_unit
    for calendar cadence. Which cadence fields actually apply depends on the
    axis: see the count and step descriptions below.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Nullable so the cascade can fill `visible` from parent `axis`. "auto"
    # (the tick-stub geometry rule) is axis_x-only -- see DimensionTicksStyle's
    # own override below, which is the one slot that actually resolves it.
    visible: bool | None = Field(
        default=None,
        description="Show axis ticks; None inherits from parent axis.",
    )
    color: Annotated[str | None, Color()] = Field(
        default=None, description="Tick color; None uses Vega-Lite's default."
    )
    length: float | None = Field(
        default=None,
        description="Tick length in pixels; None uses Vega-Lite's default.",
    )
    width: float | None = Field(
        default=None,
        description="Tick stroke width in pixels; None uses Vega-Lite's default.",
    )
    offset: float | None = Field(
        default=None,
        description="Pixel offset of ticks from their default position; None means no offset.",
    )
    # SkipInheritSlots: count is not inherited — axis family variants each set their own count.
    count: Annotated[int | None, SkipInheritSlots()] = Field(
        default=None,
        description=(
            "Target number of axis ticks: a target everywhere, never an exact "
            "count. On the measure axis (axis_y) the renderer computes an "
            "explicit round-numbered ladder of at most this many ticks. On "
            "axis_x it passes through as VL's axis.tickCount: a temporal scale "
            "honors it closely, a quantitative one rounds to a nearby "
            "round-numbered ladder. To name the interval instead of the "
            "count, author ticks.step on a quantitative axis_x. An ordinal "
            "axis_x has no tick-count concept and ignores this."
        ),
    )
    # SkipInheritSlots: step is not inherited — axis family variants each set their own.
    step: Annotated[int | None, SkipInheritSlots()] = Field(
        default=None,
        description=(
            "Tick interval on axis_x. Alongside ticks.time_unit it is a "
            "multiple of that calendar grain (time_unit: year, step: 5 -> a "
            "tick every 5 years). On its own it is a numeric interval for a "
            "quantitative axis (step: 1000 -> a tick every 1000), and acts as "
            "a floor rather than a fixed ladder, so the axis keeps covering "
            "the data as its range grows. A bare step on an axis that is not "
            "quantitative is an error, not a no-op; axis_y rejects step "
            "entirely (set ticks.count there instead)."
        ),
    )


class DimensionTicksStyle(AxisTicksStyle):
    """axis_x-only: adds the calendar unit that anchors a step cadence.

    Distinct from ``DimensionLabelStyle.time_unit`` (label-cadence override)
    and ``AxisXStyle.time_unit`` (bucketing grain) — three different
    time_unit fields, three different meanings.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Override base's `visible` type: "auto" (the tick-stub geometry rule) is
    # meaningful only on the bottom axis -- build_cartesian_axes resolves it
    # from the y-axis's own baked ladder/domain, which axis_y/axis_quantitative/
    # axis_band have no equivalent question for.
    visible: bool | Literal["auto"] | None = Field(  # type: ignore[assignment]  # type-state: type_ignore — deliberate widening of AxisTicksStyle.visible on axis_x's own tick slot only; mirrors the accepted _GeoChartStyle.color override pattern in _chart_base.py
        default=None,
        description=(
            'Show axis ticks. "auto" shows the tick only where the '
            "gridlines can't reach all the way to the labels on their own; "
            "None inherits from parent axis."
        ),
    )
    time_unit: _TIME_UNIT_LITERAL | None = Field(
        default=None,
        description="Step-anchored tick cadence unit; None disables step-anchored ticks.",
    )

    @model_validator(mode="after")
    def _validate_cadence(self) -> DimensionTicksStyle:
        # A bare `step` is deliberately NOT rejected here: it is the
        # quantitative-axis cadence lever (VL's tickMinStep), and whether this
        # axis resolves to quantitative, temporal, or ordinal is a
        # data-dependent answer no model validator has. The gate lives in
        # render/chart/type_inference.py's apply_x_tick_cadence, which every
        # cartesian x path routes through.
        if self.time_unit is not None and self.count is not None:
            raise ValueError(
                "ticks.time_unit and ticks.count cannot both be set — they "
                "are two different ways to express tick cadence. Use "
                "ticks.time_unit/step for a step-anchored cadence (every N "
                "years/quarters/months), or ticks.count for VL's advisory "
                "tick-count hint, not both."
            )
        return self


class AxisTitleStyle(BaseModel):
    """Axis title typography, deliberately thin. A title is one short static
    string, not a per-tick label stream, so it doesn't need the label-overlap
    strategy machinery or VL passthrough knobs (bound/flush/offset/line_height)
    that only make sense for a repeated per-tick label. padding is kept
    (unlike those) since the theme authors a real, non-VL-default value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    font: Annotated[FontStyle, InheritSlot(from_path="Style.charts.font")] = Field(
        default_factory=FontStyle, description="Axis title font style overrides."
    )
    padding: float | None = Field(
        default=None,
        description="Padding between axis title and labels in pixels; None uses Vega-Lite's default.",
    )
    angle: float | None = Field(
        default=None,
        description="Title rotation angle in degrees; None uses Vega-Lite's default.",
    )
    # Same left/right/center/inward/outward vocabulary as AxisLabelStyle.align
    # — resolved through the identical _resolve_own_side_align machinery.
    align: Literal["left", "right", "center", "inward", "outward"] | None = Field(
        default=None,
        description=(
            "Horizontal text alignment of the title. 'left'/'right'/'center' "
            "are absolute. 'inward'/'outward' resolve against the axis's own "
            "left/right edge, same as label.align; a no-op on an axis with no "
            "left/right edge. None uses Vega-Lite's default."
        ),
    )
    visible: bool | None = Field(
        default=None,
        description=(
            "Show the axis title; None uses Vega-Lite's default (title shown). "
            "An explicit value you set, on the board or on a single chart, "
            "wins over the title an authored x_label/y_label would force on."
        ),
    )


class AxisLabelStyle(BaseModel):
    """Axis label: font + padding + VL-passthrough.

    This is the universal base — fields that are meaningful on both dimension
    and measure axes. Dimension-only fields (tilt_increments, values,
    time_unit) live on DimensionLabelStyle, which is used for AxisXStyle.labels.
    expr stays here: it is universal and read by time_unit_detect.py on both
    axes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    font: Annotated[FontStyle, InheritSlot(from_path="Style.charts.font")] = Field(
        default_factory=FontStyle, description="Axis element font style overrides."
    )
    padding: float | None = Field(
        default=None,
        description="Padding between axis labels and ticks in pixels; None inherits from parent axis.",
    )
    max_width: float | None = Field(
        default=None,
        description="Maximum label width in pixels; None uses Vega-Lite's default (180px).",
    )
    angle: float | None = Field(
        default=None,
        description="Label rotation angle in degrees; None uses Vega-Lite's default.",
    )
    # left/right/center are absolute (edge-independent) — passed through as
    # authored. inward/outward are relative to the axis's own final
    # left/right edge — resolved to concrete left/right at resolve time (see
    # build_resolved_axis's edge parameter and _resolve_own_side_align); on a
    # bottom/top axis (no left/right edge) they collapse to None (the
    # default alignment), not an error.
    align: Literal["left", "right", "center", "inward", "outward"] | None = Field(
        default=None,
        description=(
            "Horizontal text alignment of labels. 'left'/'right'/'center' are "
            "absolute. 'inward' hugs the plot (own-side: left on a left axis, "
            "right on a right axis); 'outward' hugs away from the plot "
            "(Vega-Lite's default growth direction). inward/outward are a "
            "no-op on an axis with no left/right edge. None uses Vega-Lite's "
            "default."
        ),
    )
    # Two-bool struct enabling/disabling each overlap strategy.
    # None = not authored — inherit the parent axis's setting from the cascade.
    # SkipInheritSlots(cascade=True): if overlap is None on the child axis,
    # the whole struct is copied from the parent; if present, individual None
    # fields are filled from the parent (enabling partial authoring like
    # `overlap: { tilt: false }` while inheriting skip).
    overlap: Annotated[
        AxisLabelOverlapConfig | None,
        SkipInheritSlots(cascade=True),
    ] = Field(
        default=None,
        description=(
            "Label overlap strategy enablement. None inherits from the theme cascade. "
            "Set individual bools to enable/disable; they are applied in fixed "
            "order skip→tilt. On bucketed temporal axes, skip thins label visibility "
            "to a coarser calendar period without changing the axis or label time unit."
        ),
    )
    min_gap: float | None = Field(
        default=None,
        description="Minimum pixel gap between labels; None uses Vega-Lite's default (0px).",
    )
    visible: bool | None = Field(
        default=None,
        description="Show axis labels; None uses Vega-Lite's default (labels shown).",
    )
    # When None and the parent axis has a temporal time_unit, the render layer
    # fills in a smart conditional labelExpr — see render/chart/time_unit_detect.py.
    # Kept on the base (not just DimensionLabelStyle) — both axes may use custom
    # label expressions, e.g. for axis_y mirror overrides via AxisMirrorStyle.expr.
    expr: str | None = Field(
        default=None,
        description="Custom Vega expression for label text; None uses smart temporal defaults when applicable.",
    )
    # Positioning knobs — all None means VL per-axis defaults apply.
    bound: bool | float | None = Field(
        default=None,
        description="Hide labels that overflow the axis range; None uses Vega-Lite's default.",
    )
    flush: bool | float | None = Field(
        default=None,
        description="Align first/last label flush with the scale range; None uses Vega-Lite's default.",
    )
    offset: float | None = Field(
        default=None,
        description="Pixel offset of the label from its tick anchor; None uses Vega-Lite's default.",
    )
    format: Annotated[FormatAlias | str | FormatConfig | None, Format()] = Field(
        default=None, description="Tick value format string; None uses auto-format."
    )


class DimensionLabelStyle(AxisLabelStyle):
    """AxisLabelStyle + dimension-axis-only label fields.

    Used only by AxisXStyle.labels. Fields here are structurally absent on
    AxisYStyle.labels so the model rejects them at validation time rather than
    requiring a scattered runtime check.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Optional label cadence for temporal axes. None inherits from the parent
    # axis time_unit, except yearweek defaults to month labels.
    time_unit: _TIME_UNIT_LITERAL | None = Field(
        default=None,
        description="Label cadence for temporal axes; None inherits from the parent axis time_unit.",
    )
    # Sub-day clock register — the only new authoring key the time-notation
    # vocabulary adds (no separate register enum). The house theme default
    # (12: the Noon/Midnight vocabulary, not the meridiem-free 24-hour clock)
    # lives in the theme cascade (_base.yaml's axis_x.labels.clock), not as a
    # Python literal here — None on this field means "not authored at this
    # layer", same as every other passthrough field. See
    # ``time_unit_detect.default_subday_label_expr_for``, which reads this
    # field on a genuinely continuous (non-bucketed) temporal x-axis.
    clock: Literal[24, 12] | None = Field(
        default=None,
        description=(
            "Sub-day clock register for a continuous temporal x-axis: 24 for "
            "the unambiguous, meridiem-free 24-hour clock, or 12 for the "
            "12-hour clock with the Noon/Midnight word vocabulary. None "
            "leaves the value to the theme cascade."
        ),
    )
    # Descending tilt ladder consulted by the "tilt" strategy on discrete x-axes.
    # The picker walks this list and returns the first angle whose label widths
    # fit at the chart's drawable width; fall-through uses the last entry (steepest).
    # None means tilt is disabled (used on y-axis and any axis the resolver doesn't target).
    tilt_increments: Annotated[list[float] | None, Merge(Strategy.OVERRIDE)] = Field(
        default=None,
        min_length=1,
        description="Descending tilt angles for label overlap resolution on discrete x-axes; None disables tilt.",
    )
    # Decouples LABEL density from tick/grid density on temporal x-axes. This
    # is a filter, not a tick list: it does not add or remove ticks. Ticks and
    # gridlines are produced exactly as they would be without this field —
    # from ``axis_x.scale.values`` if authored, else the auto-fill default.
    # Each of those ticks then gets its label text kept only if its date
    # appears in this list; every other tick keeps its position/gridline but
    # its label is blanked. A date here that doesn't match any actual tick is
    # a silent no-op (nothing exists to unblank). To change tick/grid density
    # itself, author ``axis_x.scale.values``. None labels every tick per the
    # existing smart cadence/format. Temporal x-axes only — the render layer
    # raises when authored on a non-temporal axis, on axis_y (the measure
    # axis is never temporal in dbt charts' model), or on a bar chart whose
    # categorical axis renders horizontal (that axis is always nominal,
    # regardless of orientation being forced or auto-inferred).
    values: Annotated[list[Any] | None, Merge(Strategy.OVERRIDE)] = Field(
        default=None,
        min_length=1,
        description=(
            "Dates to keep label text on, chosen from among the axis's ticks "
            "(whatever axis_x.scale.values or the auto-fill cadence already "
            "produced): this filters which ticks show text, it does not "
            "add or remove ticks. Every tick not in this list keeps its "
            "position and gridline but has its label blanked. Set "
            "axis_x.scale.values separately to change tick/grid density itself. "
            "Temporal x-axes only; not supported on a horizontal bar's "
            "categorical axis. None labels every tick as usual."
        ),
    )


def _is_iso_date_string(value: str) -> bool:
    """True when ``value`` parses as an ISO-8601 date or datetime string.

    Delegates to ``fromisoformat``, so the accepted grammar is Python-version
    dependent for exotic forms (e.g. ISO week dates parse on 3.11+ only).
    The common domain-bound shapes — ``YYYY-MM-DD``, ``YYYY-MM-DDTHH:MM:SS``
    with optional offset or trailing ``Z`` — validate identically on both CI
    lanes; the ``Z`` suffix is normalized upfront because Python <3.11
    rejects it.
    """
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt.date.fromisoformat(value)
        return True
    except ValueError:
        pass
    try:
        dt.datetime.fromisoformat(value)
        return True
    except ValueError:
        return False


def _coerce_list_to_tuple(
    value: object,  # type-state: object_annotation — BeforeValidator input, any raw value
) -> object:  # type-state: object_annotation — mirrors the input's object type
    """YAML has no tuple literal -- every authored ``domain:`` arrives as a
    ``list``. Normalize it to a ``tuple`` before the field's own strict
    tuple-arity validation runs, so that validation can reject every other
    iterable (a set, a deque, a generator) as a real shape error instead of
    silently reordering or truncating it."""
    return tuple(value) if isinstance(value, list) else value


class ScaleDomainValidationMixin(BaseModel):
    """Shared authored-input validator for scale domain type-consistency.

    Inherited by both ``ScaleContinuousStyle`` and its generated Patch so
    chart-local authored patches enforce the same domain rules as
    theme-stage input — ``build_patch_model_ext`` only carries over
    validators that live on the patch model's base class.

    The 2-element shape (a fixed ``tuple``, not a variable-length ``list``),
    per-bound ``int | float | str`` typing (which also rejects a ``None``
    element — a domain bound may not be null), and the
    bool-is-not-a-domain-bound exclusion (``bool`` is an ``int`` subclass, so
    the numeric arms use ``Field(strict=True)``) are all enforced by the
    ``domain`` field's own annotation. The constraints must sit on an
    *inner* ``Annotated`` layer (wrapping ``tuple[...]``), not on the
    field's own outer ``Field(...)`` call: ``build_patch_model_ext`` copies
    a field's raw ``annotation`` into the generated Patch verbatim, but
    rebuilds its ``FieldInfo`` from scratch, forwarding only
    ``description`` plus a fixed set of marker types
    (``Inherit``/``InheritSlot``/``SkipInheritSlots``/``Merge``/``Facet``);
    an outer ``Field(min_length=..., strict=...)`` kwarg lives on the
    outer ``FieldInfo`` and is silently dropped for the Patch, while a
    constraint nested inside the annotation survives untouched.

    This mixin covers what the type can't: when the scale type is
    ``temporal``, both bounds must additionally be ISO-8601 date/datetime
    strings, a cross-field check against the sibling ``type`` field. A
    ``None`` domain — the field unset — is always valid; a ``None``
    *element* is not.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _validate_domain_temporal_type(
        cls,
        data: object,  # type-state: object_annotation — pydantic "before" validator; raw pre-validation input, shape not yet known
    ) -> object:  # type-state: object_annotation — mirrors the "before" validator's required return type
        if not isinstance(data, dict):
            return data
        domain = data.get("domain")
        if domain is None or data.get("type") != "temporal":
            return data
        if not isinstance(domain, (list, tuple)):
            # The field annotation's strict tuple check (after
            # _coerce_list_to_tuple) already rejects any non-list/tuple
            # domain -- but that's per-field validation, which runs after
            # this model-level "before" validator returns. Without this
            # guard, `for value in domain` below would either crash with a
            # raw (non-ValidationError) TypeError on a non-iterable domain
            # (an int, a float), or -- for an iterable like a set -- report
            # the generic ISO-string error instead of a message that names
            # the actual problem (wrong container, not wrong element type).
            raise ValueError(
                f"scale.domain must be a list of 2 ISO-8601 date/datetime "
                f"strings when scale.type: temporal, got {type(domain).__name__}."
            )
        for value in domain:
            if not isinstance(value, str) or not _is_iso_date_string(value):
                raise ValueError(
                    f"scale.domain element {value!r} is not an ISO-8601 "
                    "date/datetime string, but scale.type: temporal "
                    "requires both domain bounds to be dates (e.g. "
                    "'1955-01-01')."
                )
        return data


class ScaleLogStyle(BaseModel):
    """Log-scale-only param."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base: float | None = Field(
        default=None,
        description="Log base; only meaningful with type: log. None lets Vega-Lite apply its own default (10).",
    )


class ScalePowStyle(BaseModel):
    """Power-scale-only param."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    exponent: float | None = Field(
        default=None,
        description="Power exponent; only meaningful with type: pow. None uses Vega-Lite's default.",
    )


class ScaleSymlogStyle(BaseModel):
    """Symlog-scale-only param."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    constant: float | None = Field(
        default=None,
        description="Symlog constant; only meaningful with type: symlog. None uses Vega-Lite's default.",
    )


# Carries two validators moved from the old flat BaseScaleStyle:
# - ScaleDomainValidationMixin: the domain temporal-type check (applies to
#   both this class and its generated Patch via build_patch_model_ext).
# - _validate_log_pow_symlog_params: defined DIRECTLY here, NOT on a mixin —
#   this validator fires on the fully-merged result; it must NOT carry into
#   the generated Patch since a theme layer may set type: log and a
#   chart-local patch layer may set only base. The cross-field check fires
#   once, on the merged result.
class ScaleContinuousStyle(ScaleDomainValidationMixin):
    """Continuous-scale-only config: type, domain, zero-baseline, log/pow/symlog params."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    zero: bool | Literal["auto"] | None = Field(
        default=None,
        description=(
            'Force scale zero-baseline: True/False pins it; "auto" runs the '
            "dbt charts smart-zero heuristic; None passes through to Vega-Lite."
        ),
    )
    type: Literal["linear", "log", "pow", "sqrt", "symlog", "temporal"] | None = Field(
        default=None,
        description=(
            "Scale type override. 'linear'/'log'/'pow'/'sqrt'/'symlog' pass "
            "straight through to Vega-Lite's quantitative scale.type. "
            "'temporal' is a dbt charts escape hatch for a cartesian x-axis: it "
            "forces a continuous temporal scale instead of the auto-inferred "
            "ordinal/nominal bucketed type, which is required before an "
            "authored ``domain`` of ISO dates can extend the visible range "
            "past the data extent. None lets dbt charts/Vega-Lite infer from "
            "the field type."
        ),
    )
    domain: Annotated[
        Annotated[
            # A domain is structurally [low, high] -- a fixed 2-tuple, not
            # an arbitrary-length list. strict=True on the numeric arms
            # excludes bool (an int subclass; lax mode would otherwise
            # silently rewrite an authored `true` to `1`).
            tuple[
                Annotated[int, Field(strict=True)]
                | Annotated[float, Field(strict=True)]
                | str,
                Annotated[int, Field(strict=True)]
                | Annotated[float, Field(strict=True)]
                | str,
            ],
            # strict=True on the tuple itself too -- domain order is
            # semantic ([low, high]), so pydantic's lax mode silently
            # coercing a set/frozenset/deque/generator into a tuple (in
            # arbitrary order for a set) would silently corrupt it rather
            # than reject it. _coerce_list_to_tuple runs first so a real
            # authored (YAML) list still reaches this strict check as a
            # tuple, since YAML itself has no tuple literal.
            Field(strict=True),
            BeforeValidator(_coerce_list_to_tuple),
        ]
        | None,
        Merge(Strategy.OVERRIDE),
    ] = Field(
        default=None,
        description=(
            "Explicit [low, high] scale domain; None lets Vega-Lite "
            "auto-determine from data. Must have exactly 2 elements: "
            "dbt charts only wires a continuous range override through to "
            "Vega-Lite, not an explicit category enumeration. Neither element "
            "may be null; pin both bounds, or omit the key to fit the data. "
            "When ``type: temporal`` is set, both elements must be ISO-8601 "
            "date/datetime strings."
        ),
    )
    log: ScaleLogStyle | None = Field(
        default=None,
        description="Log-scale param (base); only meaningful with type: log.",
    )
    pow: ScalePowStyle | None = Field(
        default=None,
        description="Power-scale param (exponent); only meaningful with type: pow.",
    )
    symlog: ScaleSymlogStyle | None = Field(
        default=None,
        description="Symlog-scale param (constant); only meaningful with type: symlog.",
    )

    # NOT on ScaleDomainValidationMixin: unlike the domain temporal-type check,
    # this pairing can legitimately span two cascade layers (a theme sets the
    # log type, a chart overlay sets only log.base) — merge_onto_base
    # re-validates the fully-merged
    # ScaleContinuousStyle via model_validate, so this fires on the merged
    # result, not on an isolated authored patch fragment (the generated Patch
    # does not inherit it — it is defined directly here, not on a mixin).
    @model_validator(mode="after")
    def _validate_log_pow_symlog_params(self) -> ScaleContinuousStyle:
        if self.log is not None and self.log.base is not None and self.type != "log":
            raise ValueError(
                "scale.continuous.log.base is only meaningful with type: log — "
                "set axis_y.scale.continuous.type: log, or remove log.base."
            )
        if (
            self.pow is not None
            and self.pow.exponent is not None
            and self.type != "pow"
        ):
            raise ValueError(
                "scale.continuous.pow.exponent is only meaningful with type: pow "
                "— set axis_y.scale.continuous.type: pow, or remove pow.exponent."
            )
        if (
            self.symlog is not None
            and self.symlog.constant is not None
            and self.type != "symlog"
        ):
            raise ValueError(
                "scale.continuous.symlog.constant is only meaningful with type: "
                "symlog — set axis_y.scale.continuous.type: symlog, or remove "
                "symlog.constant."
            )
        if self.type == "log" and self.zero is True:
            raise ValueError(
                "scale.continuous.type: log is incompatible with zero: true — a log "
                "domain cannot include 0. Remove zero or drop the log scale."
            )
        return self


class BaseScaleStyle(BaseModel):
    """Universal + continuous-only scale config.

    Used at ``axis_y.scale`` (y-axis scale overrides) and as the base for
    ``XScaleStyle`` (``axis_x.scale``). band/point/quantize/mark_size scale
    config was deleted (2026-08 trim): no chart currently needs it, and it
    more than doubled the authoring surface for zero real use. Rely on
    Vega-Lite's own defaults until a real need for those knobs shows up.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    round: bool | None = Field(
        default=None,
        description="Round scale outputs to nearest integer; None uses Vega-Lite's default (no rounding).",
    )
    clamp: bool | None = Field(
        default=None,
        description="Clamp values to scale domain; None uses Vega-Lite's default (no clamping).",
    )
    nice: bool | None = Field(
        default=None,
        description=(
            "Round the axis domain to nice values; None uses Vega-Lite's "
            "default. Forwards natively to Vega-Lite's scale.nice."
        ),
    )
    # Unified shortcut. Maps to VL ``scale.padding``, which Vega-Lite itself
    # dispatches per scale type: bandPaddingOuter for band, pointPadding for
    # point, continuousPadding for continuous. Note that VL interprets this
    # in pixels for continuous scales but as a fraction (0..1) for band/point.
    padding: float | None = Field(
        default=None,
        description="Unified scale padding shortcut; dispatches to band, point, or continuous padding per scale type.",
    )
    headroom: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Fractional breathing room at measure-axis edges, exact (never nice-rounded). "
            "Zero-anchored axes (bar; line/area/scatter near zero): "
            "domain_max = data_max * (1 + headroom). "
            "Zoomed axes (line/area/scatter far from zero): both edges expand: "
            "domain_max = data_max + headroom * span; "
            "domain_min = data_min - headroom * span. "
            "None inherits the theme default; 0 disables headroom. "
            "Ignored with an explicit `domain` or `stack: normalize`."
        ),
    )
    # Explicit tick values for the scale (not to be confused with
    # labels.values, which is a dimension-only label density filter on
    # DimensionLabelStyle). Moved in from the old BaseAxisStyle.values.
    values: Annotated[list[Any] | None, Merge(Strategy.OVERRIDE)] = Field(
        default=None,
        description="Explicit tick values; None uses Vega-Lite's auto tick values.",
    )
    continuous: ScaleContinuousStyle | None = Field(
        default=None,
        description="Continuous-scale overrides (type, domain, zero, log/pow/symlog params); None means no override.",
    )


class XScaleStyle(BaseScaleStyle):
    """Scale config for axis_x.scale only.

    x_reverse is structurally absent from BaseScaleStyle so the model rejects
    it on axis_y.scale at validation time, closing the silent-leak bug where
    x_reverse on axis_x.scale could reach VL's y-scale via the old flat
    SCALE_FIELD_MAP.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    x_reverse: bool | None = Field(
        default=None,
        description="Reverse the x-axis scale direction; None means no reversal.",
    )


class TooltipSlotStyle(BaseModel):
    """Typography for a single tooltip slot (label or value).

    All font sub-fields are optional; the cascade fills them from theme YAML.
    Constructed with no args is valid — each slot is a FontStyle overlay.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # InheritSlot: tooltip label.font and value.font both fill from charts.font.
    font: Annotated[FontStyle, InheritSlot(from_path="Style.charts.font")] = Field(
        default_factory=FontStyle,
        description="Font overrides for this tooltip slot (color, weight).",
    )


class TooltipBorderStyle(BaseModel):
    """Tooltip box border: all fields required; theme YAML supplies defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    color: Annotated[str, Color()] = Field(
        description="Border color as a CSS color string."
    )
    width: float = Field(description="Border width in pixels.")
    radius: float = Field(description="Border corner radius in pixels.")


class TooltipShadowStyle(BaseModel):
    """Tooltip drop-shadow toggle. JS applies the shadow expression when visible=true."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    visible: bool = Field(
        description="Show a drop-shadow on the tooltip box; theme always provides this."
    )


class TooltipSwatchStyle(BaseModel):
    """Series color swatch in the tooltip: the mark-colored chip next to each
    series row. All fields required; theme YAML supplies defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    size: float = Field(description="Swatch edge length in pixels (square).")
    radius: float = Field(
        description="Swatch corner radius in pixels (0 = square, ~half of size = circle)."
    )


class TooltipStyle(BaseModel):
    """Tooltip box style: all cascade keys for the hover bubble.

    Required scalars have no in-code defaults; theme YAML (via _base.yaml) supplies
    every value so the cascade fails loudly if a theme is incomplete.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Annotated[FormatAlias | str, Format()] = Field(
        description="Default tooltip value format string; theme always provides this."
    )
    background: Annotated[str, Color()] = Field(
        description="Tooltip background color (CSS color string); theme always provides this."
    )
    line_height: float = Field(
        description="Tooltip line-height multiplier; theme always provides this."
    )
    max_width: float = Field(
        description="Maximum tooltip width in pixels; theme always provides this."
    )
    gap: float = Field(
        description="Gap in pixels between label and value columns; theme always provides this."
    )
    # InheritSlot: tooltip.font fills from charts.font.
    font: Annotated[FontStyle, InheritSlot(from_path="Style.charts.font")] = Field(
        default_factory=FontStyle,
        description="Tooltip font overrides (size etc.); cascade fills missing fields.",
    )
    padding: PaddingStyle = Field(
        description="Tooltip inner padding (4 sides in pixels); theme always provides this."
    )
    label: TooltipSlotStyle = Field(
        default_factory=TooltipSlotStyle,
        description="Label-column font overrides (color, weight).",
    )
    value: TooltipSlotStyle = Field(
        default_factory=TooltipSlotStyle,
        description="Value-column font overrides (color, weight).",
    )
    border: TooltipBorderStyle = Field(
        description="Tooltip border style; theme always provides this."
    )
    shadow: TooltipShadowStyle = Field(
        description="Tooltip drop-shadow config; theme always provides this."
    )
    swatch: TooltipSwatchStyle = Field(
        description="Series color swatch size/shape; theme always provides this."
    )
    active_marker: Literal["fill", "triangle"] = Field(
        description=(
            "How the hovered row is marked in a multi-row (x-unified) tooltip: "
            "'fill' tints the row background (default); 'triangle' draws an "
            "edge-flush wedge in the box's left padding instead. Theme always "
            "provides this."
        )
    )


class AxisMirrorStyle(BaseModel):
    """Per-edge label override for the mirrored ``axis_y.mirror`` ghost axis.

    Mirror's whole value proposition is one shared y-scale ⇒ guaranteed-aligned
    ticks on both edges; this carries only the label-presentation fields needed
    to relabel the mirrored edge (e.g. ``%`` on the right while the primary
    edge reads ``$``) — never a scale or tick override, which would defeat the
    alignment mirror exists to guarantee.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Annotated[FormatAlias | str | FormatConfig | None, Format()] = Field(
        default=None,
        description="Tick value format string for the mirrored edge; None reuses the primary axis's format.",
    )
    expr: str | None = Field(
        default=None,
        description="Custom Vega expression for the mirrored edge's label text; None reuses the primary axis's label expression.",
    )

    @model_validator(mode="after")
    def _require_exactly_one_override(self) -> AxisMirrorStyle:
        # Both are label overrides for the same edge, and VL ignores `format`
        # whenever `labelExpr` is present — both-set would make format dead
        # config. Reject rather than silently prefer expr. An empty object is
        # an override that overrides nothing — also meaningless input.
        if self.format is not None and self.expr is not None:
            raise ValueError(
                "mirror takes format or expr, not both — expr fully controls "
                "the label text (call format() inside it if needed)."
            )
        if self.format is None and self.expr is None:
            raise ValueError(
                "empty mirror override — set format or expr to relabel the "
                "mirrored edge, or use `mirror: true` to mirror the primary "
                "axis's label verbatim."
            )
        return self


class BaseAxisStyle(BaseModel):
    """Universal axis surface: grid/line/ticks/labels/title/scale.

    Channel-agnostic: used for the theme's ``axis:`` slot (applies to all axes)
    and for AxisXStyle/AxisYStyle/QuantitativeAxisStyle/BandAxisStyle subclasses.
    Dimension-only and measure-only fields live on the appropriate subclass so
    the model rejects them at validation time rather than requiring scattered
    runtime checks. No ``position`` here — AxisXStyle/AxisYStyle each declare
    their own narrowed literal; axis_quantitative/axis_band have no position
    concept of their own.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    grid: BaseAxisGridStyle = Field(
        default_factory=BaseAxisGridStyle,
        description="Grid line style for this axis.",
    )
    line: AxisLineStyle = Field(
        default_factory=AxisLineStyle, description="Domain line style for this axis."
    )
    ticks: AxisTicksStyle = Field(
        default_factory=AxisTicksStyle, description="Tick mark style for this axis."
    )
    labels: AxisLabelStyle = Field(
        default_factory=AxisLabelStyle, description="Axis label style."
    )
    title: AxisTitleStyle = Field(
        default_factory=AxisTitleStyle,
        description="Axis title style.",
    )
    # SkipInheritSlots: scale is not inherited — axis family variants each own their scale config.
    scale: Annotated[BaseScaleStyle | None, SkipInheritSlots()] = Field(
        default=None, description="Per-axis scale overrides; None means no override."
    )


class AxisXStyle(BaseAxisStyle):
    """Dimension/category-time axis style. Theme slot: axis_x.

    Adds dimension-only fields: fill (required — theme supplies via axis_x.fill),
    time_unit (bucketing), type (ordinal/temporal discriminant), and overrides
    ticks/labels with DimensionTicksStyle/DimensionLabelStyle.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Override base's ticks type to add step-anchored cadence's unit field.
    ticks: DimensionTicksStyle = Field(
        default_factory=DimensionTicksStyle,
        description="Dimension axis tick style.",
    )
    # Override base's labels type to add dimension-only label fields.
    labels: DimensionLabelStyle = Field(
        default_factory=DimensionLabelStyle,
        description="Dimension axis label style.",
    )
    position: Annotated[Literal["top", "bottom"] | None, SkipInheritSlots()] = Field(
        default=None,
        description="X-axis position; None uses Vega-Lite's default (bottom).",
    )
    # Override base's scale type to XScaleStyle so x_reverse can be authored here.
    scale: Annotated[XScaleStyle | None, SkipInheritSlots()] = Field(
        default=None,
        description="X-axis scale overrides including x_reverse; None means no override.",
    )
    # Time-unit bucketing for temporal x axes (auto-detected when None or "auto").
    # "none" disables bucketing (continuous temporal). VL timeUnit values otherwise.
    time_unit: Annotated[_TIME_UNIT_LITERAL | None, SkipInheritSlots()] = Field(
        default=None,
        description="Time-unit bucketing for temporal x-axes; None or 'auto' auto-detects from data.",
    )
    # Authored opt-in scale type for bucketed-time x-axes.
    # None/"auto" = ordinal when time_unit resolves to a bucketed-calendar grain.
    type: Annotated[
        Literal["auto", "ordinal", "temporal"] | None, SkipInheritSlots()
    ] = Field(
        default=None,
        description="Scale type for bucketed-time x-axes; None/'auto' infers from time_unit grain.",
    )
    # Fill mode for missing time buckets on ordinal bucketed-time x-axes.
    # Required; theme YAML supplies the default ("null") via axis_x.fill.
    # SkipInheritSlots: fill is x-axis-only; always non-None (validator converts None→"null").
    fill: Annotated[
        Literal[
            "null",
            "zero",
            "linear",
            "step-after",
            "step-before",
            "step-center",
            "curve",
        ],
        SkipInheritSlots(),
    ] = Field(
        description=(
            "Fill for synthesized missing-bucket rows: null, zero, linear, "
            "step-after / step-before / step-center (Looker step), or curve (smoothstep)."
        ),
    )

    # Fiscal-year-start month for year/yearquarter bucket anchoring on axis_x.
    # 1 (January) is the calendar-quarter convention; any other value shifts
    # both the bucket boundaries (_enumerate_buckets) and the Q1..Q4 numbering
    # / year-boundary detection (is_label_opener, opens_label_period,
    # _quarter_label) by the same offset. Required (like fill) — theme YAML
    # supplies the default (1) via axis_x.fiscal_year_start_month. Only
    # meaningful for the year/yearquarter grains — yearmonth/yearweek/
    # yearmonthdate bucket boundaries are fixed regardless of this value.
    # SkipInheritSlots: matches fill/time_unit/type (x-axis-only fields).
    fiscal_year_start_month: Annotated[int, SkipInheritSlots()] = Field(
        description=(
            "Calendar month (1=Jan..12=Dec) that opens a fiscal year/quarter "
            "for year/yearquarter bucketing; theme default 1 is the calendar "
            "convention (Q1=Jan-Mar). A non-default value always wins over "
            "axis_x.type: temporal for year/yearquarter grains - Vega-Lite's "
            "native timeUnit transform has no fiscal-offset concept and "
            "would otherwise silently discard the offset."
        ),
    )

    @field_validator("fiscal_year_start_month")
    @classmethod
    def _validate_fiscal_year_start_month(cls, value: int) -> int:
        if not 1 <= value <= 12:
            raise ValueError(
                "axis.fiscal_year_start_month must be between 1 and 12 "
                f"(1=Jan..12=Dec) — got {value}"
            )
        return value

    @field_validator("fill", mode="before")
    @classmethod
    def _yaml_null_is_null_fill_mode(cls, value: object) -> object:
        # Theme YAML uses `fill: null` for the null fill mode; YAML parses that as None.
        if value is None:
            return "null"
        return value


class AxisYStyle(BaseAxisStyle):
    """Measure axis style. Theme slot: axis_y.

    Adds measure-only fields: mirror. Grid style is the shared
    BaseAxisGridStyle inherited from BaseAxisStyle — its ``threshold``
    sub-block is not y-only, see that class's docstring. x-axis-only fields
    (fill, time_unit, type, tilt_increments, labels.values) are structurally
    absent so the model rejects them at validation time.

    No ``categorical_orient`` (deleted 2026-08 trim) — ``position`` (below)
    now serves the "which side does this axis's orient read as" role for
    every consumer, dimension-swap-into-VL-y included.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    position: Annotated[Literal["left", "right", "auto"] | None, SkipInheritSlots()] = (
        Field(
            default=None,
            description="Y-axis position; auto flips when endpoint labels are on the right.",
        )
    )
    # y-axis only: draw the y-scale on both edges.
    mirror: Annotated[bool | AxisMirrorStyle | None, SkipInheritSlots()] = Field(
        default=None,
        description=(
            "Draw the y-scale on both left and right edges (wide charts). "
            "true mirrors the primary axis's label verbatim; an object (format/expr) "
            "relabels only the mirrored edge (e.g. a percent-of-total right axis "
            "next to an absolute-value left axis) while ticks stay aligned to the "
            "single shared scale. Only meaningful on axis_y."
        ),
    )


class QuantitativeAxisStyle(BaseAxisStyle):
    """Scale-type overlay for quantitative axes. Theme slot: axis_quantitative.

    Thin today (no extra fields beyond BaseAxisStyle). Subclassed to give
    quantitative-axis patches a distinct type so AxisOverrides can be precisely
    typed per slot.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class BandAxisStyle(BaseAxisStyle):
    """Scale-type overlay for band (categorical) axes. Theme slot: axis_band.

    Adds band_position — structurally absent from all other variants.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    band_position: Annotated[float | None, SkipInheritSlots()] = Field(
        default=None,
        description="Band position within the step (0–1); None uses Vega-Lite's default.",
    )
