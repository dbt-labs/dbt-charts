"""Style model skeleton — bare nouns for theme-stage style classes.

Stage: COMPILE
Purpose: Define the target Pydantic model architecture for style.

Naming pattern:
  XX        — validated from theme YAML. Required fields; no fallbacks.
  XXPatch   — generated mechanically via build_patch_model(). Never hand-duplicated.
  ResolvedXX — final merged result after cascade. Sole type render/sizing accepts.

Leaf types:
  FontStyle{family, color, size, weight, style, decoration}  — text appearance, cascades
  BorderStylePatch{radius, color, width}  — hand-written all-Optional patch; carries from_css()
  BorderStyle{width, color, radius}  — resolved border, all required

Package layout (one file per concern, mirroring chart/normalized/):
  board.py       — frame/board dimensions, title, text, placeholder
  axis.py        — axis, scale, tooltip
  legend.py      — legend
  marks.py       — shared per-mark-type styles and cross-family primitives
  _chart_base.py — chart-family style base class hierarchy
  bar.py, line.py, area.py, scatter.py, heatmap.py, pie.py, histogram.py,
  geoshape.py, point_map.py — one *ChartMarksStyle + one *ChartStyle per family
  callout.py     — callout chart family
  kpi.py         — KPI chart family
  table.py       — table chart family + attached support_table primitive
  spark_bar.py   — spark_bar (standalone chart) family
  charts.py      — ChartsStyle registry (aggregates every *ChartStyle)
  layout.py      — layout containers (rows, cols, grid, tabs, details)
  page.py        — page chrome (inputs, footer, timestamp)
  variables.py   — variable controls chrome
  style.py       — Style root
"""

from __future__ import annotations

from dbt_charts.core.compile.models.primitives import FontStyle
from dbt_charts.core.compile.models.style.theme._chart_base import (
    _CartesianChartStyle,
    _ChartStyleBase,
    _ChartStyleBaseAllOptional,
    _GeoChartStyle,
    _PaintedChartStyleBase,
    _PaintedChartStyleBaseAllOptional,
    _QuantitativeAxisChartStyleMixin,
    _RadialChartStyle,
)
from dbt_charts.core.compile.models.style.theme.area import (
    AreaChartMarksStyle,
    AreaChartStyle,
    AreaLayerStyle,
    AreaLineStyle,
)
from dbt_charts.core.compile.models.style.theme.axis import (
    AxisGridThresholdStyle,
    AxisLabelOverlapConfig,
    AxisLabelStyle,
    AxisLineStyle,
    AxisMirrorStyle,
    AxisTicksStyle,
    AxisTitleStyle,
    AxisXStyle,
    AxisYStyle,
    BandAxisStyle,
    BaseAxisGridStyle,
    BaseAxisStyle,
    BaseScaleStyle,
    DimensionLabelStyle,
    DimensionTicksStyle,
    QuantitativeAxisStyle,
    ScaleContinuousStyle,
    ScaleDomainValidationMixin,
    ScaleLogStyle,
    ScalePowStyle,
    ScaleSymlogStyle,
    TooltipBorderStyle,
    TooltipShadowStyle,
    TooltipSlotStyle,
    TooltipStyle,
    XScaleStyle,
)
from dbt_charts.core.compile.models.style.theme.bar import (
    BarChartMarksStyle,
    BarChartStyle,
    BarLayerStyle,
)
from dbt_charts.core.compile.models.style.theme.board import (
    VALID_FONT_WEIGHTS,
    BlockMarginStyle,
    BoxStyle,
    ColumnRuleStyle,
    FrameStyle,
    PaddingStyle,
    PlaceholderOverlay,
    PlaceholderStyle,
    RootFontStyle,
    TextBlockquoteStyle,
    TextBoldStyle,
    TextCodeStyle,
    TextColumnStyle,
    TextStyle,
    TitlePositionStyle,
    TitleStyle,
    TitleSubtitleStyle,
    TitleWidthOffsetsStyle,
    _normalize_overflow_value,
    coerce_gap,
    font_weight_as_css,
)
from dbt_charts.core.compile.models.style.theme.callout import (
    CalloutChartStyle,
    CalloutElementStyle,
)
from dbt_charts.core.compile.models.style.theme.charts import (
    ChartsStyle,
    HoverEmphasisStyle,
)
from dbt_charts.core.compile.models.style.theme.geoshape import (
    GeoshapeChartMarksStyle,
    GeoshapeChartStyle,
)
from dbt_charts.core.compile.models.style.theme.heatmap import (
    HeatmapChartMarksStyle,
    HeatmapChartStyle,
)
from dbt_charts.core.compile.models.style.theme.histogram import (
    HistogramChartMarksStyle,
    HistogramChartStyle,
)
from dbt_charts.core.compile.models.style.theme.kpi import (
    KpiChartStyle,
    KpiSlotStyle,
    KpiTonesStyle,
    KpiValueStyle,
)
from dbt_charts.core.compile.models.style.theme.layout import (
    DetailsArrowFontStyle,
    DetailsArrowStyle,
    DetailsStyle,
    GridLayoutStyle,
    LayoutGapStyle,
    LayoutStyle,
    TabsStyle,
    ViewStyle,
)
from dbt_charts.core.compile.models.style.theme.legend import (
    LegendAlign,
    LegendDirection,
    LegendEdge,
    LegendElementStyle,
    LegendLabelStyle,
    LegendPositionStyle,
    LegendStyle,
    LegendTitleStyle,
    PieLegendStyle,
    PiePositionStyle,
)
from dbt_charts.core.compile.models.style.theme.line import (
    LineChartMarksStyle,
    LineChartStyle,
    LineLayerStyle,
)
from dbt_charts.core.compile.models.style.theme.marks import (
    AreaMarkStyle,
    BarLabelsStyle,
    BarMarkStyle,
    BarTotalLabelStyle,
    BasemapStyle,
    CircleMarkStyle,
    GeoshapeMarkStyle,
    GlobalMarksStyle,
    LabelsDefaultTemplate,
    LineMarkStyle,
    MarkLabelsStyle,
    PointLabelsStyle,
    PointMarkStyle,
    ProjectionStyle,
    RectMarkStyle,
    RuleMarkStyle,
    SeriesLabelFontStyle,
    SeriesLabelStyle,
    SliceLabelsStyle,
    SliceMarkStyle,
    SubtitleStyle,
    TextMarkStyle,
    TotalSlotStyle,
    TotalStyle,
    TotalValueSlotStyle,
)
from dbt_charts.core.compile.models.style.theme.page import (
    FooterRule,
    FooterStyle,
    InputStyle,
    InputWidths,
    RangeDefaults,
    TimestampStyle,
)
from dbt_charts.core.compile.models.style.theme.pie import (
    PieChartMarksStyle,
    PieChartStyle,
)
from dbt_charts.core.compile.models.style.theme.point_map import (
    PointMapChartMarksStyle,
    PointMapChartStyle,
)
from dbt_charts.core.compile.models.style.theme.scatter import (
    ScatterChartMarksStyle,
    ScatterChartStyle,
    ScatterLayerStyle,
)
from dbt_charts.core.compile.models.style.theme.spark_bar import (
    SparkBarBarStyle,
    SparkBarChartLabelStyle,
    SparkBarChartStyle,
    SparkBarCountStyle,
)
from dbt_charts.core.compile.models.style.theme.style import (
    Style,
)
from dbt_charts.core.compile.models.style.theme.table import (
    PaginatorStyle,
    SparkAreaStyle,
    SparkBarCellStyle,
    SparkBarLabelStyle,
    SparkColumnsStyle,
    SparkColumnStyle,
    SparkEmptyStyle,
    SparkSingleValueStyle,
    SparkStyle,
    SupportTableLabelStyle,
    SupportTableRowPaddingStyle,
    SupportTableRowStyle,
    SupportTableStyle,
    TableChartStyle,
    TableColumnsStyle,
    TableEdgeStyle,
    TableHeaderStyle,
    TableRowNumbersStyle,
    TableRowRolesStyle,
    TableRowRoleStyle,
    TableRowStripeStyle,
    TableRowStyle,
    TableRuleStyle,
    TableTitleStyle,
)
from dbt_charts.core.compile.models.style.theme.variables import (
    VariablesLabelStyle,
    VariablesPlaceholderStyle,
    VariablesStyle,
    VariablesValueStyle,
)

__all__ = [
    "AreaChartMarksStyle",
    "AreaChartStyle",
    "AreaLayerStyle",
    "AreaLineStyle",
    "AreaMarkStyle",
    "AxisGridThresholdStyle",
    "AxisLabelOverlapConfig",
    "AxisLabelStyle",
    "AxisLineStyle",
    "AxisMirrorStyle",
    "AxisTicksStyle",
    "AxisTitleStyle",
    "AxisXStyle",
    "AxisYStyle",
    "BandAxisStyle",
    "BaseAxisGridStyle",
    "BaseAxisStyle",
    "BaseScaleStyle",
    "DimensionLabelStyle",
    "DimensionTicksStyle",
    "QuantitativeAxisStyle",
    "ScaleContinuousStyle",
    "ScaleLogStyle",
    "ScalePowStyle",
    "ScaleSymlogStyle",
    "XScaleStyle",
    "BarChartMarksStyle",
    "BarChartStyle",
    "BarLayerStyle",
    "BarLabelsStyle",
    "BarMarkStyle",
    "BarTotalLabelStyle",
    "BasemapStyle",
    "BlockMarginStyle",
    "FrameStyle",
    "BoxStyle",
    "CalloutChartStyle",
    "CalloutElementStyle",
    "ChartsStyle",
    "CircleMarkStyle",
    "ColumnRuleStyle",
    "SupportTableLabelStyle",
    "SupportTableRowPaddingStyle",
    "SupportTableRowStyle",
    "SupportTableStyle",
    "DetailsArrowFontStyle",
    "DetailsArrowStyle",
    "DetailsStyle",
    "FontStyle",
    "FooterRule",
    "FooterStyle",
    "GeoshapeChartMarksStyle",
    "GeoshapeChartStyle",
    "GeoshapeMarkStyle",
    "GlobalMarksStyle",
    "GridLayoutStyle",
    "HeatmapChartMarksStyle",
    "HeatmapChartStyle",
    "HistogramChartMarksStyle",
    "HistogramChartStyle",
    "HoverEmphasisStyle",
    "InputStyle",
    "InputWidths",
    "KpiChartStyle",
    "KpiSlotStyle",
    "KpiTonesStyle",
    "KpiValueStyle",
    "LabelsDefaultTemplate",
    "LayoutGapStyle",
    "LayoutStyle",
    "LegendAlign",
    "LegendDirection",
    "LegendEdge",
    "LegendElementStyle",
    "LegendLabelStyle",
    "LegendPositionStyle",
    "LegendStyle",
    "LegendTitleStyle",
    "LineChartMarksStyle",
    "LineChartStyle",
    "LineLayerStyle",
    "LineMarkStyle",
    "MarkLabelsStyle",
    "PaddingStyle",
    "PaginatorStyle",
    "PieChartMarksStyle",
    "PieChartStyle",
    "PieLegendStyle",
    "PiePositionStyle",
    "PlaceholderOverlay",
    "PlaceholderStyle",
    "PointLabelsStyle",
    "PointMapChartMarksStyle",
    "PointMapChartStyle",
    "PointMarkStyle",
    "ProjectionStyle",
    "RangeDefaults",
    "RectMarkStyle",
    "RootFontStyle",
    "RuleMarkStyle",
    "ScaleDomainValidationMixin",
    "ScatterChartMarksStyle",
    "ScatterChartStyle",
    "ScatterLayerStyle",
    "SeriesLabelFontStyle",
    "SeriesLabelStyle",
    "SliceLabelsStyle",
    "SliceMarkStyle",
    "SparkAreaStyle",
    "SparkBarBarStyle",
    "SparkBarCellStyle",
    "SparkBarChartLabelStyle",
    "SparkBarChartStyle",
    "SparkBarCountStyle",
    "SparkBarLabelStyle",
    "SparkColumnStyle",
    "SparkColumnsStyle",
    "SparkEmptyStyle",
    "SparkSingleValueStyle",
    "SparkStyle",
    "Style",
    "SubtitleStyle",
    "TableChartStyle",
    "TableColumnsStyle",
    "TableEdgeStyle",
    "TableHeaderStyle",
    "TableRowNumbersStyle",
    "TableRowRoleStyle",
    "TableRowRolesStyle",
    "TableRowStripeStyle",
    "TableRowStyle",
    "TableRuleStyle",
    "TableTitleStyle",
    "TabsStyle",
    "TextBlockquoteStyle",
    "TextBoldStyle",
    "TextCodeStyle",
    "TextColumnStyle",
    "TextMarkStyle",
    "TextStyle",
    "TimestampStyle",
    "TitlePositionStyle",
    "TitleStyle",
    "TitleSubtitleStyle",
    "TitleWidthOffsetsStyle",
    "TooltipBorderStyle",
    "TooltipShadowStyle",
    "TooltipSlotStyle",
    "TooltipStyle",
    "TotalSlotStyle",
    "TotalStyle",
    "TotalValueSlotStyle",
    "VALID_FONT_WEIGHTS",
    "VariablesLabelStyle",
    "VariablesPlaceholderStyle",
    "VariablesStyle",
    "VariablesValueStyle",
    "ViewStyle",
    "_CartesianChartStyle",
    "_ChartStyleBase",
    "_ChartStyleBaseAllOptional",
    "_GeoChartStyle",
    "_PaintedChartStyleBase",
    "_PaintedChartStyleBaseAllOptional",
    "_QuantitativeAxisChartStyleMixin",
    "_RadialChartStyle",
    "_normalize_overflow_value",
    "coerce_gap",
    "font_weight_as_css",
]
