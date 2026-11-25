"""The anchor scalar: a literal in the axis column's type, or a call from a
closed function set (`avg()`, `percentile(25)`, `now()`, `current(month)`),
parsed into a typed model. What a literal means (a date, a number, a category)
depends on the axis it lands on, which resolve knows.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from dbt_charts.core.compile.models.aggregate import (
    AGGREGATE_FUNCTIONS,
    AggregateAnchor,
    aggregate_call,
    call,
)
from dbt_charts.core.compile.models.constraints import (
    ConstrainedModel,
    Constraint,
    Forbids,
    Requires,
    ValueIn,
)

_ANCHOR_FUNCTIONS = (
    f"{AGGREGATE_FUNCTIONS}, now(), current(day | week | month | quarter | year)"
)
ClockUnit = Literal["day", "week", "month", "quarter", "year"]
_CLOCK_UNITS: tuple[ClockUnit, ...] = ("day", "week", "month", "quarter", "year")


class ClockAnchor(ConstrainedModel):
    """The render time, or the calendar period that holds it."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    __constraints__: ClassVar[tuple[Constraint, ...]] = (
        Requires(
            ("unit",),
            "current takes a unit.",
            when=(ValueIn("fn", ("current",)),),
        ),
        Forbids(
            ("unit",),
            "now takes no unit.",
            when=(ValueIn("fn", ("now",)),),
        ),
    )

    fn: Literal["now", "current"] = Field(description="Which clock reading.")
    unit: ClockUnit | None = Field(
        default=None, description="The period a current() call fills."
    )


def _parse_anchor_function(
    value: object,  # type-state: object_annotation — raw YAML
) -> object:  # type-state: object_annotation — the field validates it
    """A function call string becomes its typed anchor; anything else passes."""
    if not isinstance(value, str) or (parsed := call(value)) is None:
        return value
    name, arg = parsed
    if (aggregate := aggregate_call(name, arg)) is not None:
        return aggregate
    if name == "now" and not arg:
        return {"fn": name}
    if name == "current" and arg in _CLOCK_UNITS:
        return {"fn": name, "unit": arg}
    raise ValueError(
        f"function {value.strip()!r} is not an anchor function; anchors take "
        f"{_ANCHOR_FUNCTIONS}, with no nesting or arithmetic"
    )


SCALAR_INPUT = float | int | date | str

AnchorScalar = Annotated[
    float | datetime | date | str | AggregateAnchor | ClockAnchor,
    BeforeValidator(_parse_anchor_function, json_schema_input_type=SCALAR_INPUT),
]


# A number, or an ISO date (`YYYY-MM-DD`, `YYYY-MM-DDTHH:MM[:SS]`). Dates are
# never numbers.
ResolvedScalar = float | str


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ResolvedValueAnchor(Frozen):
    kind: Literal["value"] = Field(description="A single value on the axis.")
    value: ResolvedScalar = Field(description="Where the annotation sits.")


class ResolvedCategoryAnchor(Frozen):
    kind: Literal["category"] = Field(description="One slot on a discrete axis.")
    value: str = Field(description="The category whose slot is filled.")
