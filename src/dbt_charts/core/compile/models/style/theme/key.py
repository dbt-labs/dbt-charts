"""Theme-stage style classes: the chart key, how a chart names its series."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.markers import InheritSlot, SkipInheritSlots
from dbt_charts.core.compile.models.style.theme.legend import (
    LegendStyle,
    PieLegendStyle,
)
from dbt_charts.core.compile.models.style.theme.marks import SeriesLabelFontStyle

# `auto` lets the engine pick; `legend` draws a legend; `direct` names the
# series on the chart itself. Families that cannot name series on the chart
# take `KeyType` without `direct`.
KeyType = Literal["auto", "legend", "direct"]
LegendOnlyKeyType = Literal["auto", "legend"]


class LineAreaDirectKeyStyle(BaseModel):
    """The direct-key leaves a line or area reads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    offset: float = Field(
        description="Gap in pixels between the plot and the series labels."
    )
    font: Annotated[
        SeriesLabelFontStyle, InheritSlot(from_path="Style.charts.font")
    ] = Field(
        description="Direct label font; unset fields come from the broader tier.",
    )


class DirectKeyStyle(LineAreaDirectKeyStyle):
    """Every direct-key leaf: the board-wide style, and a bar's own."""

    height: float = Field(
        description="Height in pixels of the label strip above a horizontal stacked bar."
    )


class ChartsKeyStyle(BaseModel):
    """The board-wide key every family inherits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    visible: bool = Field(description="Name the series at all; false hides the key.")
    type: KeyType = Field(
        description="Legend, direct labels, or `auto` to let the engine pick."
    )
    legend: LegendStyle = Field(description="Legend style.")
    direct: DirectKeyStyle = Field(description="Direct label style.")


class LineAreaKeyStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    visible: bool = Field(description="Name the series at all; false hides the key.")
    type: KeyType = Field(
        description="Legend, direct labels, or `auto` to let the engine pick."
    )
    legend: LegendStyle = Field(description="Legend style.")
    direct: Annotated[LineAreaDirectKeyStyle, SkipInheritSlots()] = Field(
        description="Direct label style."
    )


class BarKeyStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    visible: bool = Field(description="Name the series at all; false hides the key.")
    type: KeyType = Field(
        description="Legend, direct labels, or `auto` to let the engine pick."
    )
    legend: LegendStyle = Field(description="Legend style.")
    direct: Annotated[DirectKeyStyle, SkipInheritSlots()] = Field(
        description="Direct label style."
    )


class PieKeyStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    visible: bool = Field(description="Name the series at all; false hides the key.")
    type: KeyType = Field(
        description="Legend, direct wedge labels, or `auto` to let the engine pick."
    )
    legend: PieLegendStyle = Field(description="Legend style.")


class LegendOnlyKeyStyle(BaseModel):
    """The key of a scatter, heatmap, histogram or geo chart: a legend only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    visible: bool = Field(description="Name the series at all; false hides the key.")
    type: LegendOnlyKeyType = Field(
        description="A legend, or `auto` to let the engine decide."
    )
    legend: LegendStyle = Field(description="Legend style.")
