"""Aggregate calls (`max()`, `percentile(25)`) annotations and conditional rules share."""

from __future__ import annotations

import re
from typing import ClassVar, Literal

from pydantic import ConfigDict, Field

from dbt_charts.core.compile.models.constraints import (
    ConstrainedModel,
    Constraint,
    Forbids,
    Requires,
    ValueIn,
)

_CALL = re.compile(r"^\s*([A-Za-z_]+)\s*\((.*)\)\s*$")
AggregateFn = Literal["min", "max", "avg", "median", "percentile"]
AGGREGATE_FUNCTIONS = "min(), max(), avg(), median(), percentile(<0-100>)"


class AggregateAnchor(ConstrainedModel):
    """A value computed over a column's rows."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    __constraints__: ClassVar[tuple[Constraint, ...]] = (
        Requires(
            ("percent",),
            "percentile takes a percent.",
            when=(ValueIn("fn", ("percentile",)),),
        ),
        Forbids(
            ("percent",),
            "Only percentile takes a percent.",
            when=(ValueIn("fn", ("percentile",), negate=True),),
        ),
    )

    fn: AggregateFn = Field(description="Which aggregate.")
    percent: float | None = Field(
        default=None, ge=0, le=100, description="The percentile, 0 to 100."
    )


def call(text: str) -> tuple[str, str] | None:
    """``name(arg)`` as ``(name, arg)``; ``None`` when ``text`` is no call."""
    match = _CALL.match(text)
    return (match.group(1), match.group(2).strip()) if match else None


def number(arg: str, what: str) -> float:
    try:
        return float(arg)
    except ValueError:
        raise ValueError(f"{what} takes a number, got {arg!r}") from None


def aggregate_call(name: str, arg: str) -> AggregateAnchor | None:
    """The aggregate ``name(arg)`` names; ``None`` when it names none."""
    if name in ("min", "max", "avg", "median") and not arg:
        return AggregateAnchor(fn=name)
    if name == "percentile" and arg:
        percent = number(arg, "percentile()")
        if not 0 <= percent <= 100:
            raise ValueError(f"function percentile() takes 0 to 100, got {arg}")
        return AggregateAnchor(fn=name, percent=percent)
    return None
