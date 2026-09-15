"""Board-level resolved style dataclasses — post-cascade, all fields required.

Stage: COMPILE (resolve).

Hierarchy
---------
ResolvedAxisElementStyle / ResolvedAxisGridStyle / ... — axis sub-parts
  └─ ResolvedAxisStyle           — one fully-resolved axis
ResolvedLegendElementStyle
  └─ ResolvedLegendStyle
ResolvedStyle   — root resolved style; final board/chrome presentation only —
  sole type render/sizing consumes. Chart-family cascade working state lives
  on ``ChartStyleContext`` (``compile/models/style/context.py``), never here.

Populated by ``_cascade.resolve_style()``; never constructed directly outside
that module.
"""

from __future__ import annotations

import dataclasses
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from dbt_charts.core.compile.models.primitives import (
    BorderStyle,
    ResolvedFontStyle,
    SpacingValues,
)
from dbt_charts.core.compile.models.style.authored import PaginationConfig
from dbt_charts.core.compile.models.style.resolved.callout import (
    ResolvedCalloutElementStyle,
    ResolvedCalloutStyle,
)
from dbt_charts.core.compile.models.style.theme import (
    AreaChartStyle,
    AxisMirrorStyle,
    BarChartStyle,
    FooterStyle,
    FrameStyle,
    GeoshapeChartStyle,
    GlobalMarksStyle,
    HeatmapChartStyle,
    HistogramChartStyle,
    HoverEmphasisStyle,
    KpiChartStyle,
    KpiTonesStyle,
    LayoutStyle,
    LegendDirection,
    LegendPosition,
    LineChartStyle,
    PaddingStyle,
    PieChartStyle,
    PlaceholderStyle,
    PointMapChartStyle,
    ScatterChartStyle,
    SeriesLabelStyle,
    SparkBarChartStyle,
    SparkStyle,
    SupportTableStyle,
    TableChartStyle,
    TextStyle,
    TimestampStyle,
    TitleStyle,
    TooltipStyle,
    VariablesStyle,
    ViewStyle,
)
from dbt_charts.core.text.format_d3 import Notation
from dbt_charts.core.text.numeral_scale import SuffixMode, suffix_at_register

# Type alias for VL JSON passthrough — avoids per-site explicit-Any annotations.
_VLConfig = dict[str, Any]


@dataclasses.dataclass(frozen=True)
class ResolvedRulerAxis:
    """The baked "this axis ships the ruler composition" decision.

    Built once, in ``build_resolved_axis`` — see ``ResolvedAxisStyle.ruler``
    for why every consumer (paint, gutter measurement, mirror ghost) reads
    this instead of re-deriving the gate itself.
    """

    exponent: int
    mode: SuffixMode
    # Whether this axis's ticks stack into a column and therefore reserve the
    # suffix field (and required a tabular label font at resolve). False for
    # a horizontal ruler, whose ticks render along VL's x channel.
    reserve: bool
    # Whether the currency prefix repeats on every non-zero tick rather than
    # anchoring on one. Column_forming alone decides this -- unlike `mode`,
    # which can independently be REPEAT on a column-forming axis too (the
    # ladder's own magnitude-driven suffix register).
    prefix_repeats: bool
    # Split from axis.format by ruler_digit_format (core/text/numeral_scale.py)
    # once, here, rather than re-parsed at every consumer: prefix is the
    # currency symbol (empty if none), digit_spec is the fixed-point d3 spec
    # the scaled digits format through. Baking both means paint and the
    # gutter measurement read the same two strings instead of independently
    # re-deriving them from axis.format — which also means neither needs
    # axis.format to be non-None at their own call site; ruler being set
    # already guarantees it was.
    prefix: str
    digit_spec: str
    # Which END of the (ascending) tick ladder carries the magnitude suffix
    # -- True: the FIRST tick (Vega's datum.index === 0); False: the LAST
    # tick (datum.index === 1, the old, and still far more common, case).
    #
    # This is a position, not the tick's raw value, and that is deliberate.
    # A value comparison (datum.value === <the tick shared_scale_for_ladder
    # measured the ladder by>) looks more direct, but breaks the moment
    # dbt charts' own nice-rounded tick_values ladder includes an entry Vega
    # never actually draws -- which happens routinely (a headroom-expanded
    # domain_max short of the next nice round number; even a plain data
    # range Vega's own auto-fit trims tighter than dbt charts' ladder,
    # independent of any baked domain_max/domain_min). When that dropped
    # entry is the one a value comparison anchors on, no rendered tick ever
    # matches it, and the suffix silently never appears anywhere -- the
    # exact class of defect this field exists to fix, reintroduced by a
    # different route. Vega's own datum.index is renormalized 0-to-1 over
    # whichever ticks it actually draws, so comparing against a fixed END
    # (not a value) stays correct regardless of how many entries got
    # dropped, as long as they were dropped from the far end -- true in
    # every case observed (nice_tick_values only ever overshoots outward,
    # never inward).
    #
    # Both ends are the true magnitude-extreme only on a ladder that
    # crosses (or touches) zero without being one-sided -- an ascending,
    # evenly-spaced ladder's |value| is minimized somewhere in the middle
    # and maximized at exactly one of the two ends, so this is always
    # decidable, once, from tick_values alone.
    anchor_at_start: bool
    # The trailing padding a non-suffix tick reserves, composed once here
    # (font_measure.compose_suffix_reservation) from the space characters
    # that measure closest to this suffix's own advance in the resolved
    # label font — not a whole number of digit-width (U+2007) units, which
    # cannot express a non-integer suffix advance without a visible
    # residual. Baked rather than derived independently by paint and the
    # gutter measurement, so the two can never disagree about the padding
    # the way two independent re-derivations of "does the ruler apply" used
    # to disagree before this field existed. "" when reserve is False (a
    # horizontal ruler reserves nothing; a start-anchored axis gets no
    # ruler at all -- see build_resolved_axis).
    reservation: str
    # Pre-built decimal-padding strings, one per possible missing_len value
    # (0..precision+1). Each entry is the result of decimal_reservation_pad
    # for that missing_len, built from compose_decimal_units at resolve time.
    # Empty when reserve is False, when the font is non-tabular, or when the
    # mixed-depth gate finds every tick has the same fractional depth (no pad
    # needed). The axis's labelExpr and the Python gutter measurer select from
    # this table live (via decimal_pad_for), so they always agree.
    decimal_pad_table: tuple[str, ...] = dataclasses.field(default_factory=tuple)

    @property
    def register(self) -> Notation:
        """The register this decision speaks: analytic for ANCHOR, narrative
        for REPEAT. Derived from mode, not stored, so there is one source of
        truth for a decision mode already determines (register follows
        frequency: a suffix stated once is a set declaration and reads
        analytic; a suffix on every member is value-attached and reads
        narrative)."""
        return "analytic" if self.mode is SuffixMode.ANCHOR else "narrative"

    @property
    def suffix_string(self) -> str:
        """The one suffix string this decision ever emits, at its register."""
        return suffix_at_register(self.exponent, self.register)


@dataclasses.dataclass(frozen=True)
class ResolvedTickLabel:
    """The baked tick-label decision for a ladder that does not compact --
    ``ruler``'s non-compacting sibling (see ``ResolvedAxisStyle.ruler``'s
    docstring): its ticks are rewritten by ``non_compacting_tick_format``
    (full plain digits, or scientific for a ladder finer than a fixed-point
    spec reaches), unless the author asked for that exact SI format
    themselves. Read ONLY by this axis's own tick-label emission (overrides
    the VL axis ``format``, nothing else) -- never ``label.format`` (shared
    with value labels, tooltips, and the currency-warning detector) and never
    ``label.expr``. Mutually
    exclusive with ``ResolvedAxisStyle.ruler`` (see its ``__post_init__``): a
    ladder compacts or it doesn't, never both.
    """

    # Plain d3 spec. Not a Vega expression by itself -- a non-compacting
    # ladder makes no per-tick decision, so unlike `ruler` it needs no
    # expression, only a format string -- EXCEPT when `si_format` is also
    # set, where this is one arm of a per-tick conditional instead (see that
    # field, below).
    format: str
    # Currency prefix split from `format`'s symbol by non_compacting_tick_format
    # (mirrors ruler.prefix): the bare symbol (e.g. "$"), no added spacing.
    # "" when the format has no currency symbol.
    prefix: str = ""
    # Tri-state, keyed off `prefix` and column_forming (folded in at resolve,
    # never re-declared in render): "" prefix -> irrelevant, unset. Non-empty
    # prefix + column_forming -> bool, whether the anchor tick is first
    # rather than last (largest positive tick when any exist, else the
    # most-negative — sign-aware, unlike the magnitude-based
    # ResolvedRulerAxis.anchor_at_start, since a "$" has no scale dependency
    # the way a shared "K"/"M" suffix does). Non-empty prefix + NOT
    # column_forming -> None, meaning repeat the prefix on every tick (no
    # vertical digit column to disambiguate against) — mirrors
    # ResolvedRulerAxis's SuffixMode.REPEAT.
    anchor_at_start: bool | None = None
    # Mirrors ResolvedRulerAxis.decimal_pad_table: one padding string per
    # possible missing_len (0..precision+1), built from compose_decimal_units
    # at resolve time. Empty when the axis is non-column-forming, the font is
    # non-tabular, the mixed-depth gate finds every tick at the same depth, or
    # `format` is scientific (no fixed decimal position to pad to).
    decimal_pad_table: tuple[str, ...] = dataclasses.field(default_factory=tuple)
    # Set only for a LADDER-LESS axis (`ResolvedAxisStyle.tick_values` empty),
    # alongside `scientific_format` below -- both None or both set, never one
    # without the other. Turns `format` from the whole story into one arm of
    # a per-tick conditional; see `inject_axis_numeral_expr`'s docstring for
    # the full mechanism (the authoritative account -- don't restate it here).
    si_format: str | None = None
    # The other arm set alongside `si_format`: `sub_unit_scientific_format`'s
    # exponential register, for a tick too small for `format`'s
    # significant-digit register to reach cleanly. See
    # `inject_axis_numeral_expr`.
    scientific_format: str | None = None

    def __post_init__(self) -> None:
        if (self.si_format is None) != (self.scientific_format is None):
            raise ValueError(
                "ResolvedTickLabel.si_format and .scientific_format are set "
                "together or not at all -- one arm of the ladder-less "
                f"per-tick conditional with no other. got si_format="
                f"{self.si_format!r}, scientific_format={self.scientific_format!r}"
            )


# ── Resolved scale classes ────────────────────────────────────────────────────
# Pydantic BaseModels (not dataclasses) — keeps map_fields(scale, FIELD_MAP)
# working and .model_copy() working at resolve call sites in _resolve.py.


class ResolvedScaleLogStyle(BaseModel):
    """Resolved log-scale param."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base: float | None = None


class ResolvedScalePowStyle(BaseModel):
    """Resolved power-scale param."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    exponent: float | None = None


class ResolvedScaleSymlogStyle(BaseModel):
    """Resolved symlog-scale param."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    constant: float | None = None


def _coerce_list_to_tuple(
    value: object,  # type-state: object_annotation — BeforeValidator input, any raw value
) -> object:  # type-state: object_annotation — mirrors the input's object type
    """Mirrors ``theme.axis._coerce_list_to_tuple``: the authored side hands
    this field an already-validated ``tuple``, but tests construct
    ``ResolvedScaleContinuousStyle`` directly with a list literal -- accept
    both, coercing only ``list`` so a set/frozenset/deque/generator still
    fails the field's own strict tuple check rather than being silently
    reordered."""
    return tuple(value) if isinstance(value, list) else value


class ResolvedScaleContinuousStyle(BaseModel):
    """Resolved continuous-scale config."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    zero: bool | None = None
    type: Literal["linear", "log", "pow", "sqrt", "symlog", "temporal"] | None = None
    domain: (
        Annotated[
            tuple[
                Annotated[int, Field(strict=True)]
                | Annotated[float, Field(strict=True)]
                | str,
                Annotated[int, Field(strict=True)]
                | Annotated[float, Field(strict=True)]
                | str,
            ],
            Field(strict=True),
            BeforeValidator(_coerce_list_to_tuple),
        ]
        | None
    ) = None
    log: ResolvedScaleLogStyle | None = None
    pow: ResolvedScalePowStyle | None = None
    symlog: ResolvedScaleSymlogStyle | None = None


class ResolvedScaleStyle(BaseModel):
    """Resolved scale config — universal + continuous-only superset.

    Populated by build_resolved_scale() in _cascade.py. Render consumers read
    grouped paths (e.g. .scale.continuous.zero, .scale.continuous.log.base)
    not flat paths. x_reverse is populated only when resolving an x-channel scale.
    band/point/quantize/mark_size were deleted (2026-08 trim) along with the
    authoring surface for them.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    round: bool | None = None
    clamp: bool | None = None
    nice: bool | None = None
    padding: float | None = None
    headroom: float | None = None
    values: list[Any] | None = None
    continuous: ResolvedScaleContinuousStyle | None = None
    # Populated only when resolving an x-channel scale (XScaleStyle source).
    x_reverse: bool | None = None


@dataclasses.dataclass(frozen=True)
class ResolvedAxisLabelOverlapConfig:
    """Resolved axis label overlap strategy enablement — all fields required.

    True = strategy enabled; False = disabled. Fixed application order in the
    resolver: skip → tilt. Bucketed temporal axes thin label visibility by
    calendar period without changing the encoding or label format time unit.
    """

    skip: bool
    tilt: bool


@dataclasses.dataclass(frozen=True)
class ResolvedAxisElementStyle:
    """Resolved axis label or title — one shared superset dataclass.

    Chart-local Patch fields (labels.angle, labels.padding, etc.) flow through
    the typed ``axis_overrides_*`` sentinels on ``ChartStyleContext`` and
    are merged at emit time by ``resolved_axis_style``. They never land on a
    Resolved axis directly.

    Title's source type (``AxisTitleStyle``) only has font/angle/align/visible
    — its resolved instance leaves every other field at its default (usually
    None), same as how x-only/y-only fields already work on ``ResolvedAxisStyle``
    itself. ``padding`` is optional (not required) for exactly this reason:
    title has no padding concept post-trim.
    """

    font: ResolvedFontStyle
    padding: float | None = None
    # Optional VL-passthrough fields — carried from theme through resolve boundary.
    # None means "use VL default"; build_resolved_axis carries them through to
    # the VL emit layer. Mirrors AxisLabelStyle passthrough fields.
    max_width: float | None = None
    angle: float | None = None
    # Narrower than the authored Literal["left","right","center","inward",
    # "outward"] — build_resolved_axis's edge parameter resolves inward/
    # outward to a concrete side (or None with no left/right edge) before
    # construction, so this field can never hold inward/outward. left/right/
    # center pass through as authored. The render layer reads it exactly as
    # before #5845 (center still needs its own away-from-plot fallback on a
    # left/right axis — see bar.py / vl_field_maps.py).
    align: Literal["left", "right", "center"] | None = None
    # Resolved overlap strategy enablement. None = cascade did not populate
    # (resolver skips). Three required bools after cascade from _base.yaml.
    overlap: ResolvedAxisLabelOverlapConfig | None = None
    min_gap: float | None = None  # renamed from `separation`, same meaning
    visible: bool | None = None  # None = cascade-inherit from parent axis slot
    time_unit: str | None = None
    expr: str | None = None
    bound: bool | float | None = None
    flush: bool | float | None = None
    offset: float | None = None
    format: str | None = None  # tick-label format string; None uses VL auto-format
    tilt_increments: list[float] | None = None
    # Sparse label-only cadence override — None labels every tick per
    # the usual smart cadence/format; a list blanks every tick not in it.
    values: list[Any] | None = None
    # Sub-day clock register (24 or 12) — x-axis only. The theme cascade
    # defaults this to 12 (_base.yaml's axis_x.labels.clock); None here means
    # a test stub or a labels-less axis, not an authored choice.
    # See DimensionLabelStyle.clock.
    clock: int | None = None


@dataclasses.dataclass(frozen=True)
class ResolvedAxisGridThresholdStyle:
    color: str
    width: float
    visible: bool


@dataclasses.dataclass(frozen=True)
class ResolvedAxisGridStyle:
    """No defaults — must be explicitly constructed via resolve_style()."""

    visible: bool
    opacity: float
    width: float
    color: str
    dash: list[float] | None  # None → solid line (VL default)
    # Every axis carries a threshold block — a threshold rule is a property
    # of a quantitative axis, on either channel, not of the measure (y) axis
    # specifically. It is inert on an axis that never ticks at the
    # threshold (a band axis, or a categorical x-axis).
    threshold: ResolvedAxisGridThresholdStyle


@dataclasses.dataclass(frozen=True)
class ResolvedAxisLineStyle:
    visible: bool
    width: float
    color: str


@dataclasses.dataclass(frozen=True)
class ResolvedAxisTicksStyle:
    visible: bool
    color: str
    # None means "use VL default" — kept distinct from a numeric override.
    width: float | None
    length: float | None
    offset: float | None
    # Target tick count, keyed by AXIS not by type: the measure axis (axis_y)
    # bakes it into explicit tick_values at resolve(); axis_x passes it
    # through as VL axis.tickCount at emit, on a temporal or a quantitative
    # scale alike. None → VL picks automatically.
    count: int | None
    # Tick interval. With time_unit below, a multiple of that calendar grain;
    # alone on a quantitative axis_x, VL's axis.tickMinStep at emit.
    step: int | None
    # Step-anchored cadence unit. Continuous temporal axis_x only (populated
    # only when the source is DimensionTicksStyle — see build_resolved_axis);
    # passed through as VL axis.tickCount: {interval, step} at emit. None →
    # no cadence override (falls back to count, then VL's own default).
    time_unit: str | None = None


@dataclasses.dataclass(frozen=True)
class ResolvedAxisStyle:
    """Resolved per-axis style.

    At theme-resolve time this holds only theme-cascade fields; chart-local axis
    overrides flow through ``axis_overrides_*`` sentinels on ``ChartStyleContext``
    and are merged at emit time by ``resolved_axis_style``, which also returns a
    ``ResolvedAxisStyle`` with all fields fully concrete.
    """

    grid: ResolvedAxisGridStyle
    line: ResolvedAxisLineStyle
    ticks: ResolvedAxisTicksStyle
    labels: ResolvedAxisElementStyle
    title: ResolvedAxisElementStyle
    position: str | None  # None is a valid concrete value ("use VL default")
    # y-axis only: draw the y-scale on both edges. An AxisMirrorStyle object
    # additionally relabels the mirrored edge (format/expr); see AxisMirrorStyle.
    mirror: bool | AxisMirrorStyle | None
    band_position: float | None
    scale: ResolvedScaleStyle | None  # per-axis scale override (board cascade only)
    # None on non-dimension axes (axis_y, axis_quantitative, axis_band):
    # fill only exists on axis_x (AxisXStyle.fill). Theme supplies the x default;
    # the resolver carries it through to the emitter; non-x axes get None here.
    fill: (
        Literal[
            "null",
            "zero",
            "linear",
            "step-after",
            "step-before",
            "step-center",
            "curve",
        ]
        | None
    )
    time_unit: str | None  # time-unit bucketing for temporal x-axes
    type: Literal["auto", "ordinal", "temporal"] | None  # scale type for bucketed-time
    # Fiscal-year-start month (1=Jan..12=Dec) for year/yearquarter bucket
    # anchoring; 1 is the calendar-quarter convention. Only active on axis_x.
    fiscal_year_start_month: int
    # Baked by resolve() for cartesian y-axes; emitters read this, never compute.
    # Empty tuple means no explicit tick ladder (non-measure axes or no data).
    tick_values: tuple[float, ...] = ()
    # Baked by resolve(): the single "does this axis ship the ruler
    # composition" decision (core/text/numeral_scale.py's shared_scale_for_ladder,
    # folded with the SI-format check and the authored-label.expr opt-out).
    # None means the axis paints exactly as its resolved format/labelExpr
    # already specify — no magnitude producer runs, in paint, in the gutter
    # measurement, or in the mirror ghost. Every one of those three reads
    # this one field rather than re-deriving "does it apply" from
    # shared_scale/format/labelExpr independently, which is how they used to
    # disagree (paint under-gated, measurement over-gated, and the mirror
    # ghost mistook the primary's own injected expression for an authored
    # one). `mode` and `register` here are already the FINAL, effective
    # values — a non-column-forming (horizontal) axis is baked to REPEAT/
    # narrative regardless of what the bare ladder would otherwise select.
    ruler: ResolvedRulerAxis | None = None
    # y-axis only: the ruler decision for the *mirrored* edge (style.axis_y.
    # mirror), baked alongside ruler by the same build_resolved_axis call --
    # not re-derived in render. The ghost's own anchoring is typically, but
    # not always (an authored labels.align can make either edge either
    # anchoring), the opposite of the primary's, so a single ruler cannot
    # describe both; this is the second one. None when mirror is unset, the
    # ladder doesn't compact (ruler is also None then), or mirror.expr/
    # mirror.format replaces the ghost's paint entirely (the engine's own
    # composition, baked or not, is never consulted, so there is nothing
    # for this field to describe).
    mirror_ruler: ResolvedRulerAxis | None = None
    # Baked by resolve(): the non-compacting sibling of `ruler` — the same
    # compact/does-not-compact decision, the other outcome. `ruler` says
    # "this ladder compacts, here is the magnitude"; this says "this ladder
    # does not compact, here are its plain digits" — see `ResolvedTickLabel`
    # for the grouped fields (format/counts/prefix/anchor_at_start). Mutually
    # exclusive with `ruler` (see `__post_init__`): a ladder compacts or it
    # doesn't, never both. None means this axis's ticks paint from the
    # resolved `label.format`/`label.expr` exactly as authored, same as when
    # `ruler` is also None.
    tick_label: ResolvedTickLabel | None = None
    # Baked by resolve() alongside tick_values: the EXACT (non-nice-rounded)
    # measure-axis domain top after headroom expanded the data max. Emitters
    # read this for VL `domainMax`. None means VL auto-fits the top.
    domain_max: float | None = None
    # Baked alongside domain_max. Zoomed (non-zero-anchored) axes get the
    # symmetric span-relative bottom: domain_min = data_min - headroom*span.
    # A zero-anchored axis normally leaves it None — the bottom stays at 0 —
    # EXCEPT when the data is all-negative, where 0 is the ceiling instead and
    # the floor is the headroom-expanded data min (see _resolve_cartesian_ticks).
    domain_min: float | None = None
    # Whether this axis's channel is quantitative (numeric) rather than
    # categorical — the same channel classification the cascade already
    # computes to decide format/label handling, baked here so render reads
    # one fact instead of re-deriving it per family.
    is_quantitative: bool = False
    # Whether this axis's floor is anchored at 0 — an explicit author pin,
    # or unpinned data close enough to 0 for the axis to reach it on its
    # own. False on a non-measure axis or when no anchor decision ran.
    zero_anchored: bool = False

    def __post_init__(self) -> None:
        if self.ruler is not None and self.tick_label is not None:
            raise ValueError(
                "ResolvedAxisStyle.ruler and .tick_label are mutually "
                "exclusive -- both are baked outcomes of the same ladder "
                "compacts/does-not-compact decision, so at most one may be "
                f"set. got ruler={self.ruler!r}, tick_label={self.tick_label!r}"
            )


class ResolvedLegendElementStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    font: ResolvedFontStyle
    padding: float
    max_width: float | None = None
    # Only meaningful on the title slot (authored as legend.title.visible).
    # None remains meaningful until the top-horizontal auto-hide policy runs;
    # False suppresses the title and True preserves an explicit opt-in. The
    # label slot is constructed with True because it has no separate visibility.
    visible: bool | None = None


class ResolvedLegendStyle(BaseModel):
    """Resolved legend style.

    ``visible`` is False when the theme or a chart-local patch suppresses the
    legend. The render layer checks the boolean to emit VL ``legend: null``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    position: LegendPosition
    direction: LegendDirection
    columns: int
    # Divides legend entries into rows (_stack_legend_should_yield in
    # compile/resolve/chart/bar.py); LegendStyle's own ge=1 doesn't survive the
    # all-Optional authored-patch cascade, so this is the one choke point
    # every legend merge terminal-validates against (merge_onto_base's
    # type(base).model_validate) — an authored compact_columns: 0 must fail
    # loudly here, not surface as a resolve-time ZeroDivisionError.
    compact_columns: int = Field(ge=1)
    label: ResolvedLegendElementStyle
    title: ResolvedLegendElementStyle
    visible: bool
    symbol_limit: int | None = None
    # Cascade-managed sentinel: None = renderer infers entry order from the
    # color domain; a list pins explicit legend.values order (author surface).
    values: list[str] | None = None
    # Author override for the legend glyph shape (VL symbolType). None = let
    # the renderer pick the mark-aware glyph; non-None wins over the mark-derived
    # Vega expression and is emitted as a constant string.
    symbol_shape: str | None = None
    # Author override for symbol fill. False → symbolFillColor='transparent'
    # in the emitted VL legend, producing hollow glyphs. None = VL default.
    symbol_fill: bool | None = None
    # Internal, resolved-only fact — never authored, absent from every patch
    # model (LegendStyle/LegendStylePatch declare no such field). Set to the
    # author's own `position` when the tiny-width tier forced this legend
    # back to `top` over it (cartesian_series_naming's
    # legend_position_overridden_by_width); None otherwise, including a
    # tiny-width chart that authored no position or authored `top` itself.
    # Read by the WARN-LEGEND-POSITION-WIDTH-FALLBACK render-stage detector.
    position_overridden_by_width: LegendPosition | None = None


@dataclasses.dataclass(frozen=True)
class ResolvedChartDefaults:
    """Final, board-wide chart-family defaults — every ``ChartStyleContext``
    field that is NOT sparse, patch-shaped, or cascade-only.

    Exactly ``ChartStyleContext`` minus its compiler-working-state fields:
    the raw ``axis``/``axis_x``/``axis_y``/``axis_quantitative`` theme
    passthroughs; the ``charts_board_overrides`` board-patch accumulator;
    the ``axis_overrides_*``/``scale``/``color`` chart-local patch sentinels;
    ``palettes``/``roles``/``pre_style``; and ``card_padding``, which is a
    board *frame* value the chart cascade borrows, already reachable here as
    ``ResolvedStyle.frame.card_padding``. Every remaining field is fully
    concrete and board-wide (never chart-local-patched at this level), so
    this bag is legitimately ``Resolved*`` — a plain passthrough from the
    same theme cascade that builds ``ChartStyleContext``.

    Exists for render call sites that legitimately need a board-level
    chart-family default with no per-chart cascade involved: width fallbacks
    (``render_resolved_chart()``), cross-family embedded presentation (spark
    cells and KPI tone swatches inside another chart's attached support table),
    board chrome derived from chart style (table stripe/header colors for
    CSS, tooltip config). Anything requiring a per-chart cascade re-run
    (axis-offset geometry, chart-local overrides) belongs on
    ``ChartStyleContext`` instead, never here.
    """

    # --- Palette & color tokens ---
    palette: list[str]
    dark_companion_palette: tuple[str, ...]
    single_series_palette: list[str]
    requested_alias_palette: str | None
    dashes: list[list[int]]
    # Semantic tone palette (positive/negative/warning/info) — board-level,
    # shared by KPI support rows and table conditional glyphs.
    tones: KpiTonesStyle

    # --- Board-wide sizing defaults ---
    preferred_width: float
    default_chart_height: float
    default_table_height: float
    label_usable_ratio: float
    aspect_ratio: float
    min_height: float
    max_height: float
    padding: PaddingStyle
    border: BorderStyle

    # --- Global mark/legend/label defaults ---
    legend: ResolvedLegendStyle
    marks: GlobalMarksStyle
    view: ViewStyle
    series_label: SeriesLabelStyle

    # --- Per-family theme style ---
    bar: BarChartStyle
    line: LineChartStyle
    area: AreaChartStyle
    scatter: ScatterChartStyle
    pie: PieChartStyle
    geoshape: GeoshapeChartStyle
    point_map: PointMapChartStyle
    histogram: HistogramChartStyle
    heatmap: HeatmapChartStyle
    kpi: KpiChartStyle
    table: TableChartStyle
    spark: SparkStyle
    spark_bar: SparkBarChartStyle
    support_table: SupportTableStyle
    callout: ResolvedCalloutStyle
    callout_error: ResolvedCalloutStyle

    # --- Board/board chrome ---
    tooltip: TooltipStyle
    hover_emphasis: HoverEmphasisStyle
    font_family: str | None
    title: TitleStyle
    pagination: PaginationConfig | None
    formats: dict[str, str] | None
    background: str


@dataclasses.dataclass(frozen=True)
class ResolvedStyle:
    """Final merged board/chrome style — all fields required, no defaults.

    The sole type accepted by the render/sizing layer for board-level
    presentation. Every instance must be produced by resolve_style() — never
    constructed directly. Carries no chart-family cascade state (sparse axis
    overlays, patch sentinels, palette/role token bindings, the pre-inherit
    tree) — that lives on ``ChartStyleContext``
    (``compile/models/style/context.py``), produced alongside this by
    ``resolve_chart_style_context()`` and threaded separately to runtime
    chart resolution. Per-chart presentation lives on each resolved chart's
    own family style (``ResolvedBarStyle`` etc.), not here.
    """

    background: str
    accent: str
    muted: str
    # Semantic tone palette (positive/negative/warning/info) — board level,
    # shared by KPI support rows and table conditional glyphs.
    tones: KpiTonesStyle
    font: ResolvedFontStyle
    border: BorderStyle
    box_shadow: str | None  # None = no shadow (valid concrete value)
    opacity: float
    frame: FrameStyle
    title: TitleStyle
    text: TextStyle
    placeholder: PlaceholderStyle
    layout: LayoutStyle
    variables: VariablesStyle
    footer: FooterStyle
    timestamp: TimestampStyle
    # Per-board CSS-chrome. None = not authored; theme supplies no default.
    padding: SpacingValues | None
    margin: SpacingValues | None
    gap: float | None
    # Root emoji mode — preserved from RootFontStyle.emoji so render code
    # can read it without re-accessing get_config().
    emoji_mode: Literal["monochrome", "system-default", "disabled"]
    # Merged VL config dict: base defaults ⊕ style overlay ⊕ background.
    # Baked at resolve time so render code reads it without a compile reach-back.
    vega_config: _VLConfig
    # See ResolvedChartDefaults — final board-wide chart-family constants for
    # cross-family embedded presentation only.
    chart_defaults: ResolvedChartDefaults


def effective_padding(resolved: ResolvedStyle) -> SpacingValues:
    """Return padding with border-occupancy added (half stroke inside the box)."""
    pad = resolved.padding or SpacingValues()
    bw = resolved.border.width
    return SpacingValues(
        top=pad.top + bw,
        bottom=pad.bottom + bw,
        left=pad.left + bw,
        right=pad.right + bw,
    )


__all__ = [
    "ResolvedAxisElementStyle",
    "ResolvedAxisGridStyle",
    "ResolvedAxisGridThresholdStyle",
    "ResolvedAxisLineStyle",
    "ResolvedAxisStyle",
    "ResolvedAxisTicksStyle",
    "ResolvedCalloutElementStyle",
    "ResolvedCalloutStyle",
    "ResolvedChartDefaults",
    "ResolvedLegendElementStyle",
    "ResolvedLegendStyle",
    "ResolvedScaleContinuousStyle",
    "ResolvedScaleLogStyle",
    "ResolvedScalePowStyle",
    "ResolvedScaleStyle",
    "ResolvedScaleSymlogStyle",
    "ResolvedStyle",
    "effective_padding",
]
