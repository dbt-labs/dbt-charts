"""Conditional formatting: predicate rules and column-scoped rule sets."""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from dbt_charts.core.compile.models.constraints import (
    AtLeastOneOf,
    AtMostOneOf,
    ConstrainedModel,
    Constraint,
    IsSet,
    Requires,
)
from dbt_charts.core.compile.models.markers import Color
from dbt_charts.core.compile.models.primitives import (
    FontStyle,
    GlyphText,
    ToneLiteral,
)
from dbt_charts.core.compile.models.style.theme import (
    VALID_FONT_WEIGHTS,
    font_weight_as_css,
)
from dbt_charts.core.utils import coerce_numeric_cell


class _PredicateBase(ConstrainedModel):
    """Shared predicate fields and helpers for conditional rules."""

    # 'in' is a Python keyword, so the field is `in_` in Python and the alias
    # `in` is the only authored spelling. Dumping by alias lets every
    # dump-and-revalidate site in the pipeline round-trip.
    model_config = ConfigDict(
        extra="forbid", serialize_by_alias=True, allow_inf_nan=False
    )

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
        min_length=2,
        max_length=2,
        description="Match rows where the column value falls in [low, high] (inclusive).",
    )
    in_: list[Any] | None = Field(
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


_PREDICATE_KEYS: tuple[str, ...] = tuple(
    field.alias or name for name, field in _PredicateBase.model_fields.items()
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

    __constraints__: ClassVar[tuple[Constraint, ...]] = (
        AtLeastOneOf(
            _PREDICATE_KEYS, "ConditionalRule requires exactly one condition operator"
        ),
        AtMostOneOf(
            _PREDICATE_KEYS,
            "ConditionalRule requires exactly one condition operator, got: {set}",
        ),
        Requires(
            ("glyph",),
            "ConditionalRule glyph_color requires glyph to be set",
            when=(IsSet("glyph_color"),),
        ),
        Requires(
            ("glyph",),
            "ConditionalRule tone requires glyph to be set",
            when=(IsSet("tone"),),
        ),
        AtLeastOneOf(
            (
                "background",
                "font.extends",
                "font.color",
                "font.weight",
                "font.style",
                "font.decoration",
                "glyph",
            ),
            "ConditionalRule requires at least one style override "
            "(background, font.color, font.weight, font.style, "
            "font.decoration, or glyph)",
        ),
    )

    # Style overrides — at least one must be set
    background: str | None = Field(
        default=None, description="Cell background color applied when the rule matches."
    )
    font: FontStyle | None = Field(
        default=None,
        description="Font style overrides (color, weight, style, decoration) applied when the rule matches.",
    )
    glyph: GlyphText | None = Field(
        default=None,
        description="Text shown before the cell value when the rule matches.",
    )
    glyph_color: Annotated[str | None, Color()] = Field(
        default=None,
        description="Color for the glyph when the rule matches. Requires glyph to be set.",
    )
    tone: ToneLiteral | None = Field(
        default=None,
        description="Semantic tone (positive|negative|warning|info) that colors the glyph via the theme's tone palette; the preferred, theme-adaptive alternative to a raw glyph_color. Requires glyph. Explicit glyph_color wins.",
    )

    @model_validator(mode="after")
    def _validate_rule(self) -> ConditionalRule:
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
