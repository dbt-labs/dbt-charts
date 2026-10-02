"""Style authored models — all-Optional *Patch overlays for YAML authoring.

Patch classes are either:
  - hand-written (class XxxPatch(BaseModel): ...)
  - generated mechanically (XxxPatch = build_patch_model(XxxStyle))

Generated patches depend on their theme-stage counterparts imported from
``style.theme``.

Package layout (one family per file, mirroring chart/normalized/):
  _base.py      — shared/global patches (StylePatch, axis/scale/title/…)
  table.py      — table chart patch + table column configuration types
  bar.py        — BarChartStylePatch
  line.py       — LineChartStylePatch
  area.py       — AreaChartStylePatch
  scatter.py    — ScatterChartStylePatch
  heatmap.py    — HeatmapChartStylePatch
  geoshape.py   — GeoshapeChartStylePatch
  point_map.py  — PointMapChartStylePatch
  pie.py        — PieChartStylePatch
  kpi.py        — KpiChartStylePatch
  callout.py    — CalloutChartStylePatch
  spark_bar.py  — SparkBar*StylePatch family

This module assembles the chart-local ``ChartStylePatch`` discriminated overlay
and re-exports every public name from the files above.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Load-bearing import order: `_base` must be imported before any per-family
# submodule below. `_base` injects authored types into `style.theme`'s module
# globals and calls `model_rebuild()` on the theme chart-style classes so their
# EndpointLabelsConfig/etc. forward refs resolve; each per-family submodule's
# own `build_patch_model(FooChartStyle)` call reads those (by-then-resolved)
# field annotations at *its own* import time. Moving `_base` below a per-family
# import would silently rebuild the wrong (unresolved-ForwardRef) shape.
# Regression coverage: test_style_authored_bootstrap_order.py.
from dbt_charts.core.compile.models.style.authored._base import (
    AreaMarkStylePatch,
    AxisGridThresholdStylePatch,
    AxisLabelStylePatch,
    AxisLineStylePatch,
    AxisTicksStylePatch,
    AxisTitleStylePatch,
    AxisXStylePatch,
    AxisYStylePatch,
    BandAxisStylePatch,
    BarMarkStylePatch,
    BaseAxisGridStylePatch,
    BaseAxisStylePatch,
    BaseScaleStylePatch,
    ChartsStylePatch,
    DimensionLabelStylePatch,
    DimensionTicksStylePatch,
    EndpointLabelsConfig,
    EndpointLabelsConfigPatch,
    GlobalMarksStylePatch,
    LegendStylePatch,
    LineMarkStylePatch,
    PaddingStylePatch,
    PieLegendStylePatch,
    PointMarkStylePatch,
    QuantitativeAxisStylePatch,
    ScaleContinuousStylePatch,
    StylePatch,
    SupportTableStylePatch,
    TitleStylePatch,
    TotalSlotStylePatch,
    TotalStylePatch,
    XScaleStylePatch,
)
from dbt_charts.core.compile.models.style.authored.area import AreaChartStylePatch
from dbt_charts.core.compile.models.style.authored.bar import BarChartStylePatch
from dbt_charts.core.compile.models.style.authored.callout import (
    CalloutChartStylePatch,
)
from dbt_charts.core.compile.models.style.authored.geoshape import (
    GeoshapeChartStylePatch,
)
from dbt_charts.core.compile.models.style.authored.heatmap import (
    HeatmapChartStylePatch,
)
from dbt_charts.core.compile.models.style.authored.kpi import KpiChartStylePatch
from dbt_charts.core.compile.models.style.authored.line import LineChartStylePatch
from dbt_charts.core.compile.models.style.authored.pie import PieChartStylePatch
from dbt_charts.core.compile.models.style.authored.point_map import (
    PointMapChartStylePatch,
)
from dbt_charts.core.compile.models.style.authored.scatter import (
    ScatterChartStylePatch,
)
from dbt_charts.core.compile.models.style.authored.spark_bar import (
    SparkBarBarStylePatch,
    SparkBarChartLabelStylePatch,
    SparkBarChartStylePatch,
    SparkBarCountStylePatch,
)
from dbt_charts.core.compile.models.style.authored.table import (
    _SPARK_RENAMED_AWAY as _SPARK_RENAMED_AWAY,
    ColumnScaleConfig,
    PaginationConfig,
    SparkConfig,
    SparkStylePatch,
    SparkTypeLiteral,
    TableChartStylePatch,
    TableColumnConfig,
    TableColumnDefaultsConfig,
    _validate_glyph_pair as _validate_glyph_pair,
    fill_table_column_defaults,
)

__all__ = [
    "_SPARK_RENAMED_AWAY",
    "_validate_glyph_pair",
    "AreaChartStylePatch",
    "AreaMarkStylePatch",
    "AxisGridThresholdStylePatch",
    "AxisLabelStylePatch",
    "AxisLineStylePatch",
    "AxisTicksStylePatch",
    "AxisTitleStylePatch",
    "AxisXStylePatch",
    "AxisYStylePatch",
    "BandAxisStylePatch",
    "BaseAxisGridStylePatch",
    "BaseAxisStylePatch",
    "BaseScaleStylePatch",
    "DimensionLabelStylePatch",
    "DimensionTicksStylePatch",
    "QuantitativeAxisStylePatch",
    "ScaleContinuousStylePatch",
    "XScaleStylePatch",
    "BarChartStylePatch",
    "BarMarkStylePatch",
    "CalloutChartStylePatch",
    "ChartStylePatch",
    "ChartsStylePatch",
    "ColumnScaleConfig",
    "SupportTableStylePatch",
    "EndpointLabelsConfig",
    "EndpointLabelsConfigPatch",
    "GeoshapeChartStylePatch",
    "GlobalMarksStylePatch",
    "HeatmapChartStylePatch",
    "KpiChartStylePatch",
    "LegendStylePatch",
    "LineChartStylePatch",
    "LineMarkStylePatch",
    "PaddingStylePatch",
    "PaginationConfig",
    "PieChartStylePatch",
    "PieLegendStylePatch",
    "PointMapChartStylePatch",
    "PointMarkStylePatch",
    "ScatterChartStylePatch",
    "SparkBarBarStylePatch",
    "SparkBarChartLabelStylePatch",
    "SparkBarChartStylePatch",
    "SparkBarCountStylePatch",
    "SparkConfig",
    "SparkStylePatch",
    "SparkTypeLiteral",
    "StylePatch",
    "TableChartStylePatch",
    "TableColumnConfig",
    "TableColumnDefaultsConfig",
    "TitleStylePatch",
    "fill_table_column_defaults",
    "TotalSlotStylePatch",
    "TotalStylePatch",
]


# =============================================================================
# CHARTSTYLEPATCH — chart-local authored overlay
# =============================================================================


class ChartStylePatch(BaseModel):
    """Chart-local style patch — chart-level sizing fields plus per-family sub-patches.

    Each family key (bar, line, area, …) holds the fully-typed patch for that
    chart family. The render layer reads only the family that matches the chart
    type; other families are ignored.

    ``aspect_ratio``, ``min_height``, and ``max_height`` live here (not in a
    family sub-patch) so they can be authored at the top of a ``style:`` block.
    The normalizer promotes them to ``Chart.aspect_ratio`` etc. before the
    compiled ``ChartStylePatch`` is built, so these fields are always ``None``
    in the final compiled model.
    """

    model_config = ConfigDict(extra="forbid")

    # Chart-level sizing — valid on all chart types that support aspect-ratio
    # sizing (i.e. not kpi / table / callout / spark_bar which use fixed sizing).
    # Normalizer promotes these to Chart.aspect_ratio / min_height / max_height.
    aspect_ratio: float | None = Field(
        default=None,
        description="Chart aspect ratio (width/height).",
    )
    min_height: float | None = Field(
        default=None,
        description="Minimum chart height in pixels.",
    )
    max_height: float | None = Field(
        default=None,
        description="Maximum chart height in pixels.",
    )

    # Per-chart-type authored overlay patches.
    bar: BarChartStylePatch | None = Field(
        default=None, description="Bar chart-specific style overrides."
    )
    line: LineChartStylePatch | None = Field(
        default=None, description="Line chart-specific style overrides."
    )
    area: AreaChartStylePatch | None = Field(
        default=None, description="Area chart-specific style overrides."
    )
    scatter: ScatterChartStylePatch | None = Field(
        default=None, description="Scatter chart-specific style overrides."
    )
    pie: PieChartStylePatch | None = Field(
        default=None,
        description="Pie/donut chart style overrides.",
    )
    kpi: KpiChartStylePatch | None = Field(
        default=None, description="KPI chart-specific style overrides."
    )
    spark_bar: SparkBarChartStylePatch | None = Field(
        default=None, description="Spark bar chart-specific style overrides."
    )
    spark: SparkStylePatch | None = Field(
        default=None,
        description="Spark line/area chart-specific style overrides.",
    )
    heatmap: HeatmapChartStylePatch | None = Field(
        default=None, description="Heatmap chart-specific style overrides."
    )
    geoshape: GeoshapeChartStylePatch | None = Field(
        default=None, description="Geoshape/map chart-specific style overrides."
    )
    point_map: PointMapChartStylePatch | None = Field(
        default=None, description="Point map chart-specific style overrides."
    )
    table: TableChartStylePatch | None = Field(
        default=None, description="Table chart-specific style overrides."
    )
    callout: CalloutChartStylePatch | None = Field(
        default=None, description="Callout chart-specific style overrides (tone)."
    )

    @classmethod
    def coerce_chart_style(cls, raw_style: Any) -> ChartStylePatch:
        if isinstance(raw_style, cls):
            return raw_style
        if isinstance(raw_style, dict):
            return cls.model_validate(raw_style)
        return cls()

    @model_validator(mode="before")
    @classmethod
    def _reject_stale_pie_keys(cls, data: Any) -> Any:
        """Catch authors using removed flat pie keys, old arc key, or chart-root geometry in style."""
        if not isinstance(data, dict):
            return data
        # height and width belong at chart root, not in style:.
        for geo_key in ("height", "width"):
            if geo_key in data:
                raise ValueError(
                    f"style.{geo_key} is not valid on a chart's style block. "
                    f"Chart sizing fields live at the chart root, not in style:.\n\n"
                    f"  charts:\n"
                    f"    my_chart:\n"
                    f"      type: bar\n"
                    f"      {geo_key}: ...   # ← move here\n"
                    f"      style:\n"
                    f"        # paint only"
                )
        # For layered charts, sizing fields must be at the top of style:, not
        # nested inside a family sub-patch (e.g. style.bar.aspect_ratio).
        # The normalizer only promotes top-level style sizing; nested forms are
        # silently ignored, so reject them here.
        _SIZING_KEYS = {"aspect_ratio", "min_height", "max_height"}
        # spark_bar always uses fixed sizing (see ChartStylePatch docstring) —
        # these keys have no effect anywhere, at the top of style: included, so
        # reject them outright instead of redirecting authors to a location
        # that would silently no-op just the same.
        spark_bar_patch = data.get("spark_bar")
        if isinstance(spark_bar_patch, dict):
            for sizing_key in _SIZING_KEYS:
                if sizing_key in spark_bar_patch:
                    raise ValueError(
                        f"style.spark_bar.{sizing_key} is not supported on "
                        f"spark_bar charts. spark_bar uses fixed sizing; "
                        f"aspect_ratio/min_height/max_height have no effect "
                        f"on this chart type."
                    )
        _FAMILY_KEYS = {
            "bar",
            "line",
            "area",
            "scatter",
            "heatmap",
            "geoshape",
            "point_map",
            "pie",
            "spark",
        }
        for family in _FAMILY_KEYS:
            family_patch = data.get(family)
            if isinstance(family_patch, dict):
                for sizing_key in _SIZING_KEYS:
                    if sizing_key in family_patch:
                        raise ValueError(
                            f"style.{family}.{sizing_key} is not valid. "
                            f"Sizing fields must be at the top of the style: block, "
                            f"not nested inside a family sub-patch:\n\n"
                            f"  style:\n"
                            f"    {sizing_key}: ...   # ← move here\n"
                            f"    {family}:\n"
                            f"      # mark paint only"
                        )
        stale: dict[str, str] = {
            "arc": "style.pie.marks.slice",
            "slice": "style.pie.marks.slice",
            "inner_radius": "style.pie.inner_radius",
            "total": "style.pie.total",
        }
        for key, new_path in stale.items():
            if key in data:
                raise ValueError(
                    f"style.{key} has been removed. "
                    f"Pie style is now under style.pie.*. "
                    f"Move it to {new_path}."
                )
        return data
