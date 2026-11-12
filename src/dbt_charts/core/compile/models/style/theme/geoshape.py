"""Theme-stage style classes: geoshape (choropleth) chart family."""

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
    SkipInheritSlots,  # noqa: F401  # pyright: ignore[reportUnusedImport] — required in module globals for model_rebuild() to resolve inherited base-class annotations
)
from dbt_charts.core.compile.models.style.theme._chart_base import (
    _GeoChartStyle,
)
from dbt_charts.core.compile.models.style.theme.marks import (
    GeoshapeMarkStyle,
)


class GeoshapeChartMarksStyle(BaseModel):
    """Geoshape-family mark overrides."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    geoshape: GeoshapeMarkStyle = Field(
        default_factory=GeoshapeMarkStyle,
        description="Geoshape mark overrides; inherits from global.",
    )


class _GeoshapeChartStyleBase(_GeoChartStyle):
    """Geoshape (choropleth) chart style."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    marks: Annotated[
        GeoshapeChartMarksStyle, InheritSlot(from_path="Style.charts.marks")
    ] = Field(
        default_factory=GeoshapeChartMarksStyle,
        description="Geoshape-family mark overrides.",
    )


if TYPE_CHECKING:

    class GeoshapeConditionalRule(ConditionalPredicate):
        marks: GeoshapeChartMarksStyle | None

else:
    GeoshapeConditionalRule = build_conditional_model(
        _GeoshapeChartStyleBase, name="GeoshapeChartStyleConditional"
    )


class GeoshapeChartStyle(_GeoshapeChartStyleBase):
    """Geoshape chart style, plus the per-region rules only a chart's own style takes."""

    conditional: Annotated[
        dict[
            str,
            Annotated[
                list[GeoshapeConditionalRule],
                Field(min_length=1),
                AfterValidator(default_rule_last),
            ],
        ]
        | None,
        ChartLocal(),
    ] = Field(
        default=None,
        description=(
            "Rules that restyle a region by its row's value, keyed by column name. "
            "Each rule is one condition (eq, ne, lt, lte, gt, gte, between, in, "
            "is_null, or default) plus the style it applies: marks.geoshape fill and "
            "stroke (color, width). A rule's color overrides an authored "
            "color channel for the matched region only; the legend is unchanged. "
            "Every matching rule applies; where two set the same key the later one "
            "wins, so list the more specific rule last. A `default: true` rule "
            "applies only when no other rule matched. Two columns cannot patch "
            "the same property."
        ),
    )
