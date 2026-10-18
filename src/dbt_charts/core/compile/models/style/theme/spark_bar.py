"""Theme-stage style classes: spark_bar (standalone) chart family."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.markers import (
    Color,
    InheritSlot,
)
from dbt_charts.core.compile.models.primitives import (
    CornerStyle,
    FontStyle,
)
from dbt_charts.core.compile.models.style.theme.board import (
    PaddingStyle,
)
from dbt_charts.core.compile.models.style.theme.marks import (
    SubtitleStyle,
)


class SparkBarBarStyle(BaseModel):
    """Bar geometry sub-block for SparkBarChartStyle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    height: float = Field(description="Height of each bar in pixels.")
    padding: float = Field(description="Vertical padding between bars in pixels.")
    color: Annotated[str | None, Color()] = Field(
        default=None,
        description="Bar fill color; None seeds from style.charts.color.categorical.single_series_palette[0].",
    )
    background: Annotated[str, Color()] = Field(
        description="Bar track background color - fill-grade, pinned per theme."
    )


class SparkBarChartLabelStyle(BaseModel):
    """Category-label sub-block for SparkBarChartStyle.

    Not to be confused with SparkBarLabelStyle, which is the in-cell bar
    sparkline label inside table cells.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    visible: bool = Field(description="Show series label text next to bars.")
    width: float = Field(description="Reserved width for bar label text in pixels.")


class SparkBarCountStyle(BaseModel):
    """Count-value sub-block for SparkBarChartStyle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    visible: bool = Field(description="Show count/value text next to bars.")
    width: float = Field(description="Reserved width for bar count text in pixels.")


class SparkBarChartStyle(BaseModel):
    """Produced by cascade from theme YAML."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    preferred_width: float = Field(
        description="Preferred spark_bar chart width in pixels."
    )
    min_width: float = Field(description="Minimum spark_bar chart width in pixels.")
    # InheritSlot: per-family padding inherits per-side from Style.charts.padding.
    padding: Annotated[PaddingStyle, InheritSlot(from_path="Style.charts.padding")] = (
        Field(
            description="Padding as {top, right, bottom, left} in pixels, never one number.",
        )
    )
    bar: SparkBarBarStyle = Field(description="Bar geometry (height, padding, color).")
    label: SparkBarChartLabelStyle = Field(
        description="Category-label column (visibility, reserved width)."
    )
    count: SparkBarCountStyle = Field(
        description="Count-value column (visibility, reserved width)."
    )
    max_bars: int = Field(description="Maximum number of bars to render.")
    # InheritSlot: spark_bar.font fills from charts.font.
    font: Annotated[FontStyle, InheritSlot(from_path="Style.charts.font")] = Field(
        default_factory=FontStyle, description="Spark_bar font style overrides."
    )
    border: CornerStyle = Field(description="Spark_bar outer corner rounding.")
    subtitle: SubtitleStyle = Field(
        description="Spark_bar subtitle font-sizing constants."
    )
