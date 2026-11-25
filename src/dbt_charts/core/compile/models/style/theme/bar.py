"""Theme-stage style classes: bar chart family."""

from __future__ import annotations

import math
from functools import partial
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
)

from dbt_charts.core.compile.models.conditional_predicate import (
    default_rule_last,
)
from dbt_charts.core.compile.models.factories import build_conditional_model
from dbt_charts.core.compile.models.markers import (
    Absent,
    ChartLocal,
    InheritSlot,
    SkipInheritSlots,  # noqa: F401  # pyright: ignore[reportUnusedImport] — required in module globals for model_rebuild() to resolve inherited base-class annotations
)
from dbt_charts.core.compile.models.primitives import (
    OverlapSpec,
    PatternStyle,
)
from dbt_charts.core.compile.models.style.theme._chart_base import (
    _AnnotatedChartStyleMixin,
    _BarKeyedChartStyleBaseAllOptional,
    _CartesianChartStyle,
    _QuantitativeAxisChartStyleMixin,
)
from dbt_charts.core.compile.models.style.theme.axis_conditional import (
    AxisConditionalPredicate,
)
from dbt_charts.core.compile.models.style.theme.marks import (
    BarMarkStyle,
    TextMarkStyle,
)


class BarChartMarksStyle(BaseModel):
    """Bar-family mark overrides. Only bar and text (endpoint labels) are valid."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bar: BarMarkStyle = Field(
        default_factory=BarMarkStyle,
        description="Bar mark overrides for bar charts; inherits from global.",
    )
    text: TextMarkStyle = Field(
        default_factory=TextMarkStyle,
        description="Text mark overrides for bar endpoint labels; inherits from global.",
    )


class BarGroupedStyle(BaseModel):
    """Band-scale padding for grouped (side-by-side) bars."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    padding_inner: float = Field(
        ge=0, lt=1, description="Gap between groups, as a share of the band step."
    )
    padding_outer: float = Field(
        ge=0,
        lt=1,
        description="Margin at both plot edges, as a share of the band step.",
    )


def _read_cap(
    value: Any,  # type-state: explicit_any — validator input is whatever the author wrote
    *,
    allow_none: bool,
) -> float | str:
    """A cap as pixels (`64`, `"64"`, `"64px"`) or a percent string (`"33%"`); `"none"` where `allow_none`."""
    if allow_none and value == "none":
        return value
    percent = isinstance(value, str) and value.strip().endswith("%")
    digits = value
    if isinstance(value, str):
        digits = value.strip().removesuffix("%" if percent else "px")
    try:
        number = float(digits)
    except (TypeError, ValueError):
        number = math.nan
    if isinstance(value, bool) or not math.isfinite(number):
        suffix = ", or 'none'" if allow_none else ""
        raise ValueError(
            f"{value!r} is not a valid cap (use pixels such as 64 or '64px', "
            f"or a percent such as '33%'{suffix})"
        )
    if number <= 0:
        raise ValueError(f"{value!r} must be positive")
    return f"{str(digits).strip()}%" if percent else number


_PERCENT = r"\d+(\.\d+)?%"
HorizontalCap = Annotated[
    float | Annotated[str, StringConstraints(pattern=f"^{_PERCENT}$")],
    BeforeValidator(partial(_read_cap, allow_none=False)),
]
VerticalCap = Annotated[
    float | Annotated[str, StringConstraints(pattern=f"^({_PERCENT}|none)$")],
    BeforeValidator(partial(_read_cap, allow_none=True)),
]


class BarMaxThicknessStyle(BaseModel):
    """Thickest a category bar or column may be: the plot shrinks along the category axis until a bar is the cap."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    horizontal: HorizontalCap = Field(
        description=(
            "Thickest a horizontal bar may be: pixels, or a percent of the plot's width such as '20%'."
        )
    )
    vertical: VerticalCap = Field(
        description=(
            "Widest a column may be: pixels, or a percent of the plot's height such as '33%'; "
            "'none' leaves columns uncapped."
        )
    )


class BarLabelsAboveStyle(BaseModel):
    """Moves a horizontal bar chart's category names above their bars when the names beside the bars take too much of the chart."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_share: float = Field(
        gt=0,
        le=1,
        description=(
            "The largest share of the chart's width the category names may take "
            "beside the bars, as a fraction from above 0 up to 1. A single-series "
            "or stacked horizontal bar chart whose names would take more puts each "
            "name on one line above its bar instead, and a name too long for that "
            "line ends in an ellipsis."
        ),
    )


class BarOutliersCapStyle(BaseModel):
    """Size of the floating cap that stands in for a torn bar's end."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    band: float = Field(
        gt=0,
        le=1,
        description="Cap band length above the tear, as a share of the plot.",
    )
    length: float = Field(
        gt=0, description="Cap length, as a multiple of bar thickness."
    )


class BarOutliersStyle(BaseModel):
    """Tear bars far longer than the rest so they do not shrink the others.

    A bar is torn when its length is at least `median_multiplier` times the
    median bar length. An `axis_y` domain maximum below a bar tears it
    whatever the multiplier says.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    median_multiplier: Annotated[float, Field(gt=1)] | Literal["none"] = Field(
        description="Tear bars at least this many times the median bar; none turns it off."
    )
    variant: Literal["torn", "torn_with_cap"] = Field(
        description="How a torn bar is drawn: past the top of the card, or cut with a floating cap."
    )
    reveal: Literal["bar", "chart"] = Field(
        description="What hover redraws at true length: the hovered bar or every torn bar."
    )
    cap: BarOutliersCapStyle = Field(
        description="Cap size, read only by the torn_with_cap variant."
    )


class BarLayerStyle(BaseModel):
    """Required wrapper for bar-layer mark overrides (built into a Patch by build_patch_model)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    marks: BarChartMarksStyle = Field(description="Mark overrides for this bar layer.")


class _BarChartStyleBase(
    _CartesianChartStyle,
    _BarKeyedChartStyleBaseAllOptional,
    _QuantitativeAxisChartStyleMixin,
    _AnnotatedChartStyleMixin,
):
    """Bar chart style: chart-level fields + marks sub-block."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Cascade-managed sentinels — None means "not overridden at this tier".
    orientation: Literal["horizontal", "vertical", "auto"] | None = Field(
        default=None,
        description=(
            "Preferred bar orientation; None behaves like 'auto', which picks "
            "horizontal for a categorical x and vertical for a continuous one "
            "(temporal, quantitative, or date-like). Never remaps x/y."
        ),
    )
    # Stack default for the bar family. ``"none"`` renders grouped (side-by-side)
    # columns when a color channel is present; other modes are passed through to VL.
    # Used when a chart doesn't author ``chart.style.stack`` explicitly.
    stack: Literal["none", "zero", "normalize", "center"] = Field(
        description="Default stack mode for bar charts; none renders side-by-side columns.",
    )
    # Cascade-managed sentinel: None means "not overridden at this tier" and the
    # renderer falls back to 'auto'. Within-group spacing for grouped bars.
    overlap: OverlapSpec = Field(
        default=None,
        description=(
            "Within-group spacing for grouped bars. Keywords: 'auto' "
            "(2 series where one is clearly the bigger → partial, the bigger "
            "series behind; otherwise none), 'none' (small gap), 'flush' "
            "(bars touch), 'partial' (25% overlap), 'full' (bars coincide; an error "
            "unless each category holds one color). Or a number as a fraction "
            "of bar width: >0 overlaps, 0 touches, <0 gaps; 1 or more is 'full'. "
            "None "
            "uses the renderer default ('auto'). Only applies to grouped bars; "
            "setting it together with an active stack mode is an error."
        ),
    )
    # None is the unset sentinel: render ranks by value, and no theme sets it.
    series_order: Literal["value", "data", "alphabetical"] | None = Field(
        default=None,
        description=(
            "Order of the series in a stack or group. Unset or 'value' puts the "
            "largest aggregate first: at the baseline when stacked, leftmost "
            "(topmost when horizontal) when grouped. "
            "'data' orders series by first appearance: SQL row order for color:, "
            "the listed order for y: [a, b, ...] (a measure null in the first rows "
            "appears later). "
            "'alphabetical' sorts by series name. Ignored when there is only one series."
        ),
    )
    # Stacked only: resolve turns the rail back off for a grouped bar, whose
    # series all rise from the same baseline and would collide.
    grouped: BarGroupedStyle = Field(description="Band-scale padding for grouped bars.")
    outliers: BarOutliersStyle = Field(
        description="Tear bars far longer than the rest."
    )
    max_thickness: Annotated[
        BarMaxThicknessStyle | None, Absent("category bars are uncapped")
    ] = Field(
        default=None,
        description="Thickest a category bar or column may be; unset leaves them uncapped.",
    )
    labels_above: Annotated[
        BarLabelsAboveStyle | None,
        Absent("category names stay beside horizontal bars"),
    ] = Field(
        default=None,
        description="Moves long horizontal bar names above their bars; unset keeps them beside the bars.",
    )
    pattern: PatternStyle = Field(
        description="How the `pattern:` channel paints fills."
    )
    marks: Annotated[
        BarChartMarksStyle, InheritSlot(from_path="Style.charts.marks")
    ] = Field(
        default_factory=BarChartMarksStyle,
        description="Bar-family mark overrides.",
    )


if TYPE_CHECKING:

    class BarConditionalRule(AxisConditionalPredicate):
        marks: BarChartMarksStyle | None

else:
    BarConditionalRule = build_conditional_model(
        _BarChartStyleBase,
        AxisConditionalPredicate,
        name="BarChartStyleConditional",
    )


class BarChartStyle(_BarChartStyleBase):
    """Bar chart style, plus the per-bar rules only a chart's own style takes."""

    conditional: Annotated[
        dict[
            str,
            Annotated[
                list[BarConditionalRule],
                Field(min_length=1),
                AfterValidator(default_rule_last),
            ],
        ]
        | None,
        ChartLocal(),
    ] = Field(
        default=None,
        description=(
            "Rules that restyle a bar by its row's value, keyed by column name. "
            "Each rule is one condition (eq, ne, lt, lte, gt, gte, between, in, "
            "is_null, or default) plus the style it applies: marks.bar color, "
            "opacity and border, and marks.text font color and weight. Every "
            "matching rule applies; where two set the same key the later one "
            "wins, so list the more specific rule last. A `default: true` rule "
            "applies only when no other rule matched. Two columns cannot patch "
            "the same property. Rules style the base layer, not `layers:`."
        ),
    )
