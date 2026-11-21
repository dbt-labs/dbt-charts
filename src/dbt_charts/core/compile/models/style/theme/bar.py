"""Theme-stage style classes: bar chart family."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.conditional_predicate import (
    default_rule_last,
)
from dbt_charts.core.compile.models.factories import build_conditional_model
from dbt_charts.core.compile.models.markers import (
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
    reveal: Literal["bar", "chart", "none"] = Field(
        description=(
            "What hover redraws at true length: the hovered bar, every torn bar, or"
            " nothing, with the title painted over the torn bars for a static screen."
        )
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
            "(2 series → partial, 3+ → none), 'none' (small gap), 'flush' "
            "(bars touch), 'partial' (25% overlap), 'full' (bars coincide; an error "
            "unless each category holds one color). Or a number as a fraction "
            "of bar width: >0 overlaps, 0 touches, <0 gaps; 1 or more is 'full'. "
            "None "
            "uses the renderer default ('auto'). Only applies to grouped bars; "
            "setting it together with an active stack mode is an error."
        ),
    )
    # None is the unset sentinel: render ranks by value, and no theme sets it.
    stack_order: Literal["value", "data", "alphabetical"] | None = Field(
        default=None,
        description=(
            "Z-order of stacked segments. None/'value' puts the largest aggregate at baseline. "
            "'data' stacks series in first-appearance order: SQL row order for color:, "
            "the listed order for y: [a, b, ...] (a measure null in the first rows "
            "appears later). "
            "'alphabetical' sorts by series name. Ignored when stacking is off or "
            "there is only one series."
        ),
    )
    # Stacked only: resolve turns the rail back off for a grouped bar, whose
    # series all rise from the same baseline and would collide.
    grouped: BarGroupedStyle = Field(description="Band-scale padding for grouped bars.")
    outliers: BarOutliersStyle = Field(
        description="Tear bars far longer than the rest."
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
