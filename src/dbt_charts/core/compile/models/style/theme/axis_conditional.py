"""The x-axis style a cartesian chart's conditional rule may set."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.conditional_predicate import ConditionalPredicate
from dbt_charts.core.compile.models.factories import _PatchBase, build_conditional_body
from dbt_charts.core.compile.models.style.theme.axis import (
    AxisXStyle,
    DimensionLabelStyle,
)

if TYPE_CHECKING:

    class AxisXConditional(BaseModel):
        model_config = ConfigDict(extra="forbid")

        labels: DimensionLabelStyle | None

else:
    AxisXConditional = build_conditional_body(
        AxisXStyle, _PatchBase, "AxisXConditional"
    )


class AxisConditionalPredicate(ConditionalPredicate):
    """A cartesian rule's predicate plus the x-axis label style it applies."""

    axis_x: AxisXConditional | None = Field(
        default=None,
        description=(
            "Restyles the x-axis labels the rule matches. A rule on the x column tests "
            "each label's own value; a rule on another column tests the rows the label "
            "names, which must agree."
        ),
    )
