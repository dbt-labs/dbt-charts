"""Theme-stage style classes: heatmap chart family."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.conditional_predicate import (
    ConditionalPredicate,
    default_rule_last,
)
from dbt_charts.core.compile.models.factories import build_conditional_model
from dbt_charts.core.compile.models.markers import (
    ChartLocal,
    InheritSlot,
    SkipInheritSlots,
)
from dbt_charts.core.compile.models.primitives import PatternStyle
from dbt_charts.core.compile.models.style.theme._chart_base import (
    _CartesianChartStyle,
    _SideLegendedChartStyleBaseAllOptional,
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


class _HeatmapChartStyleBase(
    _CartesianChartStyle, _SideLegendedChartStyleBaseAllOptional
):
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


if TYPE_CHECKING:

    class HeatmapConditionalRule(ConditionalPredicate):
        marks: HeatmapChartMarksStyle | None

else:
    HeatmapConditionalRule = build_conditional_model(
        _HeatmapChartStyleBase, name="HeatmapChartStyleConditional"
    )


class HeatmapChartStyle(_HeatmapChartStyleBase):
    """Heatmap chart style, plus the per-cell rules only a chart's own style takes."""

    conditional: Annotated[
        dict[
            str,
            Annotated[
                list[HeatmapConditionalRule],
                Field(min_length=1),
                AfterValidator(default_rule_last),
            ],
        ]
        | None,
        ChartLocal(),
    ] = Field(
        default=None,
        description=(
            "Rules that restyle a cell by its row's value, keyed by column name. "
            "Each rule is one condition (eq, ne, lt, lte, gt, gte, between, in, "
            "is_null, or default) plus the style it applies: marks.rect color, opacity and stroke (color, width), and "
            "marks.text font color and weight. A rule's color overrides an authored "
            "color channel for the matched cell only; the legend is unchanged. "
            "Every matching rule applies; where two set the same key the later one "
            "wins, so list the more specific rule last. A `default: true` rule "
            "applies only when no other rule matched. Two columns cannot patch "
            "the same property."
        ),
    )
