"""A chart's merged style: its family style and the per-chart fields.

Stage: NORMALIZE (Output). ``Board.chart_styles`` holds one per placed chart.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field

from dbt_charts.core.compile.models.primitives import ScaleTargetConfig
from dbt_charts.core.compile.models.style.authored import (
    AxisXStylePatch,
    AxisYStylePatch,
    BandAxisStylePatch,
    BaseAxisStylePatch,
    PaginationConfig,
    QuantitativeAxisStylePatch,
)
from dbt_charts.core.compile.models.style.resolved._base import ResolvedLegendStyle
from dbt_charts.core.compile.models.style.theme import (
    AreaChartStyle,
    BarChartStyle,
    CalloutChartStyle,
    ChartTitleStyle,
    GeoshapeChartStyle,
    HeatmapChartStyle,
    HistogramChartStyle,
    KpiChartStyle,
    LineChartStyle,
    PieChartStyle,
    PointMapChartStyle,
    ScatterChartStyle,
    SparkBarChartStyle,
    SupportTableStyle,
    TableChartStyle,
)


class _MergedChartStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    color: str | None = Field(description="Single color for the chart's marks.")
    palette: list[str] = Field(description="Colors assigned to categories in order.")
    single_series_palette: list[str] = Field(
        description="Colors a single-series chart picks from."
    )
    requested_alias_palette: str | None = Field(
        description="Name of an unsupported palette that was requested."
    )
    background: str = Field(description="Fill color behind the chart.")
    ink_canvas: str = Field(
        description="Opaque color that labels and marks must contrast with."
    )
    legend: ResolvedLegendStyle = Field(description="How the chart's legend looks.")
    title: ChartTitleStyle = Field(description="How the chart's title looks.")
    pagination: PaginationConfig | None = Field(
        description="How many table rows show per page."
    )
    # None: the chart's own style sets no gradient.
    gradient: ScaleTargetConfig | None = Field(
        description="Continuous scale the chart's own style.color sets for its color field."
    )
    # The axis patches are the chart's own overrides, which the axis cascade
    # layers last; None means the chart authors none.
    axis: BaseAxisStylePatch | None = Field(
        default=None, description="Style changes applied to every axis."
    )
    axis_x: AxisXStylePatch | None = Field(
        default=None, description="Style changes applied to the x axis."
    )
    axis_y: AxisYStylePatch | None = Field(
        default=None, description="Style changes applied to the y axis."
    )
    axis_quantitative: QuantitativeAxisStylePatch | None = Field(
        default=None, description="Style changes applied to numeric axes."
    )
    axis_band: BandAxisStylePatch | None = Field(
        default=None, description="Style changes applied to category axes."
    )


class MergedBarStyle(_MergedChartStyle):
    type: Literal["bar"]
    family: BarChartStyle = Field(description="Look of the bar chart.")


class MergedHistogramStyle(_MergedChartStyle):
    type: Literal["histogram"]
    family: HistogramChartStyle = Field(description="Look of the histogram chart.")


class MergedLineStyle(_MergedChartStyle):
    type: Literal["line"]
    family: LineChartStyle = Field(description="Look of the line chart.")


class MergedAreaStyle(_MergedChartStyle):
    type: Literal["area"]
    family: AreaChartStyle = Field(description="Look of the area chart.")


class MergedScatterStyle(_MergedChartStyle):
    type: Literal["scatter"]
    family: ScatterChartStyle = Field(description="Look of the scatter chart.")


class MergedHeatmapStyle(_MergedChartStyle):
    type: Literal["heatmap"]
    family: HeatmapChartStyle = Field(description="Look of the heatmap chart.")


class MergedPieStyle(_MergedChartStyle):
    type: Literal["pie", "donut"]
    family: PieChartStyle = Field(description="Look of the pie or donut chart.")


class MergedKpiStyle(_MergedChartStyle):
    type: Literal["kpi"]
    family: KpiChartStyle = Field(description="Look of the kpi chart.")


class MergedTableStyle(_MergedChartStyle):
    type: Literal["table"]
    family: TableChartStyle = Field(description="Look of the table chart.")
    support_table: SupportTableStyle = Field(
        description="Look of the table that sits beside a chart."
    )
    width_pinned: bool = Field(
        description="Whether the table's own style fixes its preferred width."
    )


class MergedGeoshapeStyle(_MergedChartStyle):
    type: Literal["geoshape", "map"]
    family: GeoshapeChartStyle = Field(description="Look of the map chart.")


class MergedPointMapStyle(_MergedChartStyle):
    type: Literal["point_map", "bubble_map"]
    family: PointMapChartStyle = Field(description="Look of the point map chart.")


class MergedSparkBarStyle(_MergedChartStyle):
    type: Literal["spark_bar"]
    family: SparkBarChartStyle = Field(description="Look of the spark bar chart.")


class MergedCalloutStyle(_MergedChartStyle):
    type: Literal["callout"]
    family: CalloutChartStyle = Field(description="Look of the callout chart.")


class MergedCustomStyle(_MergedChartStyle):
    type: Literal["custom"]


MergedChartStyle = Annotated[
    MergedBarStyle
    | MergedHistogramStyle
    | MergedLineStyle
    | MergedAreaStyle
    | MergedScatterStyle
    | MergedHeatmapStyle
    | MergedPieStyle
    | MergedKpiStyle
    | MergedTableStyle
    | MergedGeoshapeStyle
    | MergedPointMapStyle
    | MergedSparkBarStyle
    | MergedCalloutStyle
    | MergedCustomStyle,
    Discriminator("type"),
]

__all__ = ["MergedChartStyle"]
