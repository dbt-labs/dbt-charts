"""Theme-stage style classes: line chart family."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    # These authored types are injected into theme/__init__.py's module globals at
    # runtime by dbt_charts.core.compile.models.style.authored (after authored.py
    # finishes executing). The import here is TYPE_CHECKING-only to give pyright and
    # ruff the static definitions they need without creating a circular import.
    from dbt_charts.core.compile.models.style.authored import (  # noqa: PLC0415
        EndpointLabelsConfig,
    )

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.markers import (
    InheritSlot,
    SkipInheritSlots,
)
from dbt_charts.core.compile.models.style.theme._chart_base import (
    _AnnotatedChartStyleMixin,
    _CartesianChartStyle,
    _QuantitativeAxisChartStyleMixin,
)
from dbt_charts.core.compile.models.style.theme.marks import (
    LineMarkStyle,
    PointMarkStyle,
    RuleMarkStyle,
    TextMarkStyle,
)


class LineChartMarksStyle(BaseModel):
    """Line-family mark overrides."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    line: LineMarkStyle = Field(
        default_factory=LineMarkStyle,
        description="Line mark overrides; inherits from global.",
    )
    point: PointMarkStyle = Field(
        default_factory=PointMarkStyle,
        description="Point mark overrides; inherits from global.",
    )
    text: TextMarkStyle = Field(
        default_factory=TextMarkStyle,
        description="Text mark overrides; inherits from global.",
    )
    # SkipInheritSlots(cascade=True): apply_inherit copies the entire rule object
    # from charts.marks.rule when rule is None.
    rule: Annotated[RuleMarkStyle | None, SkipInheritSlots(cascade=True)] = Field(
        default=None,
        description="Rule mark overrides; None inherits global.",
    )


class LineLayerStyle(BaseModel):
    """Required wrapper for line-layer mark overrides (built into a Patch by build_patch_model)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    marks: LineChartMarksStyle = Field(
        description="Mark overrides for this line layer."
    )


class LineChartStyle(
    _CartesianChartStyle, _QuantitativeAxisChartStyleMixin, _AnnotatedChartStyleMixin
):
    """Line chart style: chart-level fields + marks sub-block."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    endpoint_labels: EndpointLabelsConfig = Field(
        description="Series names printed at the end of each line instead of in a legend."
    )
    marks: Annotated[
        LineChartMarksStyle, InheritSlot(from_path="Style.charts.marks")
    ] = Field(
        default_factory=LineChartMarksStyle,
        description="Line-family mark overrides.",
    )
