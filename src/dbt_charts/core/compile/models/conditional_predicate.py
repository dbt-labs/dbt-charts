"""The condition half of a conditional rule, shared by every family's rule type."""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import ConfigDict, Field, StrictBool, field_validator

from dbt_charts.core.compile.models.constraints import (
    AtLeastOneOf,
    AtMostOneOf,
    ConstrainedModel,
    Constraint,
)
from dbt_charts.core.utils import coerce_numeric_cell

PREDICATE_KEYS: tuple[str, ...] = (
    "eq",
    "ne",
    "lt",
    "lte",
    "gt",
    "gte",
    "between",
    "in",
    "is_null",
    "default",
)


class ConditionalPredicate(ConstrainedModel):
    """The condition half of a conditional rule: exactly one operator."""

    __constraints__: ClassVar[tuple[Constraint, ...]] = (
        AtLeastOneOf(
            PREDICATE_KEYS, "A conditional rule requires exactly one condition operator"
        ),
        AtMostOneOf(
            PREDICATE_KEYS,
            "A conditional rule requires exactly one condition operator, got: {set}",
        ),
    )

    # 'in' is a Python keyword, so the field is `in_` in Python and the alias
    # `in` is the only authored spelling. Dumping by alias lets every
    # dump-and-revalidate site in the pipeline round-trip.
    model_config = ConfigDict(
        extra="forbid", serialize_by_alias=True, allow_inf_nan=False
    )

    eq: Any = Field(  # type-state: explicit_any — cell value
        default=None, description="Match rows where the column value equals this value."
    )
    ne: Any = Field(  # type-state: explicit_any — cell value
        default=None,
        description="Match rows where the column value does not equal this value.",
    )
    lt: int | float | None = Field(
        default=None,
        description="Match rows where the column value is less than this number.",
    )
    lte: int | float | None = Field(
        default=None,
        description="Match rows where the column value is less than or equal to this number.",
    )
    gt: int | float | None = Field(
        default=None,
        description="Match rows where the column value is greater than this number.",
    )
    gte: int | float | None = Field(
        default=None,
        description="Match rows where the column value is greater than or equal to this number.",
    )
    between: list[int | float] | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description="Match rows where the column value falls in [low, high] (inclusive).",
    )
    in_: list[Any] | None = Field(  # type-state: explicit_any — cell value
        default=None,
        min_length=1,
        alias="in",
        description="Match rows where the column value is in this list.",
    )
    is_null: StrictBool | None = Field(
        default=None, description="Match null rows (true) or non-null rows (false)."
    )
    default: Literal[True] | None = Field(
        default=None,
        description="Catch-all rule that matches any row not matched by earlier rules. Must be the last entry.",
    )

    @field_validator("between")
    @classmethod
    def _validate_between(cls, v: list[int | float] | None) -> list[int | float] | None:
        if v is not None and v[0] > v[1]:
            raise ValueError(f"between requires low <= high, got [{v[0]}, {v[1]}]")
        return v


def _in_value_matches(
    candidate: Any,  # type-state: explicit_any — cell value
    value: Any,  # type-state: explicit_any — cell value
) -> bool:
    """Equality check for a single ``in`` candidate, with the bool/int guard."""
    if isinstance(value, bool) != isinstance(candidate, bool):
        return False
    return value == candidate


def match_predicate(
    rule: ConditionalPredicate,
    value: Any,  # type-state: explicit_any — cell value
) -> bool:
    """Return True if the rule's single predicate matches value."""
    if rule.default is True:
        return True
    if rule.is_null is not None:
        return (value is None) == rule.is_null
    if rule.eq is not None:
        if isinstance(value, bool) != isinstance(rule.eq, bool):
            return False
        return value == rule.eq
    if rule.ne is not None:
        if isinstance(value, bool) != isinstance(rule.ne, bool):
            return True
        return value != rule.ne
    if rule.in_ is not None:
        return any(_in_value_matches(candidate, value) for candidate in rule.in_)
    v_coerced = coerce_numeric_cell(value)
    if v_coerced is None:
        return False
    v = v_coerced
    if rule.lt is not None:
        return v < float(rule.lt)
    if rule.lte is not None:
        return v <= float(rule.lte)
    if rule.gt is not None:
        return v > float(rule.gt)
    if rule.gte is not None:
        return v >= float(rule.gte)
    if rule.between is not None:
        low, high = rule.between
        return float(low) <= v <= float(high)
    return False


def default_rule_last(rules: list[ConditionalPredicate]) -> list[ConditionalPredicate]:
    """A column's rule list: a ``default: true`` rule is only valid as the last."""
    for index, rule in enumerate(rules):
        if rule.default is True and index != len(rules) - 1:
            raise ValueError(
                "A rule with default: true must be the last entry in a column's "
                f"rules (found at position {index} of {len(rules)})"
            )
    return rules
