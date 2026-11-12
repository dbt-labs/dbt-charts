"""Theme-stage style classes: pie/donut chart family."""

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


class _PieChartStyleBase(_RadialChartStyle):
    """Pie/donut chart style: geometry + total (flat) + marks sub-block.

    ``total`` stays flat at this level (compositional slot, not a VL mark).
    ``inner_radius`` is on _RadialChartStyle (0 is a solid pie).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    aspect_ratio: float = Field(
        description="Aspect ratio (width/height) of the pie chart viewport."
    )
    pattern: PatternStyle = Field(
        description="How the `pattern:` channel paints fills."
    )
    marks: Annotated[
        PieChartMarksStyle, InheritSlot(from_path="Style.charts.marks")
    ] = Field(
        default_factory=PieChartMarksStyle,
        description="Pie-family mark overrides.",
    )


if TYPE_CHECKING:

    class PieConditionalRule(ConditionalPredicate):
        marks: PieChartMarksStyle | None

else:
    PieConditionalRule = build_conditional_model(
        _PieChartStyleBase, name="PieChartStyleConditional"
    )


class PieChartStyle(_PieChartStyleBase):
    """Pie chart style, plus the per-slice rules only a chart's own style takes."""

    conditional: Annotated[
        dict[
            str,
            Annotated[
                list[PieConditionalRule],
                Field(min_length=1),
                AfterValidator(default_rule_last),
            ],
        ]
        | None,
        ChartLocal(),
    ] = Field(
        default=None,
        description=(
            "Rules that restyle a slice by its row's value, keyed by column name. "
            "Each rule is one condition (eq, ne, lt, lte, gt, gte, between, in, "
            "is_null, or default) plus the style it applies: marks.slice color, opacity and stroke (color, width), and "
            "marks.text font color and weight. A rule's color overrides an authored "
            "color channel for the matched slice only; the legend is unchanged. "
            "Every matching rule applies; where two set the same key the later one "
            "wins, so list the more specific rule last. A `default: true` rule "
            "applies only when no other rule matched. Two columns cannot patch "
            "the same property."
        ),
    )
