"""Theme-stage style classes: histogram chart family."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.markers import (
    InheritSlot,
    SkipInheritSlots,
)
from dbt_charts.core.compile.models.style.theme._chart_base import (
    _CartesianChartStyle,
    _QuantitativeAxisChartStyleMixin,
    _SideLegendedChartStyleBaseAllOptional,
)
from dbt_charts.core.compile.models.style.theme.marks import (
    BarMarkStyle,
    RuleMarkStyle,
)


class HistogramChartMarksStyle(BaseModel):
    """Histogram-family mark overrides."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bar: BarMarkStyle = Field(
        default_factory=BarMarkStyle,
        description="Bar mark overrides; inherits from global.",
    )
    # SkipInheritSlots(cascade=True): apply_inherit copies the entire rule object
    # from charts.marks.rule when rule is None.
    rule: Annotated[RuleMarkStyle | None, SkipInheritSlots(cascade=True)] = Field(
        default=None,
        description="Rule mark overrides; None inherits global.",
    )


class HistogramChartStyle(
    _CartesianChartStyle,
    _SideLegendedChartStyleBaseAllOptional,
    _QuantitativeAxisChartStyleMixin,
):
    """Histogram chart style."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bin_maxbins: int = Field(description="Maximum number of bins for auto-binning.")
    marks: Annotated[
        HistogramChartMarksStyle, InheritSlot(from_path="Style.charts.marks")
    ] = Field(
        default_factory=HistogramChartMarksStyle,
        description="Histogram-family mark overrides.",
    )
