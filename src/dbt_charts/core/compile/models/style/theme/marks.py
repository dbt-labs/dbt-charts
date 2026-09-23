"""Theme-stage style classes: shared per-mark-type styles and cross-family primitives."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from dbt_charts.core.compile.models.markers import (
    Color,
    Format,
    InheritSlot,
    SkipInheritSlots,
)
from dbt_charts.core.compile.models.primitives import (
    BorderStyle,
    Curve,
    FontColorStrokeStyle,
    FontStyle,
    FormatConfig,
    LineCap,
    StrokeStyle,
)
from dbt_charts.core.compile.models.schema_names import FormatAlias
from dbt_charts.core.compile.template.labels_env import (
    validate_label_template,
    validate_label_where,
)


class TextMarkStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # InheritSlot: text mark font fills from charts.font (same as axis/legend font cascade).
    font: Annotated[FontStyle, InheritSlot(from_path="Style.charts.font")] = Field(
        default_factory=FontStyle, description="Text mark font style overrides."
    )
    # Cascade tier sentinel: None means "not specified at this tier".
    align: str | None = Field(
        default=None, description="Horizontal text alignment for text marks."
    )


class SeriesLabelFontStyle(FontStyle):
    """Series-label typography with a compact width-tier size."""

    compact_size: Annotated[float, SkipInheritSlots()] = Field(
        description="Series-label font size in pixels on tiny and narrow cards."
    )
    compact_weight: Annotated[str | float, SkipInheritSlots()] = Field(
        description="Series-label font weight on tiny and narrow cards."
    )


class SeriesLabelStyle(BaseModel):
    """Series-label primitive: typography for any text mark that names a
    data series, regardless of placement.

    Endpoint labels (line/area) and direct stack labels (bar-stacked) read
    their typography from this single primitive. Placement is family-specific;
    typography is shared.

    No ``align`` field: alignment is a placement decision owned by the
    family-specific renderer (endpoint labels left-anchor in their pane;
    stack labels center within their marks).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # InheritSlot: series_label.font fills from charts.font.
    font: Annotated[
        SeriesLabelFontStyle, InheritSlot(from_path="Style.charts.font")
    ] = Field(
        description="Series label font style overrides; cascade fills missing fields from charts.font.",
    )


class TotalSlotStyle(BaseModel):
    """Theme slot for one text element of the donut center total."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # InheritSlot: pie.total.{value,label}.font fills from charts.font.
    font: Annotated[FontStyle, InheritSlot(from_path="Style.charts.font")] = Field(
        default_factory=FontStyle,
        description="Donut center total element font style overrides.",
    )


class TotalValueSlotStyle(TotalSlotStyle):
    """Theme slot for the donut center value (the number): paint plus its format."""

    # Cascade-managed sentinel: None means "no format authored anywhere in the
    # cascade" -- resolve_chart falls back to a donut-shape default (or VL's
    # own default for a non-donut total) rather than a theme-supplied literal,
    # same shape as BarTotalLabelStyle.format.
    format: Annotated[FormatAlias | str | FormatConfig | None, Format()] = Field(
        default=None,
        description="How the donut center value is written: a D3 spec, a preset name, or a format block.",
    )


class TotalStyle(BaseModel):
    """Donut center total paint: value (the number) and label (the caption)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: TotalValueSlotStyle = Field(
        description="Style for the donut center value (the number), including its format."
    )
    label: TotalSlotStyle = Field(
        description="Style for the donut center label (the caption)."
    )


class LabelsDefaultTemplate(BaseModel):
    """Default Jinja templates for per-slice pie/donut labels.

    Applied when the chart author omits ``labels:`` and the chart is
    pie/donut with ``theta:`` set. The pick between ``with_color`` and
    ``no_color`` happens at resolve time, based on whether the chart
    authored a ``color:`` channel — that's a structural fact about the
    chart's authoring surface, not a theme concern.

    Templates use the generic Jinja context vars supplied by the
    ``context_extras`` callback in ``render/chart/emitters/pie.py``
    (``percent``, ``color``, ``value``, ``total``) so they work regardless
    of the chart's theta/color field names.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    with_color: str = Field(
        description="Default per-slice label template when the chart has a color binding.",
    )
    no_color: str = Field(
        description="Default per-slice label template when the chart has no color binding.",
    )

    @field_validator("with_color", "no_color")
    @classmethod
    def _validate_jinja(cls, value: str) -> str:
        validate_label_template(value)
        return value


class SliceLabelsStyle(BaseModel):
    """Pie labels: typography + positioning offsets for per-slice text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    offset: float = Field(
        description="Radial offset of slice labels from the arc in pixels."
    )
    line_height: float = Field(
        description=(
            "Line height for slice labels in pixels. Reserved vertical "
            "space above the disk is ``line_height × <rendered lines>`` "
            "per row, so the same value handles 1-line, 2-line, and "
            "multi-line templates."
        ),
    )
    # InheritSlot: fills unset family/size/weight/style/decoration from
    # charts.font (same pattern as KpiStyle, LegendStyle, TextMarkStyle).
    # Fires at the canonical charts.marks.slice.labels path;
    # SkipInheritSlots(cascade=True) on the parent SliceMarkStyle.labels field
    # clears the outer slot context but keeps skip_slots=False so this marker
    # still processes correctly. ``color`` is excluded from the fill: it stays
    # a genuine cascade-managed sentinel (None unless authored at some tier)
    # so PieEmitter can tell "theme filled this" from "the author set
    # this" and prefer an authored color over the dark-companion default.
    font: Annotated[
        FontStyle,
        InheritSlot(from_path="Style.charts.font", exclude=frozenset({"color"})),
    ] = Field(
        default_factory=FontStyle,
        description=(
            "Slice label font style overrides. ``color`` only takes effect "
            "on single-series pies (no ``color:`` channel authored); "
            "multi-series pies always paint each label with ink derived "
            "from its own wedge color against the chart's canvas, and "
            "ignore an authored ``color`` here."
        ),
    )
    # Default content (not typography). When the chart author omits ``template:``
    # under this block, the resolver picks with_color or no_color based on
    # whether the chart has a color channel.
    default_template: LabelsDefaultTemplate = Field(
        description="Default Jinja templates for per-slice labels when template is not authored."
    )
    # Cascade-managed sentinels: None = not authored at this tier; resolver falls
    # back to default_template. When set at any cascade tier (board, chart-style),
    # the authored value wins.
    template: Annotated[str | None, AfterValidator(validate_label_template)] = Field(
        default=None,
        description="Jinja2 label template. Overrides default_template when authored.",
    )
    where: Annotated[str | None, AfterValidator(validate_label_where)] = Field(
        default=None,
        description="Jinja2 boolean filter; labels only render on rows where this is truthy.",
    )


class ProjectionStyle(BaseModel):
    """Vega-Lite map projection configuration for geo chart families."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str = Field(
        description="Vega-Lite projection type (e.g. 'mercator', 'albersUsa', 'equalEarth')."
    )


class BasemapStyle(BaseModel):
    """Background map layer for geo charts (especially point maps).

    ``source`` is the topography source identifier. None means no background
    topo layer — the chart renders points or shapes on a blank canvas.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # None = no background layer. Point maps may omit the basemap entirely
    # when the data projection is self-explanatory (e.g. lat/lng scatter).
    source: str | None = Field(
        default=None,
        description="Background topo source identifier; None means no base layer.",
    )


class MarkLabelsStyle(BaseModel):
    """Shared value-label config for bar/line/point marks.

    All fields are None-defaulted cascade-tier sentinels: None means
    "not specified at this tier; inherit from global labels slot or leave unset".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Cascade tier sentinel: None means "not specified at this tier".
    visible: bool | None = Field(
        default=None,
        description="Show numeric value labels on each mark; False by default.",
    )
    # Authored-only override (not theme-populated): name a data column to draw the
    # label text from instead of the chart's y-field. Lets the axis keep the true y
    # unit (e.g. "200M") while the label shows a pre-computed column from the query
    # (e.g. a value already scaled to millions → bare "190"). None = label the y-field.
    field: str | None = Field(
        default=None,
        description="Column to source label text from; None uses the chart's y-field.",
    )
    # None here means "fall back to the resolved measure-axis format" — filled
    # in directly at chart-resolve time (_marks.py's per-family label-format
    # fallback) from the already-baked ResolvedAxisStyle, not via apply_inherit:
    # axis_quantitative is a sparse authored-only overlay (SkipInheritSlots), so
    # the correct per-chart value only exists post-cascade, not as an inherit-
    # graph input.
    format: Annotated[FormatAlias | str | None, Format()] = Field(
        default=None,
        description="Number format string for value labels.",
    )
    # Cascade tier sentinel: None means "use position-derived default".
    dx: int | None = Field(
        default=None,
        description="Horizontal pixel offset for value labels; overrides the position default.",
    )
    # Cascade tier sentinel: None means "use position-derived default".
    dy: int | None = Field(
        default=None,
        description="Vertical pixel offset for value labels; overrides the position default.",
    )
    # SkipInheritSlots(cascade=True): apply_inherit copies the entire font object
    # from the parent slot when font is None.
    font: Annotated[FontStyle | None, SkipInheritSlots(cascade=True)] = Field(
        default=None,
        description="Value label font style (color, size, family, etc.).",
    )


class BarTotalLabelStyle(BaseModel):
    """Stack total label style for bar marks.

    Draws a single label above (or to the right of, for horizontal bars) the
    topmost segment of each stack, showing the sum of all segments in that
    stack group.  Only takes effect on stacked bar charts; ignored otherwise.

    Scalar fields (visible, format, dx, dy) are None-defaulted cascade-tier
    sentinels.  ``font`` cascades leaf-by-leaf from ``Style.charts.font`` so
    the resolved color is always the theme's foreground text color — never
    None — making the total label visible on the page canvas.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Cascade tier sentinel: None means "not specified at this tier".
    visible: bool | None = Field(
        default=None,
        description=(
            "Show stack total labels above each bar stack. "
            "Only takes effect on stacked bar charts; ignored otherwise."
        ),
    )
    # None here means "fall back to the resolved measure-axis format" — filled
    # at resolve time from the already-baked ResolvedAxisStyle.format.
    format: Annotated[FormatAlias | str | None, Format()] = Field(
        default=None,
        description="Number format string for stack total labels.",
    )
    # Cascade tier sentinel: None means "use the default offset for this orientation".
    dx: int | None = Field(
        default=None,
        description="Horizontal pixel offset for stack total labels.",
    )
    # Cascade tier sentinel: None means "use the default offset for this orientation".
    dy: int | None = Field(
        default=None,
        description="Vertical pixel offset for stack total labels.",
    )
    # InheritSlot: total_label.font fills from charts.font.
    font: Annotated[FontStyle, InheritSlot(from_path="Style.charts.font")] = Field(
        default_factory=FontStyle,
        description="Stack total label font style overrides; cascade fills missing fields from charts.font.",
    )


class BarLabelsStyle(MarkLabelsStyle):
    """Bar mark value-label config. Extends MarkLabelsStyle with bar-specific positions."""

    # Cascade tier sentinel: None means "not specified at this tier".
    position: Literal["above", "top", "middle", "middle_aligned", "bottom"] | None = (
        Field(
            default=None,
            description=(
                "Label position relative to the bar. "
                "'above' places labels above the bar top (outside). "
                "'top' places labels just inside the top edge. "
                "'middle' centers labels vertically in the bar. "
                "'middle_aligned' centers all labels at a common height (mean of bar heights / 2). "
                "'bottom' places labels just inside the bottom edge."
            ),
        )
    )


class PointLabelsStyle(MarkLabelsStyle):
    """Point/line mark value-label config. Used by both LineMarkStyle and PointMarkStyle."""

    # Cascade tier sentinel: None means "not specified at this tier".
    position: Literal["top", "bottom", "left", "right", "middle"] | None = Field(
        default=None,
        description=(
            "Label position relative to the point. "
            "'top' places labels above the point. "
            "'bottom' places labels below. "
            "'left' to the left. "
            "'right' to the right. "
            "'middle' centers on the point."
        ),
    )


class BarMarkStyle(BaseModel):
    """Bar mark geometry and stroke. Chart-level bar fields live on BarChartStyle.

    All fields None = "not set at this tier; inherit from global".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Cascade tier sentinel: None means "not specified at this tier".
    # SkipInheritSlots(cascade=True): apply_inherit copies the entire border object
    # from the parent slot when border is None; _set cannot navigate through a None
    # intermediate container.
    border: Annotated[BorderStyle | None, SkipInheritSlots(cascade=True)] = Field(
        default=None,
        description="Bar border style (color/width also serve as the bar stroke).",
    )
    # Cascade tier sentinel: None means "not specified at this tier".
    padding: float | None = Field(
        default=None, description="Padding around bar marks in pixels."
    )
    # Cascade tier sentinel: None means "not specified at this tier". Fixed-width
    # override: when set, this exact pixel width is used for every bar on any
    # scale, replacing band_width on a band scale and the gap/min_size/max_size
    # ladder on a continuous scale.
    size: float | None = Field(
        default=None, description="Bar width in pixels; overrides all other sizing."
    )
    # Bar width as a fraction of the band step (0..1). Maps to VL
    # ``mark.width: {band: N}`` and works on a BUCKETED (timeUnit) temporal x
    # scale — a genuine band, see x_encoding_is_banded — so time-unit bars can
    # preserve gaps while keeping consistent column widths. Not a raw
    # continuous temporal x (no band there to size against); there the
    # continuous gap/min_size/max_size ladder governs instead, probing the
    # scale with epoch-ms points — see continuous_bar_size_prop.
    # Cascade tier sentinel: None means "not specified at this tier".
    band_width: float | None = Field(
        default=None,
        description="Bar width as a fraction of the band step (0–1).",
    )
    # Continuous (quantitative) x/y scale only — a band scale sizes via
    # band_width instead. Cascade tier sentinel: None means "not specified at
    # this tier".
    gap: float | None = Field(
        default=None,
        description="Target pixel gap between adjacent bars on a continuous scale.",
    )
    # Cascade tier sentinel: None means "not specified at this tier".
    min_size: float | None = Field(
        default=None,
        description="Narrowest a computed bar width may shrink to on a continuous scale.",
    )
    # Cascade tier sentinel: None means "not specified at this tier".
    max_size: float | None = Field(
        default=None,
        description=(
            "Widest a computed bar width may grow to on a continuous scale. Also "
            "stands in for an unauthored `size` when reserving axis padding and "
            "when budgeting a horizontal bar chart's minimum height."
        ),
    )
    # Cascade tier sentinel: None means "not specified at this tier".
    opacity: float | None = Field(
        default=None, description="Bar fill opacity (0–1); None uses VL default."
    )
    # SkipInheritSlots(cascade=True): leaf fields inside labels inherit individually
    # from the parent slot (no container-copy since labels is never None).
    labels: Annotated[BarLabelsStyle, SkipInheritSlots(cascade=True)] = Field(
        default_factory=BarLabelsStyle,
        description="Value label style for bar marks.",
    )
    # SkipInheritSlots(cascade=True): leaf fields inside total_label inherit
    # individually from the parent slot (no container-copy since total_label is
    # never None).
    total_label: Annotated[BarTotalLabelStyle, SkipInheritSlots(cascade=True)] = Field(
        default_factory=BarTotalLabelStyle,
        description=(
            "Stack total label style. Only takes effect on stacked bar charts; "
            "ignored otherwise."
        ),
    )


class LineMarkStyle(BaseModel):
    """Line mark stroke, interpolation, and halo. Point mark lives on PointMarkStyle.

    All fields None = "not set at this tier; inherit from global".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Cascade tier sentinel: None means "not specified at this tier".
    # SkipInheritSlots(cascade=True): apply_inherit copies the entire stroke object
    # from the parent slot when stroke is None.
    stroke: Annotated[StrokeStyle | None, SkipInheritSlots(cascade=True)] = Field(
        default=None, description="Line stroke style."
    )
    # Cascade tier sentinel: None means "not specified at this tier".
    curve: Curve | None = Field(
        default=None,
        description=(
            "Line interpolation curve, one of a fixed set: 'linear', 'monotone', "
            "'natural', 'basis', 'cardinal', 'step', 'step-before', 'step-after'. "
            "On a categorical (nominal/ordinal) x-axis, 'step' draws a "
            "full-band-width plateau per x-value instead of VL's centered step; "
            "on a continuous (temporal/quantitative) x-axis it passes straight "
            "through to VL's native step."
        ),
    )
    # Cascade tier sentinel: None means "not specified at this tier"; emission
    # treats None as True. Only meaningful for curve='step' on a categorical
    # (band) x-axis: False suppresses the vertical bridges between adjacent
    # band plateaus.
    connect: bool | None = Field(
        default=None,
        description=(
            "For curve='step' on a categorical (band) x-axis, whether adjacent "
            "band plateaus are joined by vertical jumps (True/None) or left as "
            "disconnected segments (False). No-op for other curves or a "
            "continuous x-axis."
        ),
    )
    # Cascade tier sentinel: None means "not specified at this tier".
    # Sits on the mark, not inside ``stroke``, and NOT because a flat variant
    # field is prettier — ``Style.charts.area.marks`` inherits wholesale from
    # ``Style.charts.marks`` (inherit_registry), so this stroke object is copied
    # verbatim into ``AreaLineStyle.stroke``. A ``disconnected_cap`` inside it
    # would become authorable at ``charts.area.marks.line.stroke`` and silently
    # do nothing — an area edge is one continuous silhouette with no ``connect``.
    # That is the same trap AreaLineStyle's docstring says it was split out to
    # prevent, so the line/area boundary runs at the mark, next to ``connect``.
    disconnected_cap: LineCap | None = Field(
        default=None,
        description=(
            "Stroke line cap for the sub-paths of a disconnected band-step "
            "(curve='step' + connect=False on a categorical x-axis). Each band "
            "is its own flat path there, so the cap lands on every band edge "
            "rather than only the two ends of one continuous line; 'butt' keeps "
            "plateaus flush with the band. Overrides stroke.cap in that geometry "
            "only; every other line keeps stroke.cap."
        ),
    )
    # Halo: a knockout-colored line drawn behind the foreground line so that
    # crossings read cleanly. ``halo_multiplier`` of 0 disables it; otherwise
    # the halo stroke width and (when points are enabled) halo point size are
    # ``base * halo_multiplier``. Halo color is the chart's effective background.
    # Cascade tier sentinel: None means "not specified at this tier".
    halo_multiplier: float | None = Field(
        default=None,
        description="Halo stroke width multiplier relative to stroke.width; 0 disables the halo.",
    )
    # SkipInheritSlots(cascade=True): leaf fields inside labels inherit individually
    # from the parent slot (no container-copy since labels is never None).
    labels: Annotated[PointLabelsStyle, SkipInheritSlots(cascade=True)] = Field(
        default_factory=PointLabelsStyle,
        description="Value label style for line marks.",
    )


class AreaStackedMarkStyle(BaseModel):
    """Stacked / streamgraph area recipe override: solid fill + perimeter stroke.

    Applied instead of AreaMarkStyle's overlap recipe when a chart is stacked
    (zero/normalize/center) or is a single unstacked series (no color, no
    layers, at most one y measure). A dedicated type rather than a
    self-typed ``AreaMarkStyle`` field: the generated authored-patch factory
    (``build_patch_model_ext``) has no cycle guard for a compiled model that
    contains itself, so a literal self-reference blows the recursion limit
    when the authored ``AreaMarkStylePatch`` is synthesized at import time.

    All fields None = "not set at this tier; inherit from global".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Cascade tier sentinel: None means "not specified at this tier".
    opacity: float | None = Field(
        default=None, description="Area fill opacity (0–1) for the stacked recipe."
    )
    # SkipInheritSlots(cascade=True): apply_inherit copies the entire stroke object
    # from the parent slot when stroke is None.
    stroke: Annotated[StrokeStyle | None, SkipInheritSlots(cascade=True)] = Field(
        default=None, description="Perimeter stroke style for the stacked recipe."
    )
    # Cascade tier sentinel: None means "not specified at this tier".
    # NB: a stacked chart's emitter path (``is_stacked`` branch in
    # ``emit_area_layer`` and ``_emit_multi_metric_area``) skips halo
    # layers structurally, so this value is inert there. A single-series area
    # also takes this recipe but is not stacked (``chart.stack`` stays "none"),
    # so it keeps the halo composition, whose halo gate is
    # ``halo_multiplier != 0``: for that chart this value is the only thing
    # suppressing the halo, and the theme must keep it at 0.
    halo_multiplier: float | None = Field(
        default=None,
        description="Halo stroke width multiplier for the stacked recipe; 0 disables the halo.",
    )


class AreaMarkStyle(BaseModel):
    """Area mark fill opacity and shape.

    Stroke/halo geometry for the area's top-edge line lives on ``LineMarkStyle``
    (area's ``marks.line`` slot) — Vega-Lite itself compiles an area's edge
    line as a genuine separate ``line`` mark (confirmed: ``mark: {type: area,
    line: {...}}`` compiles to independent ``area``/``line``/``symbol`` marks
    in the Vega output), and dbt charts' emitter does the same by hand. Area
    owns only what's genuinely area-shaped: fill opacity and the interpolation
    curve (curve is a single shared value across the fill AND its edge line —
    they trace the same silhouette, so there is exactly one source of truth).

    All fields None = "not set at this tier; inherit from global".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Cascade tier sentinel: None means "not specified at this tier".
    opacity: float | None = Field(default=None, description="Area fill opacity (0–1).")
    # Cascade tier sentinel: None means "not specified at this tier".
    curve: Curve | None = Field(
        default=None,
        description=(
            "Area interpolation curve, one of a fixed set: 'linear', 'monotone', "
            "'natural', 'basis', 'cardinal', 'step', 'step-before', 'step-after'. "
            "On a categorical (nominal/ordinal) x-axis, 'step' draws a "
            "full-band-width plateau per x-value instead of VL's centered step; "
            "on a continuous (temporal/quantitative) x-axis it passes straight "
            "through to VL's native step. Applied to both the fill and its edge "
            "line (marks.line) so they trace the same path."
        ),
    )
    # Opaque background-color backdrop painted behind the (possibly
    # translucent) fill so its tint reads consistently regardless of what's
    # behind it (grid lines, an overlapping series). A plain on/off switch —
    # unlike a stroke, a fill has no "width" dimension to scale, so this is
    # NOT the same knob as marks.line.halo_multiplier (which sizes the edge
    # line's own halo and has no effect on this backdrop).
    # Cascade tier sentinel: None means "not specified at this tier".
    backdrop: bool | None = Field(
        default=None,
        description="Whether to paint an opaque fill backdrop behind the area.",
    )
    # NB: no ``connect`` field on area. Line-mark's ``connect: false`` breaks
    # the stroke into per-band segments — a coherent line semantic. On area,
    # the same toggle produces per-band filled rectangles, which is just a bar
    # chart via the wrong primitive; authors who want per-band target markers
    # should use ``type: bar`` (or a rule with ``x``/``x2``).
    # Override block applied when the chart is stacked (zero/normalize/center)
    # or is a single unstacked series: solid fill + a background-color
    # separator stroke replaces the overlap translucent-halo recipe
    # (mirrors the bar.border background-knockout idiom for stacked segments).
    # opacity here overrides AreaMarkStyle.opacity; stroke/halo_multiplier
    # override the LINE mark's stroke/halo_multiplier (same edge-is-a-line
    # reasoning as above — the stacked recipe's perimeter stroke is drawn via
    # the same fg-line mechanism as the overlap recipe's top-edge stroke).
    # Cascade tier sentinel: None means "not specified at this tier".
    # SkipInheritSlots(cascade=True): apply_inherit copies the entire stacked
    # object from the parent slot when stacked is None.
    stacked: Annotated[AreaStackedMarkStyle | None, SkipInheritSlots(cascade=True)] = (
        Field(
            default=None,
            description=(
                "Recipe override applied when the chart is stacked or has a "
                "single series: solid fill + background-color separator stroke."
            ),
        )
    )


class PointMarkStyle(BaseModel):
    """Point mark style (data-point markers on scatter/point/line/area charts)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    size: float | None = Field(
        default=None,
        description="Point size in square pixels; 0 disables points on line charts.",
    )
    color: Annotated[str | None, Color()] = Field(
        default=None, description="Point color; None inherits the series color."
    )
    shape: str | None = Field(
        default=None,
        description="Point shape (e.g. 'circle', 'square'); None uses VL default.",
    )
    opacity: float | None = Field(
        default=None, description="Point opacity 0–1; None uses VL default."
    )
    filled: bool | None = Field(
        default=None, description="Whether points are filled; None uses VL default."
    )
    fill: Annotated[str | None, Color()] = Field(
        default=None,
        description="Point interior fill color; only applied when filled=false.",
    )
    stroke_width: float | None = Field(
        default=None,
        description="Stroke width in pixels for hollow point rings; None uses VL default.",
    )
    # SkipInheritSlots(cascade=True): leaf fields inside labels inherit individually
    # from the parent slot (no container-copy since labels is never None).
    labels: Annotated[PointLabelsStyle, SkipInheritSlots(cascade=True)] = Field(
        default_factory=PointLabelsStyle,
        description=(
            "Value label style for point marks. On line charts, setting "
            "marks.point.labels is an alias for marks.line.labels."
        ),
    )


class SliceMarkStyle(BaseModel):
    """Pie/donut slice mark: mark-level paint and layout only.

    No chart geometry (aspect_ratio, inner_radius), no sub-components (total).
    All fields None = "not set at this tier; inherit from global".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Cascade tier sentinel: None means "not specified at this tier".
    opacity: float | None = Field(
        default=None,
        description="Arc slice opacity (0–1); None means not overridden at this level.",
    )
    # Cascade tier sentinel: None means "not specified at this tier".
    gap: float | None = Field(
        default=None, description="Angular gap between slices in radians."
    )
    # Cascade tier sentinel: None means "not specified at this tier".
    corner_radius: float | None = Field(
        default=None, description="Corner radius of arc slices in pixels."
    )
    # Cascade tier sentinel: None means "not specified at this tier".
    # SkipInheritSlots(cascade=True): apply_inherit copies the entire stroke object
    # from the parent slot when stroke is None.
    stroke: Annotated[StrokeStyle | None, SkipInheritSlots(cascade=True)] = Field(
        default=None, description="Arc slice stroke style."
    )
    # Cascade tier sentinel: None means "not set at this tier; inherit whole object".
    # SkipInheritSlots(cascade=True): apply_inherit copies the entire labels object
    # from the parent slot when labels is None (SliceLabelsStyle | None, so None is
    # routine here — unlike bar/line/point where labels is never None).
    labels: Annotated[SliceLabelsStyle | None, SkipInheritSlots(cascade=True)] = Field(
        default=None, description="Per-slice label style."
    )


class RuleMarkStyle(BaseModel):
    """Rule (reference line) mark opacity and stroke."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    opacity: float | None = Field(
        default=None,
        description="Mark opacity (0–1); None means not overridden at this level.",
    )
    stroke: FontColorStrokeStyle = Field(
        default_factory=FontColorStrokeStyle,
        description="Mark stroke style.",
    )


class RectMarkStyle(BaseModel):
    """Rect mark opacity and stroke."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    opacity: float | None = Field(
        default=None,
        description="Mark opacity (0–1); None means not overridden at this level.",
    )
    # SkipInheritSlots(cascade=True): apply_inherit copies the entire stroke object
    # from the parent slot when stroke is None (heatmap.marks.rect slot).
    stroke: Annotated[StrokeStyle | None, SkipInheritSlots(cascade=True)] = Field(
        default=None,
        description="Mark stroke style; None means not overridden at this level.",
    )


class CircleMarkStyle(BaseModel):
    """Circle mark opacity and stroke."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    opacity: float | None = Field(
        default=None,
        description="Mark opacity (0–1); None means not overridden at this level.",
    )
    # SkipInheritSlots: nullable container; apply_inherit can't navigate through None parent.
    stroke: Annotated[StrokeStyle | None, SkipInheritSlots()] = Field(
        default=None,
        description="Unset emits no stroke; Vega-Lite's default applies.",
    )


class GeoshapeMarkStyle(BaseModel):
    """Geoshape (choropleth) mark fill and boundary stroke.

    Cascade tier sentinel: None means "not specified at this tier".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Cascade tier sentinel: None means "not specified at this tier".
    fill: Annotated[str | None, Color()] = Field(
        default=None, description="Neutral geoshape fill color."
    )
    # SkipInheritSlots(cascade=True): apply_inherit copies the entire stroke object
    # from the parent slot when stroke is None (geoshape.marks.geoshape slot).
    stroke: Annotated[StrokeStyle | None, SkipInheritSlots(cascade=True)] = Field(
        default=None, description="Geoshape boundary stroke style."
    )


class GlobalMarksStyle(BaseModel):
    """Global mark defaults: one <Mark>MarkStyle per VL mark type.

    Tier 1 of the three-tier mark cascade:
        GlobalMarksStyle → <Family>ChartMarksStyle → board-local marks patch.

    Keys match VL mark type names. ``text`` is the renamed ``label`` slot.
    All fields have default_factory so the theme YAML can omit rarely-styled
    marks without breaking validation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    bar: BarMarkStyle = Field(
        default_factory=BarMarkStyle,
        description="Global bar mark defaults.",
    )
    line: LineMarkStyle = Field(
        default_factory=LineMarkStyle,
        description="Global line mark defaults.",
    )
    area: AreaMarkStyle = Field(
        default_factory=AreaMarkStyle,
        description="Global area mark defaults.",
    )
    point: PointMarkStyle = Field(
        default_factory=PointMarkStyle,
        description="Global point mark defaults.",
    )
    slice: SliceMarkStyle = Field(
        default_factory=SliceMarkStyle,
        description="Global slice (arc/pie) mark defaults.",
    )
    # `text` is the renamed `label` slot. ChartsStyle rejects the old `label` key.
    text: TextMarkStyle = Field(
        default_factory=TextMarkStyle,
        description="Global text mark defaults.",
    )
    rule: RuleMarkStyle = Field(
        default_factory=RuleMarkStyle,
        description="Global rule mark defaults.",
    )
    rect: RectMarkStyle = Field(
        default_factory=RectMarkStyle,
        description="Global rect mark defaults.",
    )
    circle: CircleMarkStyle = Field(
        default_factory=CircleMarkStyle,
        description="Global circle mark defaults.",
    )
    geoshape: GeoshapeMarkStyle = Field(
        default_factory=GeoshapeMarkStyle,
        description="Global geoshape mark defaults.",
    )


class SubtitleStyle(BaseModel):
    """Subtitle font style for chart subtitles (Table, SparkBar).

    Theme YAML populates font.size directly — no delta/floor math.
    Values live in stark.yaml style.charts.*.subtitle.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # FontStyle is all-Optional (it's a patch type); default_factory=FontStyle
    # creates an empty overlay that the theme YAML fills with font.size.
    # Same justified exception as KpiValueStyle.font.
    font: FontStyle = Field(
        default_factory=FontStyle,
        description="Subtitle font style; theme populates font.size directly.",
    )
