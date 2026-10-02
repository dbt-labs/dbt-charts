"""Shared/global style patches not tied to a single chart family.

Includes the top-level ``StylePatch`` (the all-encompassing authored overlay)
and the cross-cutting axis/scale/title/padding/tooltip/legend/mark patches
that chart-family patch files import.

Some patches here (``BaseAxisStylePatch``, ``AxisXStylePatch``,
``AxisYStylePatch``, ``SupportTableStylePatch``, ``PaddingStylePatch``,
``TableChartStylePatch``) are built early and injected back into
``style.theme``'s module globals so that Pydantic can resolve forward
references in theme chart-style classes — see the injection block below.
``theme.py`` must not import from this package, so injection is the only
DAG-safe path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dbt_charts.core.compile.models.factories import (
    _PatchBase,
    build_patch_model,
    build_patch_model_ext,
    register_patch,
)
from dbt_charts.core.compile.models.style.authored.table import (
    PaginationConfig,
    TableChartStylePatch,
    TableColumnConfig,
    TableColumnDefaultsConfig,
)
from dbt_charts.core.compile.models.style.theme import (
    AreaChartStyle,
    AreaLayerStyle,
    AreaMarkStyle,
    AxisLabelStyle,
    AxisLineStyle,
    AxisTicksStyle,
    AxisTitleStyle,
    AxisXStyle,
    AxisYStyle,
    BandAxisStyle,
    BarChartStyle,
    BarLayerStyle,
    BarMarkStyle,
    BaseAxisGridStyle,
    BaseAxisStyle,
    BaseScaleStyle,
    ChartsStyle,
    DimensionLabelStyle,
    DimensionTicksStyle,
    GeoshapeChartStyle,
    GlobalMarksStyle,
    HeatmapChartStyle,
    HistogramChartStyle,
    KpiChartStyle,
    LegendStyle,
    LineChartStyle,
    LineLayerStyle,
    LineMarkStyle,
    PaddingStyle,
    PieChartStyle,
    PieLegendStyle,
    PointMapChartStyle,
    PointMarkStyle,
    QuantitativeAxisStyle,
    ScaleContinuousStyle,
    ScatterChartStyle,
    ScatterLayerStyle,
    Style,
    SupportTableStyle as SupportTableStyle,
    TableChartStyle,
    TitleStyle,
    TotalSlotStyle,
    TotalStyle,
    XScaleStyle,
    _CartesianChartStyle,
    _GeoChartStyle,
    _QuantitativeAxisChartStyleMixin,
    _RadialChartStyle,
    coerce_gap,
)

# ── Scale group patches ───────────────────────────────────────────────────────
# Generated first (continuous is embedded in BaseScaleStyle, which is
# embedded in BaseAxisStyle). Order: leaf group → BaseScaleStyle → XScaleStyle.

# ScaleContinuousStylePatch carries ScaleDomainValidationMixin (the domain
# temporal-type check) via build_patch_model_ext, but NOT
# _validate_log_pow_symlog_params —
# that validator is defined directly on ScaleContinuousStyle (not on a mixin),
# so build_patch_model_ext naturally excludes it from the patch. A theme layer
# can set type:log and a chart-local patch can set only log.base; the
# cross-field check fires once, on the fully-merged result via
# ScaleContinuousStyle.model_validate.
if TYPE_CHECKING:

    class ScaleContinuousStylePatch(ScaleContinuousStyle):
        pass

else:
    from dbt_charts.core.compile.models.style.theme import ScaleDomainValidationMixin

    class _ScaleContinuousStylePatchBase(_PatchBase, ScaleDomainValidationMixin):
        """Base for ScaleContinuousStylePatch: extra="forbid" from _PatchBase
        plus the authored scale.domain validation from ScaleDomainValidationMixin."""

    ScaleContinuousStylePatch = register_patch(ScaleContinuousStyle)(
        build_patch_model_ext(
            ScaleContinuousStyle, base_cls=_ScaleContinuousStylePatchBase
        )
    )

BaseScaleStylePatch = build_patch_model(BaseScaleStyle)
XScaleStylePatch = build_patch_model(XScaleStyle)

# ── Axis ticks patches ─────────────────────────────────────────────────────────
# Generated (and registered) BEFORE axis variant patches so the recursive
# axis-variant build reuses these exact classes for their ``ticks`` field.
# Neither patch carries the step/time_unit cadence validator — same
# ScaleContinuousStyle convention above: the check is defined directly on
# DimensionTicksStyle (mode="after"), not a mixin, and build_patch_model_ext
# only carries over validators living on the patch's own base_cls, so it does
# NOT leak into either generated patch. It fires once, on the fully-merged
# theme-tier result, since a theme may set time_unit and a chart-local patch
# layer may set only step.
AxisTicksStylePatch = build_patch_model(AxisTicksStyle)
DimensionTicksStylePatch = build_patch_model(DimensionTicksStyle)


# ── Axis leaf patches ─────────────────────────────────────────────────────────
# Generated before axis variant patches so the recursive variant builds reuse
# these exact classes for their sub-object fields.
AxisGridThresholdStylePatch: type  # declared for forward reference clarity
from dbt_charts.core.compile.models.style.theme.axis import (
    AxisGridThresholdStyle,  # noqa: PLC0415, E402
)

AxisGridThresholdStylePatch = build_patch_model(AxisGridThresholdStyle)
BaseAxisGridStylePatch = build_patch_model(BaseAxisGridStyle)
AxisLineStylePatch = build_patch_model(AxisLineStyle)
AxisTitleStylePatch = build_patch_model(AxisTitleStyle)
AxisLabelStylePatch = build_patch_model(AxisLabelStyle)
DimensionLabelStylePatch = build_patch_model(DimensionLabelStyle)


# ── Axis variant patches ──────────────────────────────────────────────────────
# Five axis-variant patches, each typed to the matching authored slot so extra
# fields on the wrong variant raise at validation time.
# TYPE_CHECKING stubs give mypy proper class definitions — at runtime
# build_patch_model generates the actual Pydantic model.
if TYPE_CHECKING:

    class BaseAxisStylePatch(BaseAxisStyle):
        pass

    class AxisXStylePatch(AxisXStyle):
        pass

    class AxisYStylePatch(AxisYStyle):
        pass

    class QuantitativeAxisStylePatch(QuantitativeAxisStyle):
        pass

    class BandAxisStylePatch(BandAxisStyle):
        pass

else:
    BaseAxisStylePatch = build_patch_model(BaseAxisStyle)
    AxisXStylePatch = build_patch_model(AxisXStyle)
    AxisYStylePatch = build_patch_model(AxisYStyle)
    QuantitativeAxisStylePatch = build_patch_model(QuantitativeAxisStyle)
    BandAxisStylePatch = build_patch_model(BandAxisStyle)

if TYPE_CHECKING:

    class SupportTableStylePatch(SupportTableStyle):
        pass

else:
    SupportTableStylePatch = build_patch_model(SupportTableStyle)


# Patch type for padding — generated early so per-family chart-style classes
# (_CartesianChartStyle, _RadialChartStyle, _GeoChartStyle) can reference it
# in their InheritSlot padding overrides.
if TYPE_CHECKING:

    class PaddingStylePatch(PaddingStyle):
        pass

else:
    PaddingStylePatch = build_patch_model(PaddingStyle)


class EndpointLabelsConfig(BaseModel):
    """Series names printed on the chart itself instead of in a legend.

    Also called direct labels, series labels, or an in-chart legend. Each
    series gets one label in its own color, beside the plot: to the right on
    line, area, and vertical stacked bar charts, above the top row on
    horizontal stacked bars.

    Only shapes that can name every series get them. Line and area charts and
    stacked bars do; grouped bars, layered charts, small multiples, and very
    narrow cards keep a legend instead. Where the labels do appear they replace the color
    legend, so `visible` here, and not `legend`, is what takes series names
    off the side of those charts. Their font comes from
    `style.charts.series_label.font`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    visible: bool = Field(
        description=(
            "Print series names on the chart instead of in a legend. True on "
            "every built-in theme, and used wherever the shape can name every "
            "series: line and area charts, and stacked bars. Grouped bars, "
            "layered charts, small multiples, and very narrow cards keep a "
            "legend instead. "
            "Set false to move the names back into a legend everywhere. Where "
            "the labels do appear they replace the color legend, so this "
            "setting and not `legend` is what removes them."
        )
    )
    label_offset: float = Field(
        description="Gap in pixels between the plot and the series labels."
    )
    height: float = Field(
        description=(
            "Height in pixels of the label strip above a horizontal stacked bar."
        )
    )


# Inject authored types into theme.py's module globals so that Pydantic
# can resolve forward references in _CartesianChartStyle, BarChartStyle, etc.
# when build_patch_model(Style) triggers schema building for those classes.
# theme.py must not import from authored/, so injection is the only DAG-safe path.
# setattr avoids type: ignore[attr-defined] on a dynamically-typed module object.
import sys as _sys

_theme_mod = _sys.modules["dbt_charts.core.compile.models.style.theme"]
for _name, _obj in (
    ("BaseAxisStylePatch", BaseAxisStylePatch),
    ("AxisXStylePatch", AxisXStylePatch),
    ("AxisYStylePatch", AxisYStylePatch),
    ("QuantitativeAxisStylePatch", QuantitativeAxisStylePatch),
    ("BandAxisStylePatch", BandAxisStylePatch),
    ("BaseScaleStylePatch", BaseScaleStylePatch),
    ("XScaleStylePatch", XScaleStylePatch),
    ("SupportTableStylePatch", SupportTableStylePatch),
    ("PaddingStylePatch", PaddingStylePatch),
    ("TableChartStylePatch", TableChartStylePatch),
    ("EndpointLabelsConfig", EndpointLabelsConfig),
    ("PaginationConfig", PaginationConfig),
    ("TableColumnDefaultsConfig", TableColumnDefaultsConfig),
    ("TableColumnConfig", TableColumnConfig),
):
    setattr(_theme_mod, _name, _obj)
del _theme_mod, _name, _obj

# Rebuild theme chart classes that forward-ref authored types.
# Must run BEFORE build_patch_model(BarChartStyle) etc. so that model_fields
# on chart-style classes reflect the real authored types, not unresolved
# ForwardRefs. The patch factory uses field annotations to determine recursive
# types; a ForwardRef for EndpointLabelsConfig produces the wrong patch.
_authored_ns: dict[str, object] = {
    "BaseAxisStylePatch": BaseAxisStylePatch,
    "AxisXStylePatch": AxisXStylePatch,
    "AxisYStylePatch": AxisYStylePatch,
    "QuantitativeAxisStylePatch": QuantitativeAxisStylePatch,
    "BandAxisStylePatch": BandAxisStylePatch,
    "BaseScaleStylePatch": BaseScaleStylePatch,
    "XScaleStylePatch": XScaleStylePatch,
    "SupportTableStylePatch": SupportTableStylePatch,
    "PaddingStylePatch": PaddingStylePatch,
    "TableChartStylePatch": TableChartStylePatch,
    "EndpointLabelsConfig": EndpointLabelsConfig,
    "PaginationConfig": PaginationConfig,
    "TableColumnDefaultsConfig": TableColumnDefaultsConfig,
    "TableColumnConfig": TableColumnConfig,
}
_CartesianChartStyle.model_rebuild(_types_namespace=_authored_ns)
_QuantitativeAxisChartStyleMixin.model_rebuild(_types_namespace=_authored_ns)
BarChartStyle.model_rebuild(_types_namespace=_authored_ns)
LineChartStyle.model_rebuild(_types_namespace=_authored_ns)
AreaChartStyle.model_rebuild(_types_namespace=_authored_ns)
ScatterChartStyle.model_rebuild(_types_namespace=_authored_ns)
HistogramChartStyle.model_rebuild(_types_namespace=_authored_ns)
HeatmapChartStyle.model_rebuild(_types_namespace=_authored_ns)
_RadialChartStyle.model_rebuild(_types_namespace=_authored_ns)
PieChartStyle.model_rebuild(_types_namespace=_authored_ns)
_GeoChartStyle.model_rebuild(_types_namespace=_authored_ns)
GeoshapeChartStyle.model_rebuild(_types_namespace=_authored_ns)
PointMapChartStyle.model_rebuild(_types_namespace=_authored_ns)
KpiChartStyle.model_rebuild(_types_namespace=_authored_ns)
TableChartStyle.model_rebuild(_types_namespace=_authored_ns)
# Rebuild the patch model too: it was built before TableChartStyle.model_rebuild(),
# so its pagination/column_defaults annotations are still unresolved ForwardRefs.
TableChartStylePatch.model_rebuild(_types_namespace=_authored_ns)
ChartsStyle.model_rebuild(_types_namespace=_authored_ns)

# Generated and registered before _StylePatchBase so build_patch_model(Style)
# reuses this exact class when recursing into ChartsStyle, giving a stable name
# for ChartStyleContext.charts_board_overrides.
if TYPE_CHECKING:

    class ChartsStylePatch(ChartsStyle):
        pass

else:
    ChartsStylePatch = register_patch(ChartsStyle)(build_patch_model(ChartsStyle))


# build_patch_model_ext is @cache'd: theme/_chart_base.py already built this
# exact TitleStylePatch class (for the chart-local title sentinel field), so
# this call returns that same object rather than generating a second one.
if TYPE_CHECKING:

    class TitleStylePatch(TitleStyle):
        pass

else:
    TitleStylePatch = build_patch_model(TitleStyle)


# Style itself under TYPE_CHECKING: the patch carries Style's field names, so a
# reader typed against Style is checking real ones. Without this alias the class
# has no static fields at all, and reading `style.formats` off it is unresolvable.
if TYPE_CHECKING:
    _StylePatchBase = Style
else:
    _StylePatchBase = build_patch_model(Style)


class StylePatch(_StylePatchBase):
    """Authored overlay for Style: all fields optional. Adds CSS shorthand coercers."""

    @field_validator("gap", mode="before")
    @classmethod
    def _coerce_gap_value(cls, v: Any) -> Any:
        return coerce_gap(v)


# BaseAxisGridStylePatch / AxisDomainStylePatch / AxisElementStylePatch are
# built earlier (before the axis variant patches); they are not rebuilt here.
# AxisTicksStylePatch is built explicitly above (with the cadence validator
# base_cls) — not rebuilt here.
if TYPE_CHECKING:

    class PointMarkStylePatch(PointMarkStyle):
        pass

else:
    PointMarkStylePatch = build_patch_model(PointMarkStyle)

# Built explicitly (rather than left to TotalStylePatch's recursive nested
# generation) so yaml_error_formatter can import it by name to anchor the
# style.total.label text-hint on this exact model.
TotalSlotStylePatch = build_patch_model(TotalSlotStyle)
TotalStylePatch = build_patch_model(TotalStyle)
GlobalMarksStylePatch = build_patch_model(GlobalMarksStyle)

# TYPE_CHECKING stub mirrors the variant-axis-patch pattern — gives mypy a
# proper class (with accurate field names from LegendStyle) while the runtime
# uses the dynamically created patch model from build_patch_model().
if TYPE_CHECKING:

    class LegendStylePatch(LegendStyle):
        pass

else:
    LegendStylePatch = build_patch_model(LegendStyle)

if TYPE_CHECKING:

    class PieLegendStylePatch(PieLegendStyle):
        pass

else:
    PieLegendStylePatch = build_patch_model(PieLegendStyle)


# Individual mark-type patches for per-family style overrides.
if TYPE_CHECKING:

    class BarMarkStylePatch(BarMarkStyle):
        pass

    class LineMarkStylePatch(LineMarkStyle):
        pass

    class AreaMarkStylePatch(AreaMarkStyle):
        pass

    class EndpointLabelsConfigPatch(EndpointLabelsConfig):
        pass

else:
    BarMarkStylePatch = build_patch_model(BarMarkStyle)
    LineMarkStylePatch = build_patch_model(LineMarkStyle)
    AreaMarkStylePatch = build_patch_model(AreaMarkStyle)
    EndpointLabelsConfigPatch = build_patch_model(EndpointLabelsConfig)

# Per-family layer style patches — generated from thin required wrappers in style/theme/.
# build_patch_model recurses: LineLayerStyle{marks: LineChartMarksStyle} →
# LineLayerStylePatch{marks: LineChartMarksStylePatch | None}.
# TYPE_CHECKING stubs mirror the axis-variant-patch / LegendStylePatch pattern so
# mypy sees a valid class while the runtime uses the dynamically-created model.
if TYPE_CHECKING:

    class LineLayerStylePatch(LineLayerStyle):
        pass

    class BarLayerStylePatch(BarLayerStyle):
        pass

    class AreaLayerStylePatch(AreaLayerStyle):
        pass

    class ScatterLayerStylePatch(ScatterLayerStyle):
        pass

else:
    LineLayerStylePatch = build_patch_model(LineLayerStyle)
    BarLayerStylePatch = build_patch_model(BarLayerStyle)
    AreaLayerStylePatch = build_patch_model(AreaLayerStyle)
    ScatterLayerStylePatch = build_patch_model(ScatterLayerStyle)
