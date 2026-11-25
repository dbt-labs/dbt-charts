"""Theme-stage style classes: point map chart family."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.conditional_predicate import (
    ConditionalPredicate,
    default_rule_last,
)
from dbt_charts.core.compile.models.factories import build_conditional_model
from dbt_charts.core.compile.models.markers import (
    Absent,
    ChartLocal,
    InheritSlot,
    SkipInheritSlots,  # noqa: F401  # pyright: ignore[reportUnusedImport] — required in module globals for model_rebuild() to resolve inherited base-class annotations
)
from dbt_charts.core.compile.models.primitives import PatternStyle, SizeScaleStyle
from dbt_charts.core.compile.models.style.theme._chart_base import (
    _GeoChartStyle,
)
from dbt_charts.core.compile.models.style.theme.marks import (
    RingedPointMarkStyle,
)


class PointMapChartMarksStyle(BaseModel):
    """Point map-family mark overrides."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    point: RingedPointMarkStyle = Field(
        default_factory=RingedPointMarkStyle,
        description="Point mark overrides; inherits from global.",
    )


class _PointMapChartStyleBase(_GeoChartStyle):
    """Point map chart style."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    marks: Annotated[
        PointMapChartMarksStyle, InheritSlot(from_path="Style.charts.marks")
    ] = Field(
        default_factory=PointMapChartMarksStyle,
        description="Point-map-family mark overrides.",
    )
    size: SizeScaleStyle = Field(
        description="Area range and scale type of the `size:` channel.",
    )
    tints: Annotated[PatternStyle | None, Absent("the color scale's own fills")] = (
        Field(
            default=None,
            description=(
                "Draw in flat tints of one ink: each step of this sequential pattern "
                "ramp is one tint. The basemap takes the lightest step; needs a `size:` "
                "field, whose values the ramp spans."
            ),
        )
    )
    named: Annotated[int | None, Absent("no point named")] = Field(
        default=None,
        ge=1,
        description="Name the largest points by `size:`, this many, beside their dots.",
    )
    size_key: Annotated[Literal["baseline"] | None, Absent("the size legend")] = Field(
        default=None,
        description=(
            "`baseline` draws the size key as three open circles standing on one "
            "baseline: the size scale's smallest, middle and largest "
            "rungs, each named beside it."
        ),
    )


if TYPE_CHECKING:

    class PointMapConditionalRule(ConditionalPredicate):
        marks: PointMapChartMarksStyle | None

else:
    PointMapConditionalRule = build_conditional_model(
        _PointMapChartStyleBase, name="PointMapChartStyleConditional"
    )


class PointMapChartStyle(_PointMapChartStyleBase):
    """Point map chart style, plus the per-point rules only a chart's own style takes."""

    conditional: Annotated[
        dict[
            str,
            Annotated[
                list[PointMapConditionalRule],
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
            "is_null, or default) plus the style it applies: marks.point color, "
            "fill, filled, size, shape, opacity and stroke_width. A rule size "
            "overrides a `size:` channel for the points it matches; the legend is "
            "unchanged. Every matching rule applies; where two set the same key "
            "the later one wins, so list the more specific rule last. A "
            "`default: true` rule applies only when no other rule matched. Two "
            "columns cannot patch the same property. Not available with `collapse`."
        ),
    )
