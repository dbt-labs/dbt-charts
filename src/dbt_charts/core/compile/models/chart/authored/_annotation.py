"""Authored chart annotations: rules, bands, notes and trends.

An anchor is a literal in the axis column's type, a range, or a call from a
closed function set (`avg()`, `percentile(25)`, `now()`, `current(month)`),
parsed here into a typed model. What a literal means (a date, a number, a
category) depends on the axis it lands on, which resolve knows.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
)

from dbt_charts.core.compile.models.constraints import (
    AtLeastOneOf,
    ConstrainedModel,
    Constraint,
    Forbids,
    IsCall,
    IsSet,
    Requires,
    ValueIn,
)
from dbt_charts.core.compile.models.style.authored._base import (
    BandAnnotationStylePatch,
    NoteAnnotationStylePatch,
    RuleAnnotationStylePatch,
    TrendAnnotationStylePatch,
)

_CALL = re.compile(r"^\s*([A-Za-z_]+)\s*\((.*)\)\s*$")
_ANCHOR_FUNCTIONS = (
    "min(), max(), avg(), median(), percentile(<0-100>), now(), "
    "current(day | week | month | quarter | year)"
)
ClockUnit = Literal["day", "week", "month", "quarter", "year"]
_CLOCK_UNITS: tuple[ClockUnit, ...] = ("day", "week", "month", "quarter", "year")


class AggregateAnchor(ConstrainedModel):
    """A value computed over the plotted rows of the axis column."""

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

    fn: Literal["min", "max", "avg", "median", "percentile"] = Field(
        description="Which aggregate."
    )
    percent: float | None = Field(
        default=None, ge=0, le=100, description="The percentile, 0 to 100."
    )


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


class FitFunction(ConstrainedModel):
    """The function a trend fits to the plotted rows."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    __constraints__: ClassVar[tuple[Constraint, ...]] = (
        Requires(
            ("order",),
            "A polynomial fit takes an order.",
            when=(ValueIn("kind", ("polynomial",)),),
        ),
        Forbids(
            ("order",),
            "Only a polynomial fit takes an order.",
            when=(ValueIn("kind", ("polynomial",), negate=True),),
        ),
    )

    kind: Literal["linear", "polynomial", "exponential", "log", "power"] = Field(
        description="Shape of the fitted curve."
    )
    order: int | None = Field(
        default=None, ge=2, description="Degree of a polynomial fit."
    )


class IntervalFunction(BaseModel):
    """The band drawn around a trend."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    kind: Literal["confidence", "prediction"] = Field(
        description="Interval of the fitted mean, or of a new observation."
    )
    percent: float = Field(gt=0, lt=100, description="Coverage, in percent.")


def _call(text: str) -> tuple[str, str] | None:
    match = _CALL.match(text)
    return (match.group(1), match.group(2).strip()) if match else None


def _number(arg: str, what: str) -> float:
    try:
        return float(arg)
    except ValueError:
        raise ValueError(f"{what} takes a number, got {arg!r}") from None


def _parse_anchor_function(
    value: object,  # type-state: object_annotation — raw YAML
) -> object:  # type-state: object_annotation — the field validates it
    """A function call string becomes its typed anchor; anything else passes."""
    if not isinstance(value, str) or (call := _call(value)) is None:
        return value
    name, arg = call
    if name in ("min", "max", "avg", "median", "now") and not arg:
        return {"fn": name}
    if name == "percentile" and arg:
        percent = _number(arg, "percentile()")
        if not 0 <= percent <= 100:
            raise ValueError(f"function percentile() takes 0 to 100, got {arg}")
        return {"fn": name, "percent": percent}
    if name == "current" and arg in _CLOCK_UNITS:
        return {"fn": name, "unit": arg}
    raise ValueError(
        f"function {value.strip()!r} is not an anchor function; anchors take "
        f"{_ANCHOR_FUNCTIONS}, with no nesting or arithmetic"
    )


def _parse_fit(
    value: object,  # type-state: object_annotation — raw YAML
) -> object:  # type-state: object_annotation — the field validates it
    if not isinstance(value, str):
        return value
    call = _call(value)
    if call is not None:
        name, arg = call
        if name in ("linear", "exponential", "log", "power") and not arg:
            return {"kind": name}
        if name == "polynomial" and arg.isdigit() and int(arg) >= 2:
            return {"kind": name, "order": int(arg)}
    raise ValueError(
        f"fit {value!r} is not a fit function; use linear(), polynomial(<order 2 "
        "or more>), exponential(), log() or power()"
    )


def _parse_interval(
    value: object,  # type-state: object_annotation — raw YAML
) -> object:  # type-state: object_annotation — the field validates it
    if not isinstance(value, str):
        return value
    call = _call(value)
    if call is not None and call[0] in ("confidence", "prediction") and call[1]:
        percent = _number(call[1], f"{call[0]}()")
        if 0 < percent < 100:
            return {"kind": call[0], "percent": percent}
    raise ValueError(
        f"interval {value!r} is not an interval function; use confidence(<pct>) "
        "or prediction(<pct>), with pct between 0 and 100"
    )


_SCALAR_INPUT = float | int | date | str

AnchorScalar = Annotated[
    float | datetime | date | str | AggregateAnchor | ClockAnchor,
    BeforeValidator(_parse_anchor_function, json_schema_input_type=_SCALAR_INPUT),
]


class AnchorRange(ConstrainedModel):
    """A span on one axis; a missing bound runs to the axis edge."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, serialize_by_alias=True, allow_inf_nan=False
    )

    __constraints__: ClassVar[tuple[Constraint, ...]] = (
        AtLeastOneOf(("from", "to"), "A range needs from or to."),
    )

    from_: AnchorScalar | None = Field(
        default=None, alias="from", description="Where the span starts."
    )
    to: AnchorScalar | None = Field(default=None, description="Where the span ends.")


Anchor = AnchorScalar | AnchorRange


def _single_value(
    value: Any,  # type-state: explicit_any — raw authored input
) -> Any:  # type-state: explicit_any — passed through to the anchor union
    if isinstance(value, AnchorRange) or (
        isinstance(value, dict) and value.keys() & {"from", "to"}
    ):
        raise ValueError("a note takes a single value, not a range")
    return value


NoteAnchor = Annotated[
    AnchorScalar,
    BeforeValidator(_single_value, json_schema_input_type=_SCALAR_INPUT),
]

AnnotationText = Annotated[
    str, StringConstraints(min_length=1, pattern=r"^[^\n\r]*\S[^\n\r]*$")
]


class _AnnotationBase(ConstrainedModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    text: AnnotationText | None = Field(
        default=None,
        description="Text drawn with the annotation; `{{ value | format }}` prints the anchor.",
    )
    plane: Literal["background", "foreground"] | None = Field(
        default=None, description="Draw below the data marks, or above them."
    )
    series: Literal["each", "total"] | list[str] | None = Field(
        default=None,
        description=(
            "Which series a computed anchor or trend is computed for. `total` "
            "pools every series' rows, or takes the per-x sum on a stacked chart."
        ),
    )
    y_column: Annotated[str, StringConstraints(min_length=1)] | None = Field(
        default=None,
        description=(
            "The y column the annotation refers to: it picks the y axis that "
            "plots the column, and the rows a computed y anchor or trend "
            "reads. Required for a computed y anchor, trend or derived note on "
            "a chart with several y columns or `layers:`."
        ),
    )


_ONE_ANCHOR = AtLeastOneOf(("x", "y"), "A {type} needs an x or y anchor.")


class RuleAnnotation(_AnnotationBase):
    """A line across the plot at one value, or a segment of it."""

    __constraints__: ClassVar[tuple[Constraint, ...]] = (_ONE_ANCHOR,)

    type: Literal["rule"] = Field(description="Selects the annotation.")
    x: Anchor | None = Field(default=None, description="Where on the x axis.")
    y: Anchor | None = Field(default=None, description="Where on the y axis.")
    style: RuleAnnotationStylePatch | None = Field(
        default=None, description="Appearance overrides for this annotation's marks."
    )


class BandAnnotation(_AnnotationBase):
    """A shaded region over a range or a category."""

    __constraints__: ClassVar[tuple[Constraint, ...]] = (_ONE_ANCHOR,)

    type: Literal["band"] = Field(description="Selects the annotation.")
    x: Anchor | None = Field(
        default=None, description="Span or category on the x axis."
    )
    y: Anchor | None = Field(default=None, description="Span on the y axis.")
    style: BandAnnotationStylePatch | None = Field(
        default=None, description="Appearance overrides for this annotation's marks."
    )


_BOTH_OR_NEITHER = (
    "A note takes both x and y, or neither; only min() and max() can derive "
    "the other coordinate."
)


_POINT_NOTE_NO_WRAP = (
    "style.marks.text.max_width wraps only a note with no anchor or a trend; a "
    "note anchored at a point does not wrap."
)


class NoteAnnotation(_AnnotationBase):
    """Text at a point, or pinned to the plot frame when it has no anchor.

    With `min()` or `max()` on one axis and the other omitted, the note sits at
    the row holding that extreme.
    """

    __constraints__: ClassVar[tuple[Constraint, ...]] = (
        Requires(
            ("y",),
            _BOTH_OR_NEITHER,
            when=(IsSet("x"), IsCall("x", ("min", "max"), negate=True)),
        ),
        Requires(
            ("x",),
            _BOTH_OR_NEITHER,
            when=(IsSet("y"), IsCall("y", ("min", "max"), negate=True)),
        ),
        Forbids(
            ("enclose",),
            "A note with no anchor takes no enclose.",
            when=(ValueIn("x", (None,)), ValueIn("y", (None,))),
        ),
        Forbids(
            ("connector",),
            "A note with no anchor takes no connector.",
            when=(
                ValueIn("x", (None,)),
                ValueIn("y", (None,)),
                ValueIn("connector", (None,), negate=True),
            ),
        ),
        Forbids(
            ("style.marks.text.position",),
            "A note boxed with enclose: rect is centered on its anchor, so "
            "style.marks.text.position does not apply; add a connector to place "
            "the box.",
            when=(ValueIn("enclose", ("rect",)), ValueIn("connector", (None,))),
        ),
        Forbids(
            ("style.marks.text.max_width",),
            _POINT_NOTE_NO_WRAP,
            when=(IsSet("x"),),
        ),
        Forbids(
            ("style.marks.text.max_width",),
            _POINT_NOTE_NO_WRAP,
            when=(IsSet("y"),),
        ),
    )

    type: Literal["note"] = Field(description="Selects the annotation.")
    x: NoteAnchor | None = Field(
        default=None,
        description=(
            "Where on the x axis. `min()` or `max()` with y omitted anchors at "
            "the row holding that x extreme."
        ),
    )
    y: NoteAnchor | None = Field(
        default=None,
        description=(
            "Where on the y axis. `min()` or `max()` with x omitted anchors at "
            "the row holding that y extreme."
        ),
    )
    connector: Literal["line", "arrow"] | None = Field(
        default=None,
        description=(
            "Draws a line, or a line ending in an arrowhead, from the anchor to"
            " text moved away with dx/dy. Unset draws none."
        ),
    )
    enclose: Literal["circle", "rect"] | None = Field(
        default=None, description="Shape drawn around the anchor."
    )
    style: NoteAnnotationStylePatch | None = Field(
        default=None, description="Appearance overrides for this annotation's marks."
    )


class TrendAnnotation(_AnnotationBase):
    """A curve fitted to the plotted rows, with an optional interval."""

    type: Literal["trend"] = Field(description="Selects the annotation.")
    fit: Annotated[
        FitFunction, BeforeValidator(_parse_fit, json_schema_input_type=str)
    ] = Field(description="The fitted function, such as linear() or polynomial(2).")
    interval: Annotated[
        IntervalFunction | None,
        BeforeValidator(_parse_interval, json_schema_input_type=str | None),
    ] = Field(
        default=None, description="Band around the trend, such as confidence(95)."
    )
    x: AnchorRange | None = Field(
        default=None,
        description="Span of x the trend is drawn over; defaults to the data.",
    )
    style: TrendAnnotationStylePatch | None = Field(
        default=None, description="Appearance overrides for this annotation's marks."
    )


Annotation = Annotated[
    RuleAnnotation | BandAnnotation | NoteAnnotation | TrendAnnotation,
    Field(discriminator="type"),
]
