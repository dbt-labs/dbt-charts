"""Theme-stage style classes: chart-family style base class hierarchy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    # These authored types are injected into theme/__init__.py's module globals at
    # runtime by dbt_charts.core.compile.models.style.authored (after authored.py
    # finishes executing). The import here is TYPE_CHECKING-only to give mypy and
    # ruff the static definitions they need without creating a circular import.
    from dbt_charts.core.compile.models.style.authored import (  # noqa: PLC0415
        AxisXStylePatch,
        AxisYStylePatch,
        BandAxisStylePatch,
        BaseAxisStylePatch,
        QuantitativeAxisStylePatch,
        SupportTableStylePatch,
    )

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.factories import (
    build_patch_model_ext,
)
from dbt_charts.core.compile.models.markers import (
    Color,
    Format,
    Inherit,
    InheritSlot,
    SkipInheritSlots,
)
from dbt_charts.core.compile.models.primitives import (
    BorderStyle,
    ColorStyle,
    FontStyle,
    FormatConfig,
    StaticGradientColorStyle,
)
from dbt_charts.core.compile.models.schema_names import (
    NumberFormatAlias,
    TimeFormatAlias,
)
from dbt_charts.core.compile.models.style.theme.board import (
    PaddingStyle,
    TitleStyle,
)
from dbt_charts.core.compile.models.style.theme.legend import (
    LegendStyle,
    PieLegendStyle,
)
from dbt_charts.core.compile.models.style.theme.marks import (
    BasemapStyle,
    ProjectionStyle,
    TotalStyle,
)

# Built locally (not injected from authored/, unlike the axis patches above)
# since TitleStyle is already a normal, non-forward-ref import here: building
# it eagerly avoids deferring _ChartStyleBase's own field resolution to a
# model_rebuild() that, unlike its subclasses, it never otherwise receives.
# build_patch_model_ext is @cache'd, so authored/_base.py's later
# build_patch_model(TitleStyle) call for the authored surface returns this
# exact object rather than building a second one.
if TYPE_CHECKING:

    class TitleStylePatch(TitleStyle):
        pass

else:
    TitleStylePatch = build_patch_model_ext(TitleStyle, is_recursive=True)


class _ChartStyleBase(BaseModel):
    """Required geometry/frame fields — the cascade source for all chart families.

    ``ChartsStyle`` inherits this class to supply the authoritative global values.
    ``_ChartStyleBaseAllOptional`` (generated via ``build_patch_model``) is used as
    the per-family base for kpi/table, where ``None`` on any field means "inherit from
    ``ChartsStyle`` via the cascade".  Painting families inherit
    ``_PaintedChartStyleBaseAllOptional`` instead, which additionally carries
    ``legend`` and the three sizing fields.

    ``font``/``border`` are deliberately NOT declared here. Only families with a
    hand-drawn per-chart card render surface (kpi, table, spark_bar, callout,
    the geo families via ``_GeoChartStyle``) consume a per-chart font/border —
    each of those declares its own ``font``/``border`` field directly. Bar,
    line, area, scatter, histogram, heatmap, pie, and donut have no such
    surface: ``ChartsStyle`` (the board-level authoritative source) declares
    ``font``/``border`` itself in ``charts.py``, and the eight VL-emitted
    families never redeclare them, so authoring ``style.font``/``style.border``
    on those families raises ``extra_forbidden`` instead of validating and
    silently doing nothing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    preferred_width: Annotated[
        float, Inherit(from_path="Style.charts.preferred_width")
    ] = Field(description="Preferred chart width in pixels.")
    # InheritSlot: per-family padding inherits per-side from Style.charts.padding.
    # ChartsStyle (the board-level slot) overrides this with SkipInheritSlots to
    # break the self-loop the InheritSlot would otherwise create at the top of
    # the chain.
    padding: Annotated[PaddingStyle, InheritSlot(from_path="Style.charts.padding")] = (
        Field(description="Per-chart-type padding override; 4 sides in pixels.")
    )

    # Cascade-managed sentinels — chart-local paint and typography overrides.
    # None propagates unchanged through the cascade; no theme-level default
    # exists for these per-chart fields. ChartsStyle inherits these as optional
    # fields that theme YAML may omit.
    background: Annotated[str | None, Color()] = Field(
        default=None,
        description="Chart-local background color override; None inherits from theme.",
    )
    # TitleStylePatch (all-Optional), not TitleStyle: authoring only
    # font.family must not force every other TitleStyle required field
    # (compact_weight, sizes, ...) alongside it. SkipInheritSlots (no
    # cascade): per-family title overrides (charts.kpi.title, charts.bar.title,
    # ...) stay sparse patches — giving every per-family class its own
    # cascade fill here would make it inherit straight from Style.title,
    # bypassing the board-level charts.title override entirely. ChartsStyle
    # (the one board-wide slot) overrides this field with its own
    # InheritSlot(from_path="Style.title") below, same split as
    # padding/SkipInheritSlots above.
    title: Annotated[TitleStylePatch | None, SkipInheritSlots()] = Field(
        default=None,
        description="Chart-level title style override; None inherits the theme title style.",
    )


if TYPE_CHECKING:
    # build_patch_model_ext returns a dynamically-created type[BaseModel] at
    # runtime; mypy needs a real class definition to accept it as a base class
    # (KpiChartStyle, TableChartStyle). No color narrowing needed here anymore
    # — color moved to _PaintedChartStyleBase, so every field on this class
    # keeps its _ChartStyleBase-declared type.
    class _ChartStyleBaseAllOptional(_ChartStyleBase):
        pass

else:
    _ChartStyleBaseAllOptional = build_patch_model_ext(
        _ChartStyleBase, is_recursive=True
    )


class _ChartCardStyleMixin(BaseModel):
    """font/border for families with a hand-drawn per-chart card render surface.

    Not part of ``_ChartStyleBase`` (see its docstring) — kpi/table/spark_bar/
    callout declare font/border directly on their own theme classes, and
    ``_GeoChartStyle`` mixes this in (geo is out of this task's narrowing
    scope). Goes through ``build_patch_model_ext`` below, same as
    ``_ChartStyleBase``, so the Optional variant mirrors the shape that used
    to come from inheriting ``_ChartStyleBase`` directly — no behavior change
    for geoshape/point_map.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    font: FontStyle = Field(
        default_factory=FontStyle,
        description="Chart-level font overrides.",
    )
    border: BorderStyle = Field(description="Chart card border style.")


if TYPE_CHECKING:

    class _ChartCardStyleMixinAllOptional(_ChartCardStyleMixin):
        pass

else:
    _ChartCardStyleMixinAllOptional = build_patch_model_ext(
        _ChartCardStyleMixin, is_recursive=True
    )


class _PaintedChartStyleBase(_ChartStyleBase):
    """Fields for families with their own geometry and a legend: cartesian, radial, and geo.

    KPI and table use a fixed sizing contract and paint no legend — they inherit
    ``_ChartStyleBaseAllOptional`` directly, so ``extra="forbid"`` rejects these
    fields structurally.  No family (including these nine) declares ``tooltip`` at
    the per-family level — the only authoritative tooltip slot is ``ChartsStyle.tooltip``
    (global, board-wide), which is how every real tooltip consumer reads it.

    ``color`` lives here rather than on ``_ChartStyleBase`` — KPI and table paint
    no series/mark color channel (KPI has no series axis at all; table overrides
    the field with its own narrower type), so ``color`` is structurally absent
    from ``_ChartStyleBaseAllOptional`` and every family that inherits it
    directly. Painting families get the field from here; table re-declares its
    own ``color`` independently (not an override of this one).
    """

    # Inherit markers on scalars: picked up by build_patch_model_ext and forwarded
    # to _PaintedChartStyleBaseAllOptional so per-family None sentinels resolve from ChartsStyle.
    aspect_ratio: Annotated[float, Inherit(from_path="Style.charts.aspect_ratio")] = (
        Field(description="Chart aspect ratio (width/height).")
    )
    min_height: Annotated[float, Inherit(from_path="Style.charts.min_height")] = Field(
        description="Minimum chart height in pixels."
    )
    max_height: Annotated[float, Inherit(from_path="Style.charts.max_height")] = Field(
        description="Maximum chart height in pixels."
    )
    # Unified color config: static ink, categorical palette, and gradient scale.
    # Required at ChartsStyle level (theme must supply color.categorical).
    # Per-family patches (derived via build_patch_model_ext) get ColorStyle | None.
    # No Merge marker: ColorStyle is a BaseModel, so merge_patches uses Strategy.DEEP by
    # default. This lets theme patches contribute individual color sub-fields
    # (e.g. one layer sets categorical.palette, another sets categorical.single_series_palette)
    # without the later layer wiping out the earlier one.
    color: ColorStyle = Field(
        description="Chart color: static mark paint, categorical palette, and/or gradient scale."
    )


if TYPE_CHECKING:

    class _PaintedChartStyleBaseAllOptional(_PaintedChartStyleBase):
        # At runtime build_patch_model_ext makes every _PaintedChartStyleBase field Optional;
        # declare color | None so callers on _PaintedChartStyleBaseAllOptional subclasses
        # know they must guard before accessing .static/.gradient.
        # The assignment is narrowing (ColorStyle → ColorStyle | None) — intentional.
        color: ColorStyle | None  # type: ignore[assignment]

else:
    _PaintedChartStyleBaseAllOptional = build_patch_model_ext(
        _PaintedChartStyleBase, is_recursive=True
    )


class _LegendedChartStyleBase(BaseModel):
    """The legend slot for families placed along a plot edge (not pie/donut)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    legend: LegendStyle = Field(description="Chart legend style.")


class _PieLegendedChartStyleBase(BaseModel):
    """The legend slot for pie and donut."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    legend: PieLegendStyle = Field(description="Pie key style.")


if TYPE_CHECKING:

    class _LegendedChartStyleBaseAllOptional(_LegendedChartStyleBase):
        pass

    class _PieLegendedChartStyleBaseAllOptional(_PieLegendedChartStyleBase):
        pass

else:
    _LegendedChartStyleBaseAllOptional = build_patch_model_ext(
        _LegendedChartStyleBase, is_recursive=True
    )
    _PieLegendedChartStyleBaseAllOptional = build_patch_model_ext(
        _PieLegendedChartStyleBase, is_recursive=True
    )


class _CartesianChartStyle(
    _PaintedChartStyleBaseAllOptional, _LegendedChartStyleBaseAllOptional
):
    """Cartesian families: bar, line, area, scatter, histogram, heatmap.

    Shared axis tier — theme YAML populates per-family axis overrides here.
    ``axis_quantitative`` is NOT declared here — see
    ``_QuantitativeAxisChartStyleMixin`` below.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # SkipInheritSlots: per-family axis overrides are sparse patches — only the
    # fields a theme explicitly authors for the family are set; every other field
    # inherits the global axis at render time via resolved_axis_style's merge over
    # the fully-resolved base axis. Without this, apply_inherit slot-fills these
    # patches from the generic charts.font (prose ink) — so merely authoring an
    # unrelated field like bar.axis_x.labels.padding materializes labels.font and
    # drags prose ink into the label color, overriding the theme's muted axis
    # color at the merge. Mirrors the `title` sentinel above (chart-local title
    # is likewise a skip-slots sparse patch). The axis element font/color/family
    # cascade is owned by the global axis nodes, not the per-family override.
    axis: Annotated[BaseAxisStylePatch | None, SkipInheritSlots()] = Field(
        default=None,
        description="Override applied to both x and y axes; None inherits the global axis at render.",
    )
    axis_x: Annotated[AxisXStylePatch | None, SkipInheritSlots()] = Field(
        default=None,
        description="Per-chart-type x-axis style overrides; None inherits the global axis_x at render.",
    )
    axis_y: Annotated[AxisYStylePatch | None, SkipInheritSlots()] = Field(
        default=None,
        description="Per-chart-type y-axis style overrides; None inherits the global axis_y at render.",
    )
    axis_band: Annotated[BandAxisStylePatch | None, SkipInheritSlots()] = Field(
        default=None,
        description="Per-chart-type categorical (band) axis overrides; None inherits the global band axis at render.",
    )
    number_format: Annotated[
        NumberFormatAlias | str | FormatConfig | None, Format(kind="number")
    ] = Field(
        default=None,
        description="Default number format for axes and tooltips (D3 format string); None inherits from theme.",
    )
    time_format: Annotated[TimeFormatAlias | str | None, Format(kind="time")] = Field(
        default=None,
        description="Default time format for temporal axes (D3 time format string or strftime spec like '%b %Y'); None inherits from theme.",
    )
    # InheritSlot: per-family support_table inherits from Style.charts.support_table.
    # Field stays nullable so theme YAML can omit it; inherit_graph.py emits a
    # container-level copy link for nullable InheritSlot fields so apply_inherit
    # copies the whole parent when None (and fills sub-fields when partially set).
    support_table: Annotated[
        SupportTableStylePatch | None,
        InheritSlot(from_path="Style.charts.support_table"),
    ] = Field(
        default=None,
        description="Per-chart-type support_table style override.",
    )


class _QuantitativeAxisChartStyleMixin(BaseModel):
    """``axis_quantitative`` — cartesian families with a quantitative axis to style.

    Mixed into bar/line/area/scatter/histogram's theme classes alongside
    ``_CartesianChartStyle``. Heatmap does not inherit this: both its axes
    are nominal (styled via ``axis_band``), so it has no quantitative axis
    for this field to apply to — authoring it on a heatmap is a parse-time
    error rather than a silently inert field.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # SkipInheritSlots: sparse per-family patch, same rationale as
    # _CartesianChartStyle's axis/axis_x/axis_y/axis_band fields above.
    axis_quantitative: Annotated[
        QuantitativeAxisStylePatch | None, SkipInheritSlots()
    ] = Field(
        default=None,
        description="Per-chart-type quantitative-axis overrides; None inherits the global axis_quantitative at render.",
    )


class _RadialChartStyle(
    _PaintedChartStyleBaseAllOptional, _PieLegendedChartStyleBaseAllOptional
):
    """Radial families: pie, donut."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Optional sentinel: None means 'no inner radius' (solid pie); float means donut.
    # The None state is meaningful in the resolved model — NOT a 'theme failed to populate'
    # fallback. Cascade preserves None through merge so the renderer can branch on solid
    # vs. donut without re-reading raw YAML.
    inner_radius: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Hole-to-disk ratio 0–1 (inner radius / outer radius). None = solid pie; `type: donut` overrides this with a chart-local 0.6 patch, beating a theme value.",
    )
    total: TotalStyle = Field(description="Donut center total paint (value and label).")


class _GeoChartStyle(
    _ChartCardStyleMixinAllOptional,
    _PaintedChartStyleBaseAllOptional,
    _LegendedChartStyleBaseAllOptional,
):
    """Geo families: geoshape (choropleth), point_map.

    ``geo_source`` is intentionally absent — it is a chart-root channel field
    (on ``_GeoChartFields``), not a style concern. Mixes in
    ``_ChartCardStyleMixinAllOptional`` for font/border — geo is out of scope
    for the cartesian/pie/donut card-style narrowing (see ``_ChartStyleBase``'s
    docstring); this keeps geoshape/point_map accepting both fields exactly as
    before.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Override: geo families do not support categorical color (extra_forbidden via StaticGradientColorStyle).
    color: StaticGradientColorStyle | None = Field(  # type: ignore[assignment]
        default=None,
        description="Geo color: static paint or gradient scale only (no categorical arm).",
    )
    projection: ProjectionStyle = Field(
        description="Vega-Lite projection configuration for this geo family."
    )
    basemap: BasemapStyle = Field(
        default_factory=BasemapStyle,
        description="Background map layer; None source = no topo layer.",
    )
