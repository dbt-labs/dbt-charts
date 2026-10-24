"""Theme-stage style classes: scatter chart family."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.markers import (
    InheritSlot,
    SkipInheritSlots,  # noqa: F401  # pyright: ignore[reportUnusedImport] — required in module globals for model_rebuild() to resolve inherited base-class annotations
)
from dbt_charts.core.compile.models.style.theme._chart_base import (
    _AnnotatedChartStyleMixin,
    _CartesianChartStyle,
    _QuantitativeAxisChartStyleMixin,
)
from dbt_charts.core.compile.models.style.theme.marks import (
    PointMarkStyle,
    TextMarkStyle,
)


class ScatterChartMarksStyle(BaseModel):
    """Scatter-family mark overrides."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    point: PointMarkStyle = Field(
        default_factory=PointMarkStyle,
        description="Point mark overrides; inherits from global.",
    )
    text: TextMarkStyle = Field(
        default_factory=TextMarkStyle,
        description="Text mark overrides; inherits from global.",
    )


class ScatterLayerStyle(BaseModel):
    """Required wrapper for scatter-layer mark overrides (built into a Patch by build_patch_model)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    marks: ScatterChartMarksStyle = Field(
        description="Mark overrides for this scatter layer."
    )


class ScatterChartStyle(
    _CartesianChartStyle, _QuantitativeAxisChartStyleMixin, _AnnotatedChartStyleMixin
):
    """Scatter chart style: chart-level fields + marks sub-block."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    marks: Annotated[
        ScatterChartMarksStyle, InheritSlot(from_path="Style.charts.marks")
    ] = Field(
        default_factory=ScatterChartMarksStyle,
        description="Scatter-family mark overrides.",
    )
