"""The condition half of a conditional rule, shared by every family's rule type."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BeforeValidator, ConfigDict, Field, StrictBool, field_validator

from dbt_charts.core.compile.models.aggregate import (
    AGGREGATE_FUNCTIONS,
    AggregateAnchor,
    aggregate_call,
    call,
)
from dbt_charts.core.compile.models.constraints import (
    AtLeastOneOf,
    AtMostOneOf,
    ConstrainedModel,
    Constraint,
)
from dbt_charts.core.utils import coerce_numeric_cell

Threshold = int | float | date | datetime

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


def _parse_aggregate(
    value: object,  # type-state: object_annotation — raw YAML
) -> object:  # type-state: object_annotation — the field validates it
    """A call string becomes its aggregate; anything else passes."""
    if not isinstance(value, str) or (parsed := call(value)) is None:
        return value
    if (aggregate := aggregate_call(*parsed)) is None:
        raise ValueError(
            f"{value.strip()!r} is not an aggregate function; a rule takes "
            f"{AGGREGATE_FUNCTIONS}"
        )
    return aggregate


def _parse_extreme(
    value: object,  # type-state: object_annotation — raw YAML
) -> object:  # type-state: object_annotation — the field validates it
    """``eq``/``ne`` take only an extreme: a mean or percentile rarely equals a cell.

    The field is ``Any``, so a mapping (an aggregate's dumped form) is parsed here.
    """
    parsed = (
        AggregateAnchor.model_validate(value)
        if isinstance(value, Mapping)
        else _parse_aggregate(value)
    )
    if isinstance(parsed, AggregateAnchor) and parsed.fn not in ("min", "max"):
        raise ValueError(
            "eq and ne take min() or max(); compare with avg(), median() or "
            "percentile() through lt, lte, gt, gte or between"
        )
    return parsed


Bound = Annotated[
    Threshold | AggregateAnchor,
    BeforeValidator(_parse_aggregate, json_schema_input_type=Threshold | str),
]


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

    eq: Annotated[
        Any,  # type-state: explicit_any — cell value
        BeforeValidator(_parse_extreme),
    ] = Field(
        default=None,
        description=(
            "Match rows where the column value equals this value, or the "
            "column's min() or max()."
        ),
    )
    ne: Annotated[
        Any,  # type-state: explicit_any — cell value
        BeforeValidator(_parse_extreme),
    ] = Field(
        default=None,
        description=(
            "Match rows where the column value does not equal this value, or the "
            "column's min() or max()."
        ),
    )
    lt: Bound | None = Field(
        default=None,
        description="Match rows where the column value is less than this number, date or aggregate.",
    )
    lte: Bound | None = Field(
        default=None,
        description="Match rows where the column value is less than or equal to this number, date or aggregate.",
    )
    gt: Bound | None = Field(
        default=None,
        description="Match rows where the column value is greater than this number, date or aggregate.",
    )
    gte: Bound | None = Field(
        default=None,
        description="Match rows where the column value is greater than or equal to this number, date or aggregate.",
    )
    between: list[Bound] | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description="Match rows where the column value falls in [low, high] (inclusive); a bound may be an aggregate.",
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
    def _validate_between(cls, v: list[Bound] | None) -> list[Bound] | None:
        if v is None:
            return v
        low, high = v
        if isinstance(low, AggregateAnchor) or isinstance(high, AggregateAnchor):
            return v
        if isinstance(low, date) != isinstance(high, date):
            raise ValueError("between needs two numbers or two dates, not a mix")
        if _order_key(low) > _order_key(high):
            raise ValueError(f"between requires low <= high, got [{low}, {high}]")
        return v


def _in_value_matches(
    candidate: Any,  # type-state: explicit_any — cell value
    value: Any,  # type-state: explicit_any — cell value
) -> bool:
    """Equality check for a single ``in`` candidate, with the bool/int guard."""
    if isinstance(value, bool) != isinstance(candidate, bool):
        return False
    return value == candidate


def _days(value: date | datetime) -> float:
    """A date or datetime as days since the epoch; a naive datetime is UTC."""
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=UTC)
        return aware.timestamp() / 86400
    return float(value.toordinal() - date(1970, 1, 1).toordinal())


def _order_key(value: Threshold) -> float:
    return _days(value) if isinstance(value, date) else float(value)


def _date_cell_days(
    value: Any,  # type-state: explicit_any — cell value
) -> float:
    """A cell compared with a date operand: a date, a datetime or ISO text."""
    if isinstance(value, date):
        return _days(value)
    if isinstance(value, str):
        try:
            return _days(datetime.fromisoformat(value))
        except ValueError:
            pass
    raise ValueError(
        f"{value!r} is not a date; a rule with a date operand compares dates only"
    )


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
    if any(isinstance(bound, date) for bound in _bounds(rule)):
        if value is None:
            return False
        v = _date_cell_days(value)
    else:
        v_coerced = coerce_numeric_cell(value)
        if v_coerced is None:
            return False
        v = v_coerced
    if rule.lt is not None:
        return v < _literal(rule.lt)
    if rule.lte is not None:
        return v <= _literal(rule.lte)
    if rule.gt is not None:
        return v > _literal(rule.gt)
    if rule.gte is not None:
        return v >= _literal(rule.gte)
    if rule.between is not None:
        low, high = rule.between
        return _literal(low) <= v <= _literal(high)
    return False


def _literal(bound: Threshold | AggregateAnchor) -> float:
    if isinstance(bound, AggregateAnchor):
        raise TypeError("resolve computes an aggregate bound before rows match")
    return _order_key(bound)


def _bounds(rule: ConditionalPredicate) -> list[Threshold | AggregateAnchor]:
    bounds = [b for b in (rule.lt, rule.lte, rule.gt, rule.gte) if b is not None]
    if rule.between is not None:
        bounds.extend(rule.between)
    return bounds


def default_rule_last(rules: list[ConditionalPredicate]) -> list[ConditionalPredicate]:
    """A column's rule list: a ``default: true`` rule is only valid as the last."""
    for index, rule in enumerate(rules):
        if rule.default is True and index != len(rules) - 1:
            raise ValueError(
                "A rule with default: true must be the last entry in a column's "
                f"rules (found at position {index} of {len(rules)})"
            )
    return rules
