"""Theme-stage style classes: pie/donut chart family."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.markers import (
    InheritSlot,
    SkipInheritSlots,
)
from dbt_charts.core.compile.models.style.theme._chart_base import (
    _RadialChartStyle,
)
from dbt_charts.core.compile.models.style.theme.marks import (
    SliceMarkStyle,
    TextMarkStyle,
)


class PieChartMarksStyle(BaseModel):
    """Pie/donut-family mark overrides."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slice: SliceMarkStyle = Field(
        default_factory=SliceMarkStyle,
        description="Slice mark overrides; inherits from global.",
    )
    # SkipInheritSlots(cascade=True): apply_inherit copies the entire text object
    # from charts.marks.text when text is None.
    text: Annotated[TextMarkStyle | None, SkipInheritSlots(cascade=True)] = Field(
        default=None,
        description="Text mark overrides; None inherits global.",
    )


class PieChartStyle(_RadialChartStyle):
    """Pie/donut chart style: geometry + total (flat) + marks sub-block.

    ``total`` stays flat at this level (compositional slot, not a VL mark).
    ``inner_radius`` is on _RadialChartStyle (0 is a solid pie).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    aspect_ratio: float = Field(
        description="Aspect ratio (width/height) of the pie chart viewport."
    )
    marks: Annotated[
        PieChartMarksStyle, InheritSlot(from_path="Style.charts.marks")
    ] = Field(
        default_factory=PieChartMarksStyle,
        description="Pie-family mark overrides.",
    )
