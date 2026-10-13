"""Cross-cutting compile primitives shared across all compile stages.

Contains leaf value types used by both authored-input models (types.py) and
compiled-output models (models/board/compiled.py, models/style/compiled.py), and therefore
cannot live in either without creating a circular import.
"""

from __future__ import annotations

import dataclasses
import re
from functools import cached_property
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeVar

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    PlainSerializer,
    PositiveInt,
    SerializationInfo,
    StringConstraints,
    ValidationError,
    ValidatorFunctionWrapHandler,
    WrapValidator,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic_core import core_schema

from d3_format import LocaleDefinition
from dbt_charts.core.colors import CSS_COLOR_NAMES
from dbt_charts.core.compile.models.factories import (
    FontAlias,
    FontSlotBase,
    _PatchBase,
    build_patch_model_ext,
    register_as_own_patch,
    register_patch,
    register_shorthand,
)
from dbt_charts.core.compile.models.markers import (
    Color,
    FontFamily,
    Inherit,
    Merge,
    Palette,
    Strategy,
)
from dbt_charts.core.compile.models.schema_names import (
    VEGA_SCHEME_NAMES,
    ScalePaletteName,
    StopsPaletteName,
)
from dbt_charts.core.text.currencies import CURRENCIES, CurrencyCode, with_symbol
from dbt_charts.core.text.format_d3 import (
    VEGA_SAFE_TIME_DIRECTIVES,
    WEEKDAYS,
    HouseLocale,
    Notation,
    NumberLocaleName,
    SignPlacement,
    TimeLocaleDefinition,
    TimeLocaleName,
    WeekdayName,
    default_sign_placement,
    house_locale,
    number_locale_definition,
    time_directive_letters,
    time_locale_definition,
    time_locale_week_start,
    unsupported_time_directives,
)
from dbt_charts.core.text.magnitude_words import magnitude_words
from dbt_charts.core.text.predefined_formats import PREDEFINED_NUMBER_NAMES

# A frozenset dumps in Python's per-process hash-seed order, so two otherwise-
# identical processes emit different orderings for the same set. Every model
# carrying variable names dumps them sorted for a deterministic wire shape.
VariableDependencies = Annotated[
    frozenset[str], PlainSerializer(sorted, return_type=list[str])
]

# Within-group spacing for grouped bars. Keywords resolve to an overlap fraction
# of bar width at render time; a number is that fraction directly (>0 overlaps,
# 0 touches, <0 gaps). None means "not authored" → renderer 'auto'.
OverlapSpec = Literal["auto", "none", "flush", "partial", "full"] | float | None

# Line/area interpolation curve, shared by theme-stage (LineMarkStyle/
# AreaMarkStyle) and resolved-stage (ResolvedLineMarkStyle/ResolvedAreaMarkStyle)
# mark models — lives here (not in style/theme/ or style/resolved/) because both
# packages import it and either direction would be a circular import. A curated
# subset of Vega-Lite's `interpolate` values: no "step-band" (retired — the
# band-aware plateau is the plain `step` keyword on a categorical x-axis, see
# render/chart/step_band.py), no bundle/open/closed/catmull-rom variants.
Curve = Literal[
    "linear",
    "monotone",
    "natural",
    "basis",
    "cardinal",
    "step",
    "step-before",
    "step-after",
]

# Semantic tone vocabulary shared by KPI value/glyph and table conditional
# glyphs. Lives here (not in the authored kpi model) because
# _conditional_formatting.py needs it too, and importing from the kpi model would
# create an import cycle (kpi -> _base -> _conditional_formatting).
ToneLiteral = Literal["positive", "negative", "warning", "info"]

# HTML rendering tier for board body text (`html_policy` field). Ordered from
# most to least restrictive — declaration order is the canonical ordering, so
# `get_args(HtmlPolicy)` gives the tuple used by cap_html_policy. Lives here
# (not in models/board/authored or compile/config) because authored, normalized,
# resolved, and config all use it and each pair is a circular edge.
HtmlPolicy = Literal["none", "safe-subset", "trusted-raw"]

# SVG/Vega-Lite stroke-linecap vocabulary. One home: border dash caps, the
# shared StrokeStyle, and the resolved stroke all name the same three values.
LineCap = Literal["butt", "round", "square"]


@dataclasses.dataclass(frozen=True)
class ErrorHint:
    """Annotated marker: every rule declared before it fails with ``hint``.

    A pattern or Literal states the rule once, for Python and the JSON Schema
    alike; this carries the remediation text pydantic's generic message lacks.
    ``{value}`` in the hint is the rejected value. Only errors of the ``only``
    kinds are replaced (default: a pattern or Literal mismatch); a wrong type
    keeps pydantic's own message. ``only=None`` replaces every error.
    """

    hint: str
    only: tuple[str, ...] | None = ("string_pattern_mismatch", "literal_error")

    def __get_pydantic_core_schema__(
        self,
        source: Any,  # type-state: explicit_any — pydantic's annotated type
        handler: GetCoreSchemaHandler,
    ) -> core_schema.CoreSchema:
        def with_hint(
            value: Any,  # type-state: explicit_any — raw value under validation
            inner: ValidatorFunctionWrapHandler,
        ) -> Any:  # type-state: explicit_any — whatever the inner schema returns
            try:
                return inner(value)
            except ValidationError as exc:
                if self.only is not None and any(
                    e["type"] not in self.only for e in exc.errors()
                ):
                    raise
                raise ValueError(self.hint.format(value=value)) from None

        return core_schema.no_info_wrap_validator_function(with_hint, handler(source))


# A string with at least one non-space character: YAML parses a blank value to
# null or "", which must not read as a name.
NON_BLANK = r"\S"

GlyphText = Annotated[
    str,
    StringConstraints(pattern=NON_BLANK),
    ErrorHint("glyph must be a non-empty string, got {value!r}"),
]

# A data-channel column name. A leading `#` is a literal color, which belongs
# in `style:`, never on a channel.
CHANNEL_COLUMN = r"^(?:[^#]|$)"

# Incremental refresh setting, shared by board (AuthoredBoard.incremental) and
# query (_BaseQueryFields.incremental): a watermark column name enables the
# tail path, `false` opts out of an inherited setting, `None` inherits it. A
# bare `True` is rejected by validate_incremental_value — a single field, so
# there is no separate key field left to name a column on.
IncrementalValue = (
    Annotated[
        str,
        StringConstraints(pattern=NON_BLANK),
        ErrorHint(
            "incremental: {value!r} is not valid — give the watermark column "
            "name, or omit the field (or set false) to disable incremental refresh",
            only=None,
        ),
    ]
    | Literal[False]
    | None
)


def validate_incremental_value(
    v: Any,  # type-state: explicit_any — mode="before" validator input; raw YAML value
) -> Any:  # type-state: explicit_any — passthrough of the same boundary value
    """Reject a bare `incremental: true` — it has no watermark column to key on.

    Used as a `field_validator(mode="before")` body on both AuthoredBoard and
    the shared query field, so the message is identical at either scope.
    """
    if v is True:
        raise ValueError(
            "incremental: true is not valid — give the watermark column name "
            "instead, e.g. incremental: updated_at"
        )
    return v


# =============================================================================
# FORMAT CONFIG
# =============================================================================


AffixRepeat = Literal["every", "anchor"]


class FormatConfig(BaseModel):
    """Format configuration for value display.

    This model supports both simple format strings and complex formatting
    with custom prefix/suffix.

    Attributes:
        spec: D3 format string, preset name, or Excel pattern
        prefix: Custom prefix to prepend to formatted value
        suffix: Custom suffix to append to formatted value

    Example YAML:
        format:
          spec: ",.0f"
          prefix: "$"
          suffix: " USD"
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    spec: str | None = Field(
        default=None,
        description="D3 format string (e.g., ',.0f'), preset name (e.g., 'currency'), or Excel pattern.",
    )
    prefix: str | None = Field(
        default=None,
        description="Text placed before the formatted value (e.g., '$').",
    )
    suffix: str | None = Field(
        default=None,
        description="Text placed after the formatted value (e.g., ' USD', '%').",
    )
    notation: Notation | None = Field(
        default=None,
        description="Notation style: 'analytic' for SI-prefix (1B, 1M) or 'narrative' for prose-style (1bn, 1mn).",
    )
    sign_placement: SignPlacement | None = Field(
        default=None,
        description=(
            "Where a negative sign sits against the prefix: 'before_prefix' "
            "(−€500) or 'after_prefix' (EUR −500). Unset: after_prefix when "
            "the prefix ends in a space or paints on one value only, else "
            "before_prefix. Table columns keep the sign with the digits and "
            "reject it."
        ),
    )
    repeat: AffixRepeat | None = Field(
        default=None,
        description=(
            "Whether the prefix/suffix paints on 'every' value of an axis, "
            "table column or support_table row, or on one 'anchor' value. "
            "Unset keeps each surface's default. Single-value slots ignore it."
        ),
    )


_ModelT = TypeVar("_ModelT", bound=BaseModel)


def resolved_as(
    cls: type[_ModelT],
    model: BaseModel,
    **update: Any,  # type-state: explicit_any — replacement field values
) -> _ModelT:
    """``model``'s fields with ``update`` applied, as the resolved subclass ``cls``."""
    return cls(**{**dict(model), **update})


AuthoredFormat = str | FormatConfig
"""A format as authored: a spec, preset or alias name, or a format block."""


FormatAliases = dict[str, AuthoredFormat]
"""The ``style.formats`` alias table."""


_MagnitudeWord = Annotated[str, StringConstraints(pattern=r"\S")]
_MagnitudeWords = tuple[_MagnitudeWord, _MagnitudeWord, _MagnitudeWord, _MagnitudeWord]


class NumberLocale(BaseModel):
    """A d3 formatLocale definition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decimal: str = Field(description='The decimal mark (e.g. `"."`).')
    thousands: str = Field(description='The group separator (e.g. `","`).')
    grouping: tuple[PositiveInt, ...] = Field(
        min_length=1,
        description="The group sizes (e.g. `[3]`), cycled as needed.",
    )
    currency: tuple[str, str] = Field(
        description=(
            'The currency prefix and suffix (e.g. `["$", ""]`) a `$` in a d3 '
            "spec prints."
        ),
    )
    percent: str = Field(default="%", description="The percent sign.")
    minus: str = Field(default="\u2212", description="The minus sign.")
    analytic: _MagnitudeWords = Field(
        default=magnitude_words("en-US")[0],
        description=(
            "The extra-short thousand, million, billion and trillion "
            "suffixes, where a magnitude is declared once (en-US: K, M, B, T). "
            "A leading space, where a locale wants one, is part of the "
            "suffix."
        ),
    )
    narrative: _MagnitudeWords = Field(
        default=magnitude_words("en-US")[1],
        description=(
            "The abbreviated thousand, million, billion and trillion "
            'suffixes on every value (en-US: `["k", "mn", "bn", "trn"]`).'
        ),
    )


_Days = tuple[str, str, str, str, str, str, str]
_Months = tuple[str, str, str, str, str, str, str, str, str, str, str, str]


def _checked_pattern(forbidden: str) -> BeforeValidator:
    """Runs before ``pattern`` so its clearer message wins."""

    def check(
        pattern: Any,  # type-state: explicit_any — validator input, any JSON value
    ) -> Any:  # type-state: explicit_any — a non-str is left for the str schema
        if not isinstance(pattern, str):
            return pattern
        if unsupported := unsupported_time_directives(pattern):
            raise ValueError(
                f"directives {unsupported} are not painted identically by Vega "
                "and Python; use only d3-time-format directives"
            )
        if recursive := sorted(set(time_directive_letters(pattern)) & set(forbidden)):
            raise ValueError(
                f"directives {recursive} would expand into themselves here"
            )
        return pattern

    return BeforeValidator(check)


def _token_pattern(forbidden: str) -> str:
    """Whole painted directives only, so expansion can't splice a new one."""
    letters = "".join(sorted(VEGA_SAFE_TIME_DIRECTIVES - set(forbidden)))
    return rf"^(?:[^%]|%%|%[-_0]?[{letters}])*$"


_DateTimePattern = Annotated[
    str,
    _checked_pattern("c"),
    Field(description="The `%c` pattern.", pattern=_token_pattern("c")),
]
_DatePattern = Annotated[
    str,
    _checked_pattern("cxX"),
    Field(description="The `%x` pattern.", pattern=_token_pattern("cxX")),
]
_TimePattern = Annotated[
    str,
    _checked_pattern("cxX"),
    Field(description="The `%X` pattern.", pattern=_token_pattern("cxX")),
]


class TimeLocale(BaseModel):
    """A d3 timeFormatLocale definition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    date_time: _DateTimePattern
    date: _DatePattern
    time: _TimePattern
    periods: tuple[str, str] = Field(description="The AM and PM names.")
    days: _Days = Field(description="The weekday names, Sunday first.")
    short_days: _Days = Field(description="The abbreviated weekday names.")
    months: _Months = Field(description="The month names, January first.")
    short_months: _Months = Field(description="The abbreviated month names.")
    week_start: WeekdayName = Field(description="The first day of the calendar week.")


class RegionalLocale(BaseModel):
    """The d3 locales a board writes numbers and dates in."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    number: NumberLocaleName | NumberLocale = Field(
        default="en-US",
        description="A d3-format locale name or definition.",
    )
    time: TimeLocaleName | TimeLocale = Field(
        default="en-US",
        description="A d3-time-format locale name or definition.",
    )


class Regional(BaseModel):
    """How a board writes numbers and dates: its d3 locales and currency symbol."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    locale: RegionalLocale = Field(
        default_factory=RegionalLocale,
        description="The number and time locales.",
    )
    currency: CurrencyCode | None = Field(
        default=None,
        description=(
            "ISO 4217 code whose symbol replaces the locale's own in its currency "
            "template, at the locale's placement. Unset prints the locale's own."
        ),
    )

    @model_validator(mode="after")
    def _currency_has_a_place(self) -> Regional:
        number = self.locale.number
        if (
            self.currency is not None
            and isinstance(number, NumberLocale)
            and not "".join(number.currency).strip()
        ):
            raise ValueError(
                "regional.currency replaces the symbol in "
                "regional.locale.number.currency, which has none: give the "
                'template one (e.g. ["$", ""]).'
            )
        return self

    @cached_property
    def format_locale(self) -> HouseLocale:
        locale = self.locale.number
        definition: LocaleDefinition
        if isinstance(locale, NumberLocale):
            definition = {
                "decimal": locale.decimal,
                "thousands": locale.thousands,
                "grouping": list(locale.grouping),
                "currency": list(locale.currency),
                "percent": locale.percent,
                "minus": locale.minus,
            }
            analytic, narrative = locale.analytic, locale.narrative
        else:
            definition = number_locale_definition(locale).copy()
            analytic, narrative = magnitude_words(locale)
        if self.currency is not None:
            template = definition.get("currency")
            if template is None:
                raise ValueError(f"locale {locale!r} has no currency template")
            before, after = template
            symbol = CURRENCIES[self.currency].symbol
            definition["currency"] = list(with_symbol((before, after), symbol))
        return house_locale(definition, analytic, narrative)

    @cached_property
    def time_locale(self) -> TimeLocaleDefinition:
        locale = self.locale.time
        if isinstance(locale, str):
            return time_locale_definition(locale)
        return {
            "dateTime": locale.date_time,
            "date": locale.date,
            "time": locale.time,
            "periods": list(locale.periods),
            "days": list(locale.days),
            "shortDays": list(locale.short_days),
            "months": list(locale.months),
            "shortMonths": list(locale.short_months),
        }

    @cached_property
    def week_start(self) -> int:
        locale = self.locale.time
        if isinstance(locale, str):
            return time_locale_week_start(locale)
        return WEEKDAYS.index(locale.week_start)


DEFAULT_REGIONAL = Regional()
"""What a style resolved without a board (a theme preview) formats with."""


class FormatTable(BaseModel):
    """What resolves a format name on one board: the author's ``style.formats``
    aliases, and the ``regional.currency`` whose minor unit ``currency_full``
    prints (None: the locale's own currency, at two decimals)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    aliases: FormatAliases
    currency: CurrencyCode | None

    def __contains__(
        self,
        name: object,  # type-state: object_annotation — the `in` protocol's signature
    ) -> bool:
        return name in self.aliases

    def __getitem__(self, name: str) -> AuthoredFormat:
        return self.aliases[name]


AUTHORED_FORMATS = "authored_formats"
"""Serialization context flag: dump each ``ResolvedFormat`` as authored board YAML."""


class ResolvedFormat(FormatConfig):
    """A resolved d3 spec plus the affix/notation it cannot carry, and the
    authored spec (``raw``) house rules key on.

    Serializes to the bare spec when it carries neither affix nor ``raw``, else
    to a ``FormatConfig``-shaped object plus ``raw``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    spec: str = ""
    prefix: str = ""
    suffix: str = ""
    notation: Notation | None = None
    repeat: AffixRepeat | None = None
    raw: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_wire(
        cls,
        value: Any,  # type-state: explicit_any — validator boundary input
    ) -> Any:  # type-state: explicit_any — validator boundary output
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return {"spec": value}
        if isinstance(value, FormatConfig):
            value = value.model_dump()
        if isinstance(value, dict):
            return {
                k: "" if v is None and k in ("spec", "prefix", "suffix") else v
                for k, v in value.items()
            }
        return value

    @model_serializer(mode="plain")
    def _to_wire(
        self, info: SerializationInfo
    ) -> str | dict[str, Any]:  # type-state: explicit_any — JSON object
        if info.context and info.context.get(AUTHORED_FORMATS):
            if self._is_bare:
                return self.spec if self.raw is None else self.raw
            config = self._config().model_dump(exclude_none=True)
            return config if self.raw is None else {**config, "spec": self.raw}
        if self._is_bare and self.raw is None:
            return self.spec
        wire = self._config().model_dump(exclude_none=True)
        if self.raw is not None:
            wire["raw"] = self.raw
        return wire

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: Any,  # type-state: explicit_any — pydantic CoreSchema
        handler: Any,  # type-state: explicit_any — pydantic GetJsonSchemaHandler
    ) -> Any:  # type-state: explicit_any — JsonSchemaValue
        return {"anyOf": [{"type": "string"}, handler(core_schema)]}

    @property
    def has_affix(self) -> bool:
        return bool(self.prefix or self.suffix or self.notation is not None)

    @property
    def _is_bare(self) -> bool:
        return (
            not self.has_affix and self.repeat is None and self.sign_placement is None
        )

    @property
    def placement(self) -> SignPlacement:
        """Effective placement; ``sign_placement`` is the authored value, None
        when unset."""
        if self.sign_placement is not None:
            return self.sign_placement
        return default_sign_placement(self.prefix)

    @property
    def is_house(self) -> bool:
        """True when ``raw`` names a predefined number format (house rules apply)."""
        return self.raw is not None and self.raw in PREDEFINED_NUMBER_NAMES

    @property
    def is_spec_less_affix(self) -> bool:
        """True when an affix is present but no spec (``column_format`` fills it)."""
        return not self.spec and self.has_affix

    @property
    def authored(self) -> AuthoredFormat | None:
        """The authored input, to re-resolve through ``format_value``."""
        if self.raw is None or self._is_bare:
            return self.raw
        return self._config().model_copy(update={"spec": self.raw})

    def _config(self) -> FormatConfig:
        return FormatConfig(
            spec=self.spec or None,
            prefix=self.prefix or None,
            suffix=self.suffix or None,
            notation=self.notation,
            sign_placement=self.sign_placement,
            repeat=self.repeat,
        )


def signs_before_prefix(fmt: ResolvedFormat) -> bool:
    """An authored ``before_prefix`` on a prefix, which no anchored surface can
    paint."""
    return bool(fmt.prefix) and fmt.sign_placement == "before_prefix"


def painted_sign_placement(fmt: ResolvedFormat, anchored: bool) -> SignPlacement:
    """The placement a painter composes: an anchored currency or prefix (one
    value per ladder, column or strip) leads the sign, so the anchor's digits
    line up with the bare values beside it (``€−50`` above ``−40``)."""
    return "after_prefix" if anchored else fmt.placement


# =============================================================================
# SPACING PRIMITIVE
# =============================================================================


_SPACING_LENGTH = r"[0-9]+(?:\.[0-9]+)?(?:px)?"
_SPACING_PATTERN = rf"{_SPACING_LENGTH}(?: {_SPACING_LENGTH}){{0,3}}"


class SpacingValues(BaseModel):
    """Pre-parsed CSS spacing (margin/padding).

    CSS spacing supports 1-4 values:
    - "16px" → all sides equal
    - "8px 16px" → vertical, horizontal
    - "8px 16px 12px" → top, horizontal, bottom
    - "8px 16px 12px 4px" → top, right, bottom, left

    Attributes:
        top: Top spacing in pixels
        right: Right spacing in pixels
        bottom: Bottom spacing in pixels
        left: Left spacing in pixels
    """

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    top: float = Field(default=0.0, description="Top spacing in pixels.")
    right: float = Field(default=0.0, description="Right spacing in pixels.")
    bottom: float = Field(default=0.0, description="Bottom spacing in pixels.")
    left: float = Field(default=0.0, description="Left spacing in pixels.")

    @property
    def horizontal(self) -> float:
        """Total horizontal spacing (left + right)."""
        return self.left + self.right

    @property
    def vertical(self) -> float:
        """Total vertical spacing (top + bottom)."""
        return self.top + self.bottom

    @classmethod
    def from_css(cls, value: str) -> SpacingValues:
        """Parse 1-4 CSS lengths (``8px 16px``); raise on anything else."""
        if not re.fullmatch(_SPACING_PATTERN, value):
            raise ValueError(
                f"spacing must be 1-4 pixel lengths such as '16px' or "
                f"'8px 16px', got {value!r}"
            )
        nums = [float(v) for v in re.findall(r"[0-9]+(?:\.[0-9]+)?", value)]
        if len(nums) == 1:
            nums *= 4
        elif len(nums) == 2:
            nums *= 2
        elif len(nums) == 3:
            nums.append(nums[1])
        top, right, bottom, left = nums
        return cls(top=top, right=right, bottom=bottom, left=left)


def _parse_spacing(
    value: Any,  # type-state: explicit_any — before-validator input, raw YAML value
) -> Any:  # type-state: explicit_any — non-string input passes through
    if isinstance(value, bool):
        raise ValueError(f"spacing must be a number or a CSS string, got {value!r}")
    if isinstance(value, (int, float)):
        return SpacingValues(top=value, right=value, bottom=value, left=value)
    if isinstance(value, str):
        return SpacingValues.from_css(value)
    return value


# SpacingValues serves as its own patch: its string/number shorthand is declared
# below; it has defaults on all fields, and should NOT be replaced by an
# auto-generated all-Optional stripped variant in build_patch_model.
register_patch(SpacingValues)(SpacingValues)
register_shorthand(
    SpacingValues,
    BeforeValidator(
        _parse_spacing,
        json_schema_input_type=Annotated[
            str, StringConstraints(pattern=f"^{_SPACING_PATTERN}$")
        ]
        | float
        | SpacingValues,
    ),
)


# =============================================================================
# LEAF VALUE TYPES
# =============================================================================


class SingleLineFontStyle(FontSlotBase):
    """Text appearance for a slot that always paints one line.

    All fields Optional — it doubles as a patch at every level.

    A font slot also accepts a registry name (``font: label``) or
    ``{extends: label, ...overrides}``; the cascade expands both before
    anything downstream reads the slot, so ``extends`` never survives it.
    """

    extends: FontAlias | None = Field(
        default=None,
        description=(
            "Name of a ``style.fonts`` entry this slot starts from; the fields "
            "set beside it override that entry."
        ),
    )
    family: Annotated[str | None, FontFamily()] = Field(
        default=None, description="Font family name (e.g., 'sans-serif', 'Roboto')."
    )
    color: Annotated[str | None, Color()] = Field(
        default=None, description="Text color as a CSS color string."
    )
    size: float | None = Field(default=None, description="Font size in pixels.")
    weight: str | float | None = Field(
        default=None,
        description="How heavy the type is drawn (e.g., 'bold', 400, 700).",
    )
    style: Literal["normal", "italic"] | None = Field(
        default=None, description="Upright or slanted type (normal or italic)."
    )
    decoration: Literal["none", "line-through", "underline"] | None = Field(
        default=None,
        description="Line drawn on the text (underline, line-through, or none).",
    )
    case: (
        Literal["none", "sentence", "title", "upper", "lower", "slug", "camel"] | None
    ) = Field(
        default=None,
        description=(
            "Letter-case transform applied at render time. "
            "'title' uses Chicago/Gruber rules and preserves tokens with internal "
            "capitals (ARR, iPhone). 'sentence' uppercases only the first character. "
            "'none' (default) emits the string without any letter-case change."
        ),
    )


class FontStyle(SingleLineFontStyle):
    """Text appearance, including leading for text that wraps. Merged as a unit."""

    line_height: float | None = Field(
        default=None,
        description=(
            "Line height as a unitless multiple of font size. Cascades through "
            "Style.font to all text roles that carry a FontStyle slot. Body prose "
            "defaults to 1.25; titles override tighter via their font patch in theme YAML."
        ),
    )


class ColumnFontStyle(FontStyle):
    """A table column's font. ``color`` may name a query column, so it is not a color field."""

    color: Annotated[str | None, Color(column_ref=True)] = Field(
        default=None,
        description=(
            "Text color (CSS color string), or a column ID: a value matching a "
            "query column name uses that row's value in the named column."
        ),
    )


class NamedFont(FontStyle):
    """A ``style.fonts`` registry entry: a font other slots reference by name."""

    sizes: Annotated[list[float] | None, Merge(Strategy.OVERRIDE)] = Field(
        default=None,
        description=(
            "The H1–H6 heading ramp (six sizes, indexed by heading level - 1). "
            "Valid only on the ``heading`` font."
        ),
    )


class ResolvedFontStyle(BaseModel):
    """Fully resolved font — all fields concrete after cascade."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    family: str
    color: str
    size: float
    weight: str | float
    style: Literal["normal", "italic"]
    decoration: Literal["none", "line-through", "underline"]
    case: Literal["none", "sentence", "title", "upper", "lower", "slug", "camel"]
    line_height: float
    # Whether `family` guarantees tabular (fixed-advance) digits with no CSS
    # feature toggle — set from the vendored font registry (fonts.font_is_tabular),
    # never authored directly. A column-forming quantitative axis (the ruler
    # consumer) requires this before it can ship the suffix-field
    # reservation (font_measure.compose_suffix_reservation): the padding is
    # composed from measured space characters to match the suffix's
    # advance, and the column holds together only because every digit
    # shares one fixed advance, not the proportional one every other
    # character (including the padding itself) has.
    tabular_figures: bool


def one_error(message: str) -> WrapValidator:
    """Report any failure under this field as ``message``; ``{value}`` is the input.

    A union reports one error per arm, which buries the rule the author broke.
    """

    def validate(
        value: Any,  # type-state: explicit_any — wrap-validator input, raw YAML value
        handler: ValidatorFunctionWrapHandler,
    ) -> Any:  # type-state: explicit_any — whatever the wrapped schema returns
        try:
            return handler(value)
        except ValidationError:
            raise ValueError(message.format(value=repr(value))) from None

    return WrapValidator(validate)


Dasharray = Annotated[
    str,
    StringConstraints(pattern=r"^\s*\d+(\.\d+)?([ ,]+\d+(\.\d+)?)*\s*$"),
    one_error("dasharray must be positive numbers such as '6 3', got {value}"),
]


class DividerStyle(BaseModel):
    """A separator line: whether it draws, and its color, width and line style.

    Every divider slot is complete in every theme, so a partial write such as
    ``{color: red}`` recolors a divider without switching it on or off.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    visible: bool = Field(description="Draw the divider.")
    color: Annotated[str, Color()] = Field(description="Divider color.")
    width: float = Field(gt=0, description="Divider line width in pixels.")
    # Optional on purpose: unset is a solid line, so no theme has to say so.
    dasharray: Dasharray | None = Field(
        default=None,
        description=(
            'Dash pattern in pixels, such as "6 3" (6 drawn, 3 gap); '
            "unset draws a solid line."
        ),
    )

    # Normalized on the compiled style only, so an authored patch keeps its
    # spelling.
    @field_validator("dasharray")
    @classmethod
    def _space_separated(cls, value: str | None) -> str | None:
        return None if value is None else " ".join(re.split(r"[ ,]+", value.strip()))


class BorderStyle(BaseModel):
    """Box border. width/color/radius are required, produced by cascade from
    theme YAML; dash_array/line_cap/dash_offset are optional authoring opt-ins
    (unset means solid) and no theme is required to supply them.

    Does NOT cascade (ADR-003: box properties reset per level).
    'No border' is expressed as width=0, color='transparent', radius=0.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    width: float = Field(description="Border width in pixels.")
    color: str = Field(description="Border color as a CSS color string.")
    radius: float = Field(description="Border corner radius in pixels.")
    # dash_array/line_cap/dash_offset are genuinely optional, not a theme-population
    # gap: unset means a solid border (the common case, and every built-in theme's
    # default); dashing is an authoring opt-in, not something themes must supply.
    dash_array: Annotated[list[float] | None, Merge(Strategy.OVERRIDE)] = Field(
        default=None,
        description=(
            "SVG stroke-dasharray pattern in pixels (e.g. [4, 4]). "
            "None means a solid border."
        ),
    )
    line_cap: LineCap | None = Field(
        default=None,
        description="How each dash's ends are finished on a dashed border. None uses the renderer default (butt).",
    )
    dash_offset: float | None = Field(
        default=None,
        description="How far into the dash pattern the line starts, in pixels. None means 0.",
    )


class CornerStyle(BaseModel):
    """Corner rounding only, for a slot whose renderer never draws a stroke.

    A full BorderStyle's width/color/dash would validate cleanly here and
    do nothing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    radius: float = Field(description="Corner radius in pixels.")


# One gr[ae]y spelling keeps both variants out of the emitted schema (codespell).
_COLOR_KEYWORDS = "|".join(
    sorted({re.sub("gr[ae]y", "gr[ae]y", n) for n in CSS_COLOR_NAMES} | {"transparent"})
)
_BORDER_COLOR = (
    r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})"
    r"|rgba?\([^)]*\)"
    rf"|{_COLOR_KEYWORDS}"
)
# Capture groups: width, style, color. Lookaheads reject "" and a trailing space.
_BORDER_PATTERN = (
    r"(?=\S)(?!.*\s$)(?:([0-9]+(?:\.[0-9]+)?)px(?: |$))?"
    r"(?:(solid|dashed|dotted)(?: |$))?"
    rf"({_BORDER_COLOR})?"
)
# CSS box-shadow, minus inset and spread: SVG draws it as a drop-shadow filter,
# which has neither.
_LENGTH = r"(-?[0-9]+(?:\.[0-9]+)?)(?:px)?"
BOX_SHADOW_PATTERN = (
    rf"{_LENGTH} {_LENGTH}(?: ([0-9]+(?:\.[0-9]+)?)(?:px)?)? ({_BORDER_COLOR})"
)


@dataclasses.dataclass(frozen=True)
class BoxShadow:
    dx: float
    dy: float
    blur: float
    color: str


def parse_box_shadow(value: str) -> BoxShadow:
    """Parse ``<x> <y> [<blur>] <color>``; ``inset`` and spread are errors."""
    match = re.fullmatch(BOX_SHADOW_PATTERN, value)
    if match is None:
        raise ValueError(
            "box_shadow must look like '0 2px 8px #00000040' (x offset, y offset, "
            "optional blur, hex/rgb()/CSS color keyword; no inset or spread)"
        )
    dx, dy, blur, color = match.groups()
    blur_px = float(blur or 0)  # type-state: silent_fallback — CSS omits blur for 0
    return BoxShadow(float(dx), float(dy), blur_px, color)


def check_box_shadow(
    value: Any,  # type-state: explicit_any — before-validator input, raw YAML value
) -> Any:  # type-state: explicit_any — non-strings go to type validation
    if isinstance(value, str):
        parse_box_shadow(value)
    return value


# Line styles BorderStyle.dash_array can express.
_BORDER_DASHES: dict[str, dict[str, list[float] | str]] = {
    "dashed": {"dash_array": [4.0, 4.0]},
    "dotted": {"dash_array": [1.0, 3.0], "line_cap": "round"},
}


@register_patch(BorderStyle)
class BorderStylePatch(BaseModel):
    # Hand-written (not build_patch_model): carries from_css() classmethod
    """Box border. Does NOT cascade (ADR-003: box properties reset per level).

    Doubles as CSS shorthand parser via from_css() for StylePatch.border.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    radius: float | None = Field(
        default=None, description="Border corner radius in pixels."
    )
    color: Annotated[str | None, Color()] = Field(
        default=None, description="Border color (CSS color string)."
    )
    width: float | None = Field(default=None, description="Border width in pixels.")
    dash_array: Annotated[list[float] | None, Merge(Strategy.OVERRIDE)] = Field(
        default=None,
        description=(
            "SVG stroke-dasharray pattern in pixels (e.g. [4, 4]). "
            "None means a solid border."
        ),
    )
    line_cap: LineCap | None = Field(
        default=None,
        description="How each dash's ends are finished on a dashed border. None uses the renderer default (butt).",
    )
    dash_offset: float | None = Field(
        default=None,
        description="How far into the dash pattern the line starts, in pixels. None means 0.",
    )

    @classmethod
    def from_css(cls, value: str) -> BorderStylePatch:
        """Parse CSS border shorthand ``2px dashed #333``.

        The grammar is ``[<n>px] [solid|dashed|dotted] [<color>]``, where color
        is hex, ``rgb()``/``rgba()`` or a lowercase CSS color keyword. ``dashed``
        and ``dotted`` set ``dash_array``. Radius is never read from the string;
        set ``border.radius`` explicitly in YAML.
        """
        match = re.fullmatch(_BORDER_PATTERN, value)
        if match is None:
            raise ValueError(
                f"border must look like '2px dashed #333' (width in px, "
                f"solid/dashed/dotted, hex/rgb()/CSS color keyword), got {value!r}"
            )
        width, line_style, color = match.groups()
        parsed: dict[str, float | str | list[float]] = {}
        if line_style in _BORDER_DASHES:
            parsed.update(_BORDER_DASHES[line_style])
        if width is not None:
            parsed["width"] = float(width)
        if color is not None:
            parsed["color"] = color
        return cls.model_validate(parsed)


def _parse_border(
    value: Any,  # type-state: explicit_any — before-validator input, raw YAML value
) -> Any:  # type-state: explicit_any — non-string input passes through
    if isinstance(value, str):
        return BorderStylePatch.from_css(value).model_dump(exclude_unset=True)
    return value


# BorderStylePatch is all-Optional and is its own patch — prevent build_patch_model
# from synthesizing a double-suffixed BorderStylePatchPatch when BarChartStyle
# (which inherits border: BorderStylePatch | None) is patched for authored overlays.
register_as_own_patch(BorderStylePatch)
register_shorthand(
    BorderStylePatch,
    BeforeValidator(
        _parse_border,
        json_schema_input_type=Annotated[
            str, StringConstraints(pattern=f"^{_BORDER_PATTERN}$")
        ]
        | BorderStylePatch,
    ),
)


class StrokeStyle(BaseModel):
    """Stroke appearance sub-block shared across mark families.

    All fields optional — StrokeStyle doubles as the overlay type for cascade
    patches and as a nullable sentinel (``stroke: StrokeStyle | None``) on
    MarkStyle. Compiled mark families (LineStyle, AreaStyle, etc.) use it as a
    required sub-block and enforce their specific sub-field requirements via
    ``@model_validator`` on the family class itself.

    None semantics per field:
      - color: line/area use the mark's own fill color; other families require it.
      - width: absent = VL default (1 px) for most compiled families; line and
        area instead fall back to chart_rendering.stroke.fallback_width /
        .stacked_fallback_width once the cascade and adaptive bake both leave
        it unset — see _apply_line_width.
      - cap/join: absent = VL default (butt/miter). Only line/arc set these.
      - dasharray: absent = solid line. Only spark.empty sets this.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    color: Annotated[str | None, Color()] = Field(
        default=None, description="Stroke color as a CSS color string."
    )
    width: float | None = Field(default=None, description="Stroke width in pixels.")
    cap: LineCap | None = Field(
        default=None,
        description="How the stroke's ends are finished (butt, round, or square).",
    )
    join: Literal["miter", "round", "bevel"] | None = Field(
        default=None,
        description="How two stroke segments are joined (miter, round, or bevel).",
    )
    dasharray: str | None = Field(
        default=None,
        description="Lengths of the dashes and the blanks between them (e.g. '4 2').",
    )


class FontColorStrokeStyle(StrokeStyle):
    """Stroke for rule marks whose color defaults to the root font color.

    Subclasses StrokeStyle to scope the Inherit marker: line/area marks also use
    StrokeStyle but must NOT inherit font.color (they inherit stroke from their own
    fill via slot expansion). Annotating StrokeStyle.color globally would override
    those per-family slot-derived links; a subclass confines the inheritance to
    rule only.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    color: Annotated[str | None, Inherit(from_path="Style.font.color"), Color()] = (
        Field(
            default=None,
            description="Stroke color for rule marks.",
        )
    )


def might_be_palette_role(value: str) -> bool:
    """True when ``value`` could still name a theme palette role.

    Deliberately shape-only: a role is whatever a theme's ``palettes:`` block
    binds, and that map is not final at validation time. What is *not* a role is
    decidable now, and must stay a hard error here rather than be deferred:

    - anything carrying ``:N``/``_r`` shorthand or a leading ``#``
    - a Vega scheme name, which is legal in a gradient but never in a stops slot
    - a perceptual anti-pattern (`jet`, `rainbow`, `hsv`), whose rejection is a
      deliberate gate that deferral would silently swallow
    """
    from dbt_charts.core.compile.resolve.style.palette import (  # noqa: PLC0415
        is_hard_fail_name,
    )

    return (
        bool(value)
        and ":" not in value
        and not value.startswith("#")
        and value not in VEGA_SCHEME_NAMES
        and not is_hard_fail_name(value)
    )


class ScaleTargetPaletteValidationMixin(BaseModel):
    """Shared authored-input validator for ``ScaleTargetConfig.palette``.

    Inherited by both ``ScaleTargetConfig`` and ``ScaleTargetConfigPatch`` so
    chart-local authored patches reject a bad palette at the compile boundary
    (where ``normalize_board()`` wraps the resulting ``pydantic.ValidationError``
    into a proper ``ERR-VALIDATION-FIELD`` diagnostic) instead of only at
    resolve time, where the raw pydantic error leaks past dbt charts' error
    formatting — ``build_patch_model_ext`` only carries over validators that
    live on the patch model's base class (mirrors ``ScaleDomainValidationMixin``).
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _validate_named_palette(cls, data: object) -> object:
        """A string palette is either a literal Vega scheme name (forwarded
        as-is to VL's ``scale.scheme`` by geo/heatmap gradient rendering) or a
        dbt charts named palette (resolved to hex stops downstream by
        ``bake_scale_target_stops``/``resolve_palette_stops``). Reject anything
        that resolves as neither.

        Deliberately does *not* bake ``resolved_stops`` here: this validator
        also runs on every intermediate ``merge_onto_base`` cascade step, so
        baking here can leave a stale value from a *previous* palette on a
        chart-local override. Baking happens explicitly, once, at each
        resolve-time construction site instead — see ``bake_scale_target_stops``
        below.

        A dash-suffixed name whose prefix is a real scheme (``"blues-9"``,
        VL's sampled-discrete shorthand, which dbt charts does not forward) gets
        a specific hint naming the continuous scheme instead of the generic
        unknown-palette message — that shape is the exact mistake this
        validator exists to catch: an author reaching for N discrete buckets
        that dbt charts doesn't support yet, not a random typo.

        ``mode="before"`` on the raw dict (not ``field_validator``) so this
        runs unchanged on both ``ScaleTargetConfig`` and the generated
        ``ScaleTargetConfigPatch`` — a field-scoped validator would need the
        field to exist on this mixin itself, which a patch base never does.
        """
        if not isinstance(data, dict):
            return data
        value = data.get("palette")
        if not isinstance(value, str) or value in VEGA_SCHEME_NAMES:
            return data
        from dbt_charts.core.compile.resolve.style.palette import (  # noqa: PLC0415
            UnknownPaletteError,
            palette as resolve_palette,
        )

        try:
            resolve_palette(value)
        except UnknownPaletteError as exc:
            base, _, suffix = value.rpartition("-")
            if base in VEGA_SCHEME_NAMES and suffix.isdigit():
                raise ValueError(
                    f"palette {value!r} requests a {suffix}-step discrete "
                    f"variant of the '{base}' scheme. dbt charts does not support "
                    "bucketed/quantized color scales yet — use the continuous "
                    f"scheme '{base}' instead."
                ) from None
            if might_be_palette_role(value):
                # A theme palette role, unresolvable until the extends chain has
                # merged. `expand_palette_refs` substitutes it the moment the
                # role map is final, and raises there if it is not one.
                return data
            raise ValueError(str(exc)) from None
        return data


class ScaleTargetConfig(ScaleTargetPaletteValidationMixin):
    """Scale configuration for a single style target (background or color).

    Base of ``ColumnScaleTargetConfig`` and the ``gradient`` field of
    ``ColorStyle`` / ``StaticGradientColorStyle``.
    Lives in primitives so both ``chart/authored.py`` and ``style/authored.py``
    can import it without creating a circular dependency.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    palette: Annotated[
        ScalePaletteName | list[str] | list[float] | str,
        Merge(Strategy.OVERRIDE),
        Palette(),
    ] = Field(
        description="Which colors the scale draws from: a built-in palette name or Vega scheme, a CSS color list for categorical, or a float list for relative stops."
    )
    domain: Literal["data"] | None = Field(
        default="data",
        description=(
            "Scale domain source ('data' uses the data extent, widened to "
            "nice round bounds when nice is true; None uses explicit min/max)."
        ),
    )
    min: float | int | None = Field(
        default=None, description="Minimum scale domain value (overrides data minimum)."
    )
    max: float | int | None = Field(
        default=None, description="Maximum scale domain value (overrides data maximum)."
    )
    nice: bool = Field(
        default=True,
        description=(
            "Widen a data-derived domain to round ('nice') bounds and label "
            "every nice tick on the legend, mirroring Vega-Lite's own "
            "scale.nice. Applies to the color scale a gradient legend "
            "labels, not the x/y position scale. Ignored once min or max is "
            "set; a single-sided bound already fixes that edge exactly. "
            "Widening is currently honored on heatmap's themed color "
            "gradient only; geoshape's choropleth honors `nice: false` "
            "(exact endpoint labels) but not `nice: true`'s widening."
        ),
    )
    null_color: Annotated[str | None, Color()] = Field(
        default=None, description="Color assigned to null values."
    )
    hinge: float | Literal["auto"] | None = Field(
        default=None,
        description="Diverging scale midpoint value, or 'auto' to use the data midpoint.",
    )
    arm_mode: Literal["asymmetric", "symmetric"] = Field(
        default="asymmetric",
        description="How diverging scale arms are stretched: 'asymmetric' (proportional) or 'symmetric' (equal arms).",
    )


class ResolvedScaleTargetConfig(ScaleTargetConfig):
    """Bake ran; palette was an inline list or a Vega scheme name — nothing
    to resolve. No resolved_stops field — this variant structurally never
    has one, so a caller cannot read a stale/absent value by mistake."""

    @model_validator(mode="after")
    def _reject_unbaked_named_palette(self) -> ResolvedScaleTargetConfig:
        # A ResolvedNamedPaletteScaleTargetConfig legitimately holds a named
        # palette string next to its baked stops.
        if not isinstance(self, ResolvedNamedPaletteScaleTargetConfig) and isinstance(
            self.palette, str
        ):
            if self.palette not in VEGA_SCHEME_NAMES:
                raise ValueError(
                    f"palette {self.palette!r} is a named dbt-charts palette and must be "
                    "baked to resolved_stops before construction — "
                    "use bake_scale_target_stops() which produces ResolvedNamedPaletteScaleTargetConfig."
                )
        return self


class ResolvedNamedPaletteScaleTargetConfig(ResolvedScaleTargetConfig):
    """Bake ran; palette was a string that was resolved to stops.

    Covers both dbt-charts named palettes (resolved via bake_scale_target_stops)
    and the WCAG-safe table column path (_with_resolved_scale_stops). The
    resolved_stops field carries the baked hex stops.
    """

    resolved_stops: tuple[str, ...] = Field(
        description=(
            "Hex stops baked once at resolve-time. Structurally required — only "
            "this variant has the field, so callers cannot read a stale/absent "
            "value on the wrong type."
        ),
    )


# Union alias: every pydantic field that HOLDS a resolved scale target must use
# this, not the bare ResolvedScaleTargetConfig. Pydantic serializes to the
# declared field type; a ResolvedNamedPaletteScaleTargetConfig stored in a
# ResolvedScaleTargetConfig-annotated field silently drops resolved_stops on
# dump and fails extra_forbidden on reload. This union lets pydantic's smart
# union pick the correct member in both directions.
ResolvedScaleTarget = ResolvedNamedPaletteScaleTargetConfig | ResolvedScaleTargetConfig


def bake_scale_target_stops(target: ScaleTargetConfig) -> ResolvedScaleTargetConfig:
    """Construct the correct resolved subtype for a scale target.

    Named dbt charts palette → ResolvedNamedPaletteScaleTargetConfig with fresh
    resolved_stops. Vega scheme name or inline stop list → plain
    ResolvedScaleTargetConfig (no resolved_stops — structurally absent, not None).

    Called explicitly at each resolve-time construction site (chart channels
    via `compile/resolve/chart/channel.py::_parse_channel_scale`, heatmap/geo theme-cascade
    gradients) — never from `ScaleTargetConfig`'s own validator, which also
    runs on every intermediate `merge_onto_base` cascade step and would bake
    a stale value there before the final palette is known.
    `_with_resolved_scale_stops` (compile/resolve/chart/_table.py) is the WCAG
    `surface="table"` analog for table column scales.
    """
    if not isinstance(target.palette, str) or target.palette in VEGA_SCHEME_NAMES:
        return ResolvedScaleTargetConfig.model_validate(
            target.model_dump(exclude_unset=True)
        )
    from dbt_charts.core.compile.resolve.style.palette import (
        palette as resolve_palette,
    )  # noqa: PLC0415

    return ResolvedNamedPaletteScaleTargetConfig.model_validate(
        {
            **target.model_dump(exclude_unset=True),
            "resolved_stops": tuple(resolve_palette(target.palette)),
        }
    )


# Patch type for ScaleTargetConfig — generated (and registered) here, right after
# the compiled class, so every later recursive build_patch_model_ext call that
# encounters ScaleTargetConfig as a nested field (StaticGradientColorStyle.gradient)
# reuses this exact registered class instead of a vanilla auto-generated one with
# no palette validation.
#
# resolved_stops is absent from ScaleTargetConfig itself (it lives only on the
# resolved subtypes), so ScaleTargetConfigPatch correctly excludes it by
# construction — no explicit exclude= needed.
if TYPE_CHECKING:

    class ScaleTargetConfigPatch(ScaleTargetConfig):
        pass

else:

    class _ScaleTargetConfigPatchBase(_PatchBase, ScaleTargetPaletteValidationMixin):
        """Base for ScaleTargetConfigPatch: extra="forbid" from _PatchBase plus
        the palette validation from ScaleTargetPaletteValidationMixin."""

    ScaleTargetConfigPatch = register_patch(ScaleTargetConfig)(
        build_patch_model_ext(
            ScaleTargetConfig,
            base_cls=_ScaleTargetConfigPatchBase,
        )
    )


class CategoricalColorStyle(BaseModel):
    """Categorical palette config: per-series colors and single-series ink list."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # The StopsPaletteName arm on palette/single_series_palette is schema-facing
    # only — _expand_palette_names below eagerly resolves a named palette to
    # its list[str] stops during validation, so the name itself never
    # survives onto a compiled instance. Stops-only, not every shipped name:
    # a tone resolves to no stops, so offering one is an authored board that
    # parses and dies at resolve.
    palette: Annotated[
        StopsPaletteName | list[str] | str | None, Merge(Strategy.OVERRIDE), Palette()
    ] = Field(
        default=None,
        description="Categorical color palette: list of stops or a named palette. Expanded to list[str] at validation time.",
    )
    # Empty would break the spark cascade and the single-series render path.
    single_series_palette: Annotated[
        StopsPaletteName | Annotated[list[str], Field(min_length=1)] | str | None,
        Merge(Strategy.OVERRIDE),
        Palette(),
        one_error(
            "single_series_palette must be a palette name or a non-empty list of "
            "colors, got {value}"
        ),
    ] = Field(
        default=None,
        description="Ordered list of single-series mark inks (must be non-empty when set), or a palette name.",
    )
    requested_alias_palette: Annotated[str | None, Merge(Strategy.OVERRIDE)] = Field(
        default=None,
        exclude=True,
        json_schema_extra={"internal": True},
        description=(
            "Internal: set when this layer authors palette/single_series_palette "
            "as a known WARN-PALETTE-UNSUPPORTED anti-pattern name (e.g. RdYlGn); "
            "holds the originally-requested name for the render-stage detector. "
            "Computed by _expand_palette_names — not user-authorable."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _expand_palette_names(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        from dbt_charts.core.compile.resolve.style.palette import (  # noqa: PLC0415
            is_warn_alias,
            resolve_palette_alias,
        )

        # The merge cascade re-validates an already-compiled model through a
        # dict of every field, provenance included — so the key's presence
        # can't be the tell. Only a value this resolver would never produce is
        # one an author fabricated.
        authored = data.get("requested_alias_palette")
        if authored is not None and not is_warn_alias(authored):
            raise ValueError(
                "requested_alias_palette is computed internally from an aliased "
                "palette/single_series_palette name and cannot be authored directly."
            )

        from dbt_charts.core.compile.resolve.style.palette import (  # noqa: PLC0415
            UnknownPaletteError,
        )

        data = dict(data)
        for field_name in ("palette", "single_series_palette"):
            value = data.get(field_name)
            if not isinstance(value, str):
                continue
            try:
                stops, requested = resolve_palette_alias(value)
            except UnknownPaletteError:
                if not might_be_palette_role(value):
                    raise
                # Possibly a theme palette role (`category`). Roles are not
                # knowable here: the theme's `palettes:` map is not final until
                # its whole extends chain has merged. `expand_palette_refs`
                # runs the moment it is, substitutes the role, and raises on a
                # name that is neither. Nothing downstream sees a bare name.
                continue
            data[field_name] = stops
            if requested is not None:
                data["requested_alias_palette"] = requested
        return data


class StaticGradientColorStyle(BaseModel):
    """Color config for geo/point_map/table families, no categorical arm.

    Authoring ``categorical`` on these families raises a pydantic.ValidationError
    (extra_forbidden). Use ``ColorStyle`` for cartesian/pie families.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    static: Annotated[str | None, Color()] = Field(
        default=None,
        description="One explicit color: the marks on most families when no color column is encoded, the cell text on a table.",
    )
    gradient: ScaleTargetConfig | None = Field(
        default=None,
        description="Continuous gradient scale for the color encoding.",
    )


class ColorStyle(StaticGradientColorStyle):
    """Unified chart color config: static paint, categorical palette, gradient scale.

    Cartesian and radial families use this type; ``categorical`` carries the
    series color palette. Geo/table families use the narrower
    ``StaticGradientColorStyle`` (no ``categorical`` arm).
    """

    categorical: CategoricalColorStyle | None = Field(
        default=None,
        description="Palettes used when color encodes distinct categories, or a lone series.",
    )
