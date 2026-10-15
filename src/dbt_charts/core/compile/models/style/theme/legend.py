"""Theme-stage style classes: legend."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.markers import (
    Absent,
    InheritSlot,
)
from dbt_charts.core.compile.models.primitives import (
    FontStyle,
)

# Where a legend sits relative to the plot. `edge` is the side it hugs,
# `align` its position along that edge, `overlay` whether it floats over the
# marks (reserving nothing) or takes a strip beside the plot.
LegendEdge = Literal["left", "right", "top", "bottom"]
LegendAlign = Literal["start", "center", "end"]
# Vega-Lite legend direction values (encoding.color.legend.direction).
LegendDirection = Literal["horizontal", "vertical"]


class LegendElementStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    font: Annotated[FontStyle, InheritSlot(from_path="Style.charts.font")] = Field(
        default_factory=FontStyle,
        description="Legend element font style overrides.",
    )
    padding: float = Field(
        description="Padding between legend symbol and element text in pixels."
    )


class LegendLabelStyle(LegendElementStyle):
    max_width: float | None = Field(
        default=None,
        description="Maximum label width in pixels; maps to VL labelLimit. None uses Vega-Lite's default.",
    )


class LegendTitleStyle(LegendElementStyle):
    visible: bool | Literal["auto"] | None = Field(
        default=None,
        description=(
            "Show the legend title. 'auto' shows it except on a top legend "
            "that runs horizontally; false suppresses it (VL legend.title: null)."
        ),
    )


class LegendPositionStyle(BaseModel):
    """Cartesian and geo legend placement: edge, align along it, overlay or reserve.

    Every leaf is `auto` on every built-in theme: the engine decides
    (``resolve/style/legend_position.py``) and bakes the concrete placement
    into the resolved legend. A concrete leaf from any tier is placed as written.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    edge: LegendEdge | Literal["auto"] | None = Field(
        default=None,
        description="Side of the plot the legend sits on. 'auto' = the engine decides.",
    )
    align: LegendAlign | Literal["auto"] | None = Field(
        default=None,
        description="Position along the edge. 'auto' = the engine decides.",
    )
    overlay: bool | Literal["auto"] | None = Field(
        default=None,
        description=(
            "True floats the legend over the plot, reserving no space; False "
            "reserves a strip for it. 'auto' = the engine decides."
        ),
    )


class PiePositionStyle(BaseModel):
    """Pie key placement: edge and align, never overlay.

    A floating key would cover wedges, so a pie key always reserves its room.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    edge: LegendEdge | Literal["auto"] | None = Field(
        default=None,
        description=(
            "Side of the wheel the key (legend or attached table) sits on. "
            "'auto' = the engine decides."
        ),
    )
    align: LegendAlign | Literal["auto"] | None = Field(
        default=None,
        description=(
            "Position of the key along that edge. 'auto' = the engine decides."
        ),
    )


class LegendRowStyle(BaseModel):
    """Fixed chrome around each entry of a single-row top legend.

    Constant regardless of mark size or entry count: a swatch stays legible even
    when the marks it names are countless and tiny. The label text is measured
    against the real resolved label font, never estimated.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    swatch_width: float = Field(ge=0, description="Swatch box width in pixels.")
    entry_gap: float = Field(
        ge=0, description="Gap in pixels between one entry and the next."
    )


class _LegendStyleBase(BaseModel):
    """Every legend field except ``position``, whose shape differs per family."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    direction: LegendDirection | None = Field(
        default=None,
        description=(
            "Legend layout direction. Omit to infer it from the edge: "
            "horizontal on top/bottom, vertical on left/right."
        ),
    )
    columns: int = Field(
        ge=0,
        description=(
            "Legend entry columns. Zero keeps the renderer default; positive values "
            "set Vega-Lite legend columns."
        ),
    )
    compact_columns: int = Field(
        ge=1,
        description=(
            "Entry columns when a horizontal legend wraps: the automatic compact "
            "top legend, or an inferred-horizontal legend too wide for one row."
        ),
    )
    row: LegendRowStyle = Field(
        description="Chrome around each entry of a single-row top legend."
    )
    label: LegendLabelStyle = Field(description="Legend label style.")
    title: LegendTitleStyle = Field(description="Legend title style.")
    visible: bool | Literal["auto"] | None = Field(
        default=None,
        description=(
            "Show the legend. 'auto' shows it unless direct labeling (endpoint "
            "labels, or a pie's slice labels) names the series instead; true "
            "keeps it beside direct labels and false hides it."
        ),
    )
    symbol_limit: Annotated[int | None, Absent("no cap")] = Field(
        default=None,
        ge=1,
        description=(
            "Maximum number of legend entries to display; maps to VL symbolLimit. "
            "None uses Vega-Lite's default (no cap). "
            "Set to a positive integer to prevent legend overflow on high-cardinality series."
        ),
    )
    # Cascade tier sentinel: None means "not specified at this tier"; the render
    # layer infers entry order from the color scale domain (data + layer order).
    # Authored as a list to pin/reorder legend entries explicitly (maps to VL
    # legend.values). No theme default — ordering is an author decision.
    values: list[str] | None = Field(
        default=None,
        description=(
            "Explicit legend entry order/filter; each entry resolves against "
            "the real legend domain by its rendered text or its column/measure "
            "name (case/separator-insensitive). None lets the renderer infer "
            "order from the data."
        ),
    )
    # Author override for the legend glyph shape (VL symbolType). None means
    # "let the renderer pick the mark-aware glyph"; a string (e.g. 'stroke',
    # 'square', 'diamond') emits a constant symbolType that wins over the
    # mark-derived Vega expression. No theme default.
    symbol_shape: str | None = Field(
        default=None,
        description=(
            "Override the legend glyph shape; maps to VL legend.symbolType. "
            "None uses the mark-aware glyph derived from the chart's mark type."
        ),
    )
    # Author override for symbol fill. False → symbolFillColor='transparent'
    # in the emitted VL legend, which makes hollow (stroke-only) glyphs. None
    # means "use Vega-Lite's default" (filled). No theme default.
    symbol_fill: bool | None = Field(
        default=None,
        description=(
            "When False, emits symbolFillColor='transparent' to produce a hollow "
            "legend glyph. None uses Vega-Lite's default (filled symbol)."
        ),
    )


class LegendStyle(_LegendStyleBase):
    position: LegendPositionStyle = Field(
        default_factory=LegendPositionStyle,
        description="Legend placement: edge, align along the edge, overlay or reserve.",
    )


class PieLegendStyle(_LegendStyleBase):
    position: PiePositionStyle = Field(
        default_factory=PiePositionStyle,
        description="Pie key placement: edge and align along it.",
    )
