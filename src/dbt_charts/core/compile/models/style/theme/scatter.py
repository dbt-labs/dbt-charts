"""Theme-stage style classes: scatter chart family."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

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
from dbt_charts.core.compile.models.primitives import SizeScaleStyle
from dbt_charts.core.compile.models.style.theme._chart_base import (
    _AnnotatedChartStyleMixin,
    _CartesianChartStyle,
    _LegendKeyedChartStyleBaseAllOptional,
    _QuantitativeAxisChartStyleMixin,
)
from dbt_charts.core.compile.models.style.theme.axis_conditional import (
    AxisConditionalPredicate,
)
from dbt_charts.core.compile.models.style.theme.marks import (
    PointMarkStyle,
    RingedPointMarkStyle,
    TextMarkStyle,
)

ScatterSeriesSymbol = Literal[
    "hollow_circle",
    "plus",
    "open_triangle",
    "cross",
    "diamond",
    "tripod",
    "square",
    "asterisk",
]


def _distinct_symbols(
    symbols: list[ScatterSeriesSymbol] | None,
) -> list[ScatterSeriesSymbol] | None:
    if symbols is not None and len(set(symbols)) != len(symbols):
        repeated = sorted({s for s in symbols if symbols.count(s) > 1})
        raise ValueError(
            f"series_symbols repeats {repeated}; each series needs its own symbol"
        )
    return symbols


class ScatterChartMarksStyle(BaseModel):
    """Scatter-family mark overrides."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    point: RingedPointMarkStyle = Field(
        default_factory=RingedPointMarkStyle,
        description="Point mark overrides; inherits from global.",
    )
    text: TextMarkStyle = Field(
        default_factory=TextMarkStyle,
        description="Text mark overrides; inherits from global.",
    )


class ScatterLayerMarksStyle(BaseModel):
    """Scatter-layer mark overrides: plain points, which carry no halo."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    point: PointMarkStyle = Field(
        default_factory=PointMarkStyle,
        description="Point mark overrides; inherits from the scatter family.",
    )
    text: TextMarkStyle = Field(
        default_factory=TextMarkStyle,
        description="Text mark overrides; inherits from the scatter family.",
    )


class ScatterLayerStyle(BaseModel):
    """Required wrapper for scatter-layer mark overrides (built into a Patch by build_patch_model)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    marks: ScatterLayerMarksStyle = Field(
        description="Mark overrides for this scatter layer."
    )


class _ScatterChartStyleBase(
    _CartesianChartStyle,
    _LegendKeyedChartStyleBaseAllOptional,
    _QuantitativeAxisChartStyleMixin,
    _AnnotatedChartStyleMixin,
):
    """Scatter chart style: chart-level fields + marks sub-block."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    marks: Annotated[
        ScatterChartMarksStyle, InheritSlot(from_path="Style.charts.marks")
    ] = Field(
        default_factory=ScatterChartMarksStyle,
        description="Scatter-family mark overrides.",
    )
    size: SizeScaleStyle = Field(
        description="Area range and scale type of the `size:` channel.",
    )
    frame: Literal["full", "range"] = Field(
        description="How the axes draw: in full, or as a range frame over the data.",
    )
    series_symbols: Annotated[
        list[ScatterSeriesSymbol] | None,
        Absent("the point mark's own shape"),
        AfterValidator(_distinct_symbols),
    ] = Field(
        min_length=1,
        default=None,
        description="Each series' symbol, in series order, under a range frame.",
    )


if TYPE_CHECKING:

    class ScatterConditionalRule(AxisConditionalPredicate):
        marks: ScatterChartMarksStyle | None

else:
    ScatterConditionalRule = build_conditional_model(
        _ScatterChartStyleBase,
        AxisConditionalPredicate,
        name="ScatterChartStyleConditional",
    )


class ScatterChartStyle(_ScatterChartStyleBase):
    """Scatter chart style, plus the per-point rules only a chart's own style takes."""

    conditional: Annotated[
        dict[
            str,
            Annotated[
                list[ScatterConditionalRule],
                Field(min_length=1),
                AfterValidator(default_rule_last),
            ],
        ]
        | None,
        ChartLocal(),
    ] = Field(
        default=None,
        description=(
            "Rules that restyle a point by its row's value, keyed by column name. "
            "Each rule is one condition (eq, ne, lt, lte, gt, gte, between, in, "
            "is_null, or default) plus the style it applies: marks.point color, "
            "fill, filled, size, shape, opacity and stroke_width, and marks.text "
            "font color and weight. A rule size or shape overrides a `size:` or "
            "`shape:` channel for the points it matches; the legend is unchanged. "
            "Every matching rule applies; where two set the same key the later one "
            "wins, so list the more specific rule last. A `default: true` rule "
            "applies only when no other rule matched. Two columns cannot patch "
            "the same property. Rules style the base layer, not `layers:`."
        ),
    )
