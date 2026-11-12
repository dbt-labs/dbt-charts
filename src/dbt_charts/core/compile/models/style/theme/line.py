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
from dbt_charts.core.compile.models.style.theme._chart_base import (
    _AnnotatedChartStyleMixin,
    _CartesianChartStyle,
    _LegendedChartStyleBaseAllOptional,
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


class _LineChartStyleBase(
    _CartesianChartStyle,
    _LegendedChartStyleBaseAllOptional,
    _QuantitativeAxisChartStyleMixin,
    _AnnotatedChartStyleMixin,
):
    """Line chart style: chart-level fields + marks sub-block."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    endpoint_labels: EndpointLabelsConfig = Field(
        description="Styling of the series names printed at the end of each line (`legend.position.edge: endpoints`)."
    )
    marks: Annotated[
        LineChartMarksStyle, InheritSlot(from_path="Style.charts.marks")
    ] = Field(
        default_factory=LineChartMarksStyle,
        description="Line-family mark overrides.",
    )


if TYPE_CHECKING:

    class LineConditionalRule(ConditionalPredicate):
        marks: LineChartMarksStyle | None

else:
    LineConditionalRule = build_conditional_model(
        _LineChartStyleBase, name="LineChartStyleConditional"
    )


class LineChartStyle(_LineChartStyleBase):
    """Line chart style, plus the per-point rules only a chart's own style takes."""

    conditional: Annotated[
        dict[
            str,
            Annotated[
                list[LineConditionalRule],
                Field(min_length=1),
                AfterValidator(default_rule_last),
            ],
        ]
        | None,
        ChartLocal(),
    ] = Field(
        default=None,
        description=(
            "Rules that restyle a point by its row's value, keyed by column name. "
            "Each rule is one condition (eq, ne, lt, lte, gt, gte, between, in, "
            "is_null, or default) plus the style it applies: marks.point color, fill, filled, size, shape, opacity and stroke_width, marks.text font color and weight (the value labels), and marks.line.stroke color, width and dasharray. "
            "A line segment takes a stroke key only when both of its end points resolve the same value for it, "
            "so a run of matching points restyles the segments between them, and the segment stepping into the run keeps the base style; "
            "include the boundary point in the condition to draw it. "
            "Curve, connect and the other whole-series properties cannot vary per row. "
            "Every matching rule applies; where two set the same key the later one "
            "wins, so list the more specific rule last. A `default: true` rule "
            "applies only when no other rule matched. Two columns cannot patch "
            "the same property. Rules style the base layer, not `layers:`."
        ),
    )
