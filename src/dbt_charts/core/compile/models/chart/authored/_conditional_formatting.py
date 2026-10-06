"""Conditional formatting: predicate rules and column-scoped rule sets."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from dbt_charts.core.compile.models.primitives import FontStyle, ToneLiteral
from dbt_charts.core.compile.models.style.authored import _validate_glyph_pair
from dbt_charts.core.compile.models.style.theme import (
    VALID_FONT_WEIGHTS,
    font_weight_as_css,
)
from dbt_charts.core.utils import coerce_numeric_cell


class _PredicateBase(BaseModel):
    """Shared predicate fields and helpers for conditional rules."""

    # extra="forbid": rejects unknown fields like all authored models.
    # populate_by_name=True: 'in' is a Python keyword, so the field is `in_`
    # in Python; YAML authors write `in:` and the alias bridges the two.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    eq: Any = Field(
        default=None, description="Match rows where the column value equals this value."
    )
    ne: Any = Field(
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
        description="Match rows where the column value falls in [low, high] (inclusive).",
    )
    in_: list[Any] | None = Field(
        default=None,
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
        if v is None:
            return v
        if len(v) != 2:
            raise ValueError(
                f"between expects exactly 2 values [low, high], got {len(v)}"
            )
        low, high = v
        if low > high:
            raise ValueError(f"between requires low <= high, got [{low}, {high}]")
        return v

    @field_validator("in_")
    @classmethod
    def _validate_in(cls, v: list[Any] | None) -> list[Any] | None:
        if v is None:
            return v
        if len(v) == 0:
            raise ValueError("in must be a non-empty list")
        return v


# Derived from _PredicateBase.model_fields — no manual maintenance needed.
# Pydantic preserves field declaration order, so iteration order is stable.
_PREDICATE_OPS: tuple[str, ...] = tuple(_PredicateBase.model_fields.keys())


def _validate_exactly_one_predicate(obj: Any) -> None:
    """Raise ValueError if obj does not have exactly one condition operator set."""
    ops = [f for f in _PREDICATE_OPS if getattr(obj, f) is not None]
    if len(ops) == 0:
        raise ValueError(
            f"{type(obj).__name__} requires exactly one condition operator"
        )
    if len(ops) > 1:
        raise ValueError(
            f"{type(obj).__name__} requires exactly one condition operator, got: {ops}"
        )


def _in_value_matches(candidate: Any, value: Any) -> bool:
    """Equality check for a single ``in`` candidate, with the bool/int guard."""
    if isinstance(value, bool) != isinstance(candidate, bool):
        return False
    return value == candidate


def match_predicate(rule: _PredicateBase, value: Any) -> bool:
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


class ConditionalRule(_PredicateBase):
    """A single conditional formatting rule."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # Style overrides — at least one must be set
    background: str | None = Field(
        default=None, description="Cell background color applied when the rule matches."
    )
    font: FontStyle | None = Field(
        default=None,
        description="Font style overrides (color, weight, style, decoration) applied when the rule matches.",
    )
    glyph: str | None = Field(
        default=None,
        description="Text shown before the cell value when the rule matches.",
    )
    glyph_color: str | None = Field(
        default=None,
        description="Color for the glyph when the rule matches. Requires glyph to be set.",
    )
    tone: ToneLiteral | None = Field(
        default=None,
        description="Semantic tone (positive|negative|warning|info) that colors the glyph via the theme's tone palette; the preferred, theme-adaptive alternative to a raw glyph_color. Requires glyph. Explicit glyph_color wins.",
    )

    @model_validator(mode="after")
    def _validate_rule(self) -> ConditionalRule:
        _validate_exactly_one_predicate(self)
        _font_effective = self.font is not None and (
            self.font.extends is not None
            or self.font.color is not None
            or self.font.weight is not None
            or self.font.style is not None
            or self.font.decoration is not None
        )
        _validate_glyph_pair(self.glyph, self.glyph_color, label="ConditionalRule")
        if self.tone is not None and self.glyph is None:
            raise ValueError("ConditionalRule tone requires glyph to be set")
        if self.background is None and not _font_effective and self.glyph is None:
            raise ValueError(
                "ConditionalRule requires at least one style override "
                "(background, font.color, font.weight, font.style, "
                "font.decoration, or glyph)"
            )
        if self.font is not None and self.font.weight is not None:
            if font_weight_as_css(self.font.weight) not in VALID_FONT_WEIGHTS:
                raise ValueError(
                    f"ConditionalRule font.weight {self.font.weight!r} is not a valid "
                    "CSS font-weight; use a numeric string (e.g. '700') or 'normal'/'bold'."
                )
        return self


class FieldConditionalFormatting(BaseModel):
    """Conditional formatting rules scoped to a single column."""

    model_config = ConfigDict(extra="forbid")

    when: list[ConditionalRule] = Field(
        description="Ordered list of conditional rules. The first matching rule applies; a 'default: true' rule must be last."
    )

    @model_validator(mode="after")
    def _validate_default_position(self) -> FieldConditionalFormatting:
        for i, rule in enumerate(self.when):
            if rule.default is True and i != len(self.when) - 1:
                raise ValueError(
                    "ConditionalRule with default: true must be the last entry "
                    f"in a when list (found at position {i} of {len(self.when)})"
                )
        return self
