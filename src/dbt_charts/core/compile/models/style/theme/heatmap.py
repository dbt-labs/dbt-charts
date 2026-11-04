"""Theme-stage style classes: heatmap chart family."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.markers import (
    InheritSlot,
    SkipInheritSlots,
)
from dbt_charts.core.compile.models.primitives import PatternStyle
from dbt_charts.core.compile.models.style.theme._chart_base import (
    _CartesianChartStyle,
)
from dbt_charts.core.compile.models.style.theme.marks import (
    RectMarkStyle,
    TextMarkStyle,
)


class HeatmapChartMarksStyle(BaseModel):
    """Heatmap-family mark overrides."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rect: RectMarkStyle = Field(
        default_factory=RectMarkStyle,
        description="Rect mark overrides; inherits from global.",
    )
    # SkipInheritSlots(cascade=True): apply_inherit copies the entire text object
    # from charts.marks.text when text is None.
    text: Annotated[TextMarkStyle | None, SkipInheritSlots(cascade=True)] = Field(
        default=None,
        description="Text mark overrides; None inherits global.",
    )


class HeatmapChartStyle(_CartesianChartStyle):
    """Heatmap chart style."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cell_padding: float = Field(description="Padding between heatmap cells in pixels.")
    pattern: PatternStyle = Field(
        description="How the `pattern:` channel paints fills."
    )
    marks: Annotated[
        HeatmapChartMarksStyle, InheritSlot(from_path="Style.charts.marks")
    ] = Field(
        default_factory=HeatmapChartMarksStyle,
        description="Heatmap-family mark overrides.",
    )
