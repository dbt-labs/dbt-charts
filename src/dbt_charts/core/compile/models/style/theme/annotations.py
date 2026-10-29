"""Theme-stage style classes: annotations (rules, bands, notes, trends)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from dbt_charts.core.compile.models.factories import FontAlias, FontSlotBase
from dbt_charts.core.compile.models.markers import (
    AUTO,
    Absent,
    Color,
    FontFamily,
    FontWeightValue,
    InheritSlot,
    SkipInheritSlots,
)
from dbt_charts.core.compile.models.primitives import one_error
from dbt_charts.core.compile.models.style.theme.legend import LegendAlign, LegendEdge

# What the engine draws: `auto` is sized to the stroke width, else dash/gap lengths.
AnnotationDasharray = Annotated[
    str,
    StringConstraints(pattern=r"^(auto|\s*\d+(\.\d+)?([ ,]+\d+(\.\d+)?)*\s*)$"),
    one_error(
        "dasharray must be `auto` or positive numbers such as '6 3', got {value}"
    ),
]
AnnotationCurve = Literal["linear", "natural"]
AnnotationOpacity = Annotated[float, Field(ge=0, le=1)]
# A rule label's color keyword: the color of the rule's own line.
RULE_LINE = "line"


class AnnotationStrokeStyle(BaseModel):
    """An annotation mark's outline: color, width and dash pattern."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    # Cascade tier sentinels: None means "not specified at this tier".
    color: Annotated[str | None, Color()] = Field(
        default=None, description="Stroke color."
    )
    width: float | None = Field(
        default=None, ge=0, description="Stroke width in pixels."
    )
    dasharray: Annotated[AnnotationDasharray | None, Absent("solid line")] = Field(
        default=None,
        description=(
            "Dash pattern: lengths of dashes and blanks (e.g. '4 2'), or `auto` "
            "for a pattern sized to the width."
        ),
    )


class RuleStrokeStyle(AnnotationStrokeStyle):
    """A rule's line, whose color may be left to the engine."""

    color: Annotated[str | None, Color(keywords=(AUTO,))] = Field(
        default=None,
        description=(
            "Stroke color. `auto` draws the quietest gray of the theme's chrome "
            "ladder that holds 3:1 against the canvas and the marks the rule crosses."
        ),
    )


class AnnotationBoxStrokeStyle(BaseModel):
    """The outline of the box behind an annotation's text."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    color: Annotated[str | None, Color()] = Field(
        default=None, description="Outline color."
    )
    width: float | None = Field(
        default=None, ge=0, description="Outline width in pixels."
    )


class AnnotationFontStyle(FontSlotBase):
    """The font of an annotation's text: the fields the engine sets type with."""

    extends: FontAlias | None = Field(
        default=None,
        description=(
            "Name of a ``style.fonts`` entry this slot starts from; the fields "
            "set beside it override that entry."
        ),
    )
    family: Annotated[str | None, FontFamily()] = Field(
        default=None,
        description="Font family or CSS stack; single-quote multiword names: \"'Source Serif 4', serif\".",
    )
    color: Annotated[str | None, Color()] = Field(
        default=None, description="Text color as a CSS color string."
    )
    size: float | None = Field(default=None, gt=0, description="Font size in pixels.")
    weight: Annotated[
        Annotated[float, Field(gt=0)]
        | Literal["normal", "bold", "bolder", "lighter"]
        | None,
        FontWeightValue(),
    ] = Field(
        default=None,
        description="How heavy the type is drawn: a number such as 400 or 700, or normal, bold, bolder, lighter.",
    )
    style: Literal["normal", "italic"] | None = Field(
        default=None, description="Upright or slanted type (normal or italic)."
    )
    line_height: float | None = Field(
        default=None,
        gt=0,
        description="Line height as a unitless multiple of font size.",
    )


class RuleFontStyle(AnnotationFontStyle):
    """A rule's label font, whose color may follow the rule's line."""

    color: Annotated[str | None, Color(keywords=(RULE_LINE,))] = Field(
        default=None,
        description="Text color as a CSS color string; `line` is the rule's line color.",
    )


class AnnotationPositionStyle(BaseModel):
    """Which side of the annotation the text sits on, and where along it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    edge: LegendEdge | None = Field(
        default=None, description="Side of the annotation the text sits on."
    )
    align: LegendAlign | None = Field(
        default=None, description="Position along the edge."
    )
    overlay: bool | None = Field(
        default=None,
        description="True draws the text over the plot; False reserves room for it.",
    )


class BandPositionStyle(BaseModel):
    """A band's text position; `auto` follows the band's direction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    edge: LegendEdge | Literal["auto"] | None = Field(
        default=None,
        description="Side of the band the text sits on; `auto` is top for an x band, right for a y band.",
    )
    align: LegendAlign | Literal["auto"] | None = Field(
        default=None, description="Position along the edge; `auto` is center."
    )
    overlay: bool | Literal["auto"] | None = Field(
        default=None,
        description="True draws the text over the plot; False reserves room for it; `auto` is True.",
    )


class RulePositionStyle(AnnotationPositionStyle):
    """A rule's text position; `auto` lets placement move it off the chart's ink."""

    edge: LegendEdge | Literal["auto"] | None = Field(
        default=None,
        description="Side of the rule the text sits on; `auto` is top, or another side when that one is taken.",
    )
    align: LegendAlign | Literal["auto"] | None = Field(
        default=None,
        description="Position along the edge; `auto` is end, or another spot when that one is taken.",
    )


class NotePositionStyle(AnnotationPositionStyle):
    """A note's text position; `auto` follows the value axis."""

    edge: LegendEdge | Literal["auto"] | None = Field(
        default=None,
        description="Side of the anchor the text sits on; `auto` is past the value: top on a vertical chart, right on a horizontal one.",
    )
    align: LegendAlign | Literal["auto"] | None = Field(
        default=None,
        description="Position along the edge; `auto` is center, or another spot when that one is taken.",
    )


class AnnotationTextBoxStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Cascade tier sentinels: None means "not specified at this tier".
    visible: bool | None = Field(
        default=None, description="Draw a box behind the text."
    )
    fill: Annotated[str | None, Color()] = Field(
        default=None, description="Box fill color."
    )
    opacity: float | None = Field(
        default=None, ge=0, le=1, description="Box fill opacity, 0 to 1."
    )
    padding: float | None = Field(
        default=None,
        ge=0,
        description="Space between the text and the box edge, in pixels.",
    )
    radius: float | None = Field(
        default=None, ge=0, description="Corner radius of the box, in pixels."
    )
    stroke: Annotated[AnnotationBoxStrokeStyle | None, Absent("no outline")] = Field(
        default=None, description="Outline of the box."
    )


class AnnotationTextMarkStyle(BaseModel):
    """Annotation text: its font, halo, box, placement and nudge."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # InheritSlot: the font fills from charts.font, as a text mark's does.
    font: Annotated[AnnotationFontStyle, InheritSlot(from_path="Style.charts.font")] = (
        Field(default_factory=AnnotationFontStyle, description="Text font style.")
    )
    # SkipInheritSlots: the global text mark has no such fields to fall back to;
    # the annotation theme slot sets each one. None means "not set at this tier".
    halo: Annotated[
        Annotated[float, Field(ge=0)] | Literal["auto"] | None, SkipInheritSlots()
    ] = Field(
        default=None,
        description=(
            "Width of the canvas-colored outline behind the text, in pixels; 0 "
            "draws none. `auto` draws a 3px outline only on text that overlaps a "
            "bar, point or area fill."
        ),
    )
    halo_opacity: Annotated[float | None, SkipInheritSlots()] = Field(
        default=None,
        ge=0,
        le=1,
        description="Opacity of the halo as a whole, 0 to 1; overlapping letter outlines do not darken.",
    )
    box: Annotated[AnnotationTextBoxStyle, SkipInheritSlots()] = Field(
        default_factory=AnnotationTextBoxStyle, description="Box drawn behind the text."
    )
    position: Annotated[AnnotationPositionStyle, SkipInheritSlots()] = Field(
        default_factory=AnnotationPositionStyle,
        description="Which side of the annotation the text sits on, and where along it.",
    )
    dx: Annotated[float | None, SkipInheritSlots()] = Field(
        default=None, description="Horizontal nudge after placement, in pixels."
    )
    dy: Annotated[float | None, SkipInheritSlots()] = Field(
        default=None, description="Vertical nudge after placement, in pixels."
    )


class BandTextMarkStyle(AnnotationTextMarkStyle):
    """A band's text, whose position may follow the band's direction."""

    position: Annotated[BandPositionStyle, SkipInheritSlots()] = Field(
        default_factory=BandPositionStyle,
        description="Which side of the band the text sits on, and where along it.",
    )


class RuleTextMarkStyle(AnnotationTextMarkStyle):
    """A rule's text."""

    font: Annotated[RuleFontStyle, InheritSlot(from_path="Style.charts.font")] = Field(
        default_factory=RuleFontStyle, description="Text font style."
    )
    position: Annotated[RulePositionStyle, SkipInheritSlots()] = Field(
        default_factory=RulePositionStyle,
        description="Which side of the rule the text sits on, and where along it.",
    )


class NoteTextMarkStyle(AnnotationTextMarkStyle):
    """A note's text, plus the measure a note with no anchor wraps to."""

    position: Annotated[NotePositionStyle, SkipInheritSlots()] = Field(
        default_factory=NotePositionStyle,
        description="Which side of the anchor the text sits on, and where along it.",
    )

    max_width: Annotated[
        Annotated[float, Field(gt=0)] | Literal["auto"] | None, SkipInheritSlots()
    ] = Field(
        default=None,
        description=(
            "Width in pixels a note with no anchor wraps its text to; `auto` is a "
            "readable measure of about 28 characters, kept inside the plot. A "
            "note anchored at a point does not wrap."
        ),
    )


class TrendTextMarkStyle(AnnotationTextMarkStyle):
    """A trend's text, plus the measure it wraps to."""

    max_width: Annotated[
        Annotated[float, Field(gt=0)] | Literal["auto"] | None, SkipInheritSlots()
    ] = Field(
        default=None,
        description=(
            "Width in pixels a trend's text wraps to; `auto` is a readable measure "
            "of about 28 characters, kept inside the plot."
        ),
    )


class AnnotationLineMarkStyle(BaseModel):
    """A note's connector or a trend's curve: stroke, curve and opacity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stroke: AnnotationStrokeStyle = Field(
        default_factory=AnnotationStrokeStyle, description="Line stroke style."
    )
    # SkipInheritSlots: a chart line's curve may be one annotations cannot draw.
    curve: Annotated[AnnotationCurve | None, SkipInheritSlots()] = Field(
        default=None,
        description=(
            "Line interpolation: `linear` is straight, `natural` bends a note's "
            "connector and smooths a trend's curve."
        ),
    )
    # SkipInheritSlots: the global line mark has no opacity to fall back to;
    # the annotation theme slot sets it. None means "not set at this tier".
    opacity: Annotated[AnnotationOpacity | None, SkipInheritSlots()] = Field(
        default=None, description="Line opacity, 0 to 1."
    )


class NoteLineMarkStyle(AnnotationLineMarkStyle):
    """A note's connector: the annotation line plus its length."""

    length: Annotated[float | None, SkipInheritSlots()] = Field(
        default=None,
        ge=0,
        description="Distance between the anchor and the note's text when a connector is drawn, in pixels.",
    )


class AnnotationAreaMarkStyle(BaseModel):
    """A trend's interval: opacity and fill."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # SkipInheritSlots: the global area mark takes its opacity and fill from the
    # series; the annotation theme slot sets both. None means "not set at this tier".
    opacity: Annotated[AnnotationOpacity | None, SkipInheritSlots()] = Field(
        default=None, description="Interval fill opacity, 0 to 1."
    )
    fill: Annotated[str | None, Color(), SkipInheritSlots()] = Field(
        default=None, description="Interval fill color."
    )


class BandRectMarkStyle(BaseModel):
    """A band's region: opacity, outline and fill."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    opacity: Annotated[AnnotationOpacity | None, SkipInheritSlots()] = Field(
        default=None, description="Region opacity, 0 to 1."
    )
    # SkipInheritSlots(cascade=True): apply_inherit copies the entire stroke object
    # from the parent slot when stroke is None.
    stroke: Annotated[
        AnnotationStrokeStyle | None,
        SkipInheritSlots(cascade=True),
        Absent("no stroke"),
    ] = Field(default=None, description="Outline of the region.")
    # SkipInheritSlots: the global rect mark has no fill to fall back to;
    # the annotation theme slot sets it. None means "not set at this tier".
    fill: Annotated[str | None, Color(), SkipInheritSlots()] = Field(
        default=None, description="Fill color of the region."
    )


class NoteRectMarkStyle(BandRectMarkStyle):
    """A box around a note: the band rect plus the inset around the text."""

    padding: Annotated[float | None, SkipInheritSlots()] = Field(
        default=None,
        ge=0,
        description="Space between the note's text and the box drawn around it, in pixels.",
    )


class AnnotationCircleMarkStyle(BaseModel):
    """A circle around a note's anchor: opacity, outline and radius."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    opacity: Annotated[AnnotationOpacity | None, SkipInheritSlots()] = Field(
        default=None, description="Circle opacity, 0 to 1."
    )
    stroke: AnnotationStrokeStyle = Field(
        default_factory=AnnotationStrokeStyle, description="Outline of the circle."
    )
    # SkipInheritSlots: the global circle mark has no radius to fall back to;
    # the annotation theme slot sets it. None means "not set at this tier".
    radius: Annotated[
        Annotated[float, Field(ge=0)] | Literal["auto"] | None, SkipInheritSlots()
    ] = Field(
        default=None,
        description=(
            "Radius of the circle, in pixels; `auto` clears the annotated point "
            "by the text gap, at least 6."
        ),
    )


class _AnnotationMarksStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnnotationRuleMarkStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    opacity: Annotated[AnnotationOpacity | None, SkipInheritSlots()] = Field(
        default=None, description="Rule opacity, 0 to 1."
    )
    stroke: RuleStrokeStyle = Field(
        default_factory=RuleStrokeStyle, description="Rule stroke style."
    )


class RuleAnnotationMarksStyle(_AnnotationMarksStyle):
    rule: AnnotationRuleMarkStyle = Field(
        default_factory=AnnotationRuleMarkStyle, description="The line of the rule."
    )
    text: RuleTextMarkStyle = Field(
        default_factory=RuleTextMarkStyle, description="The rule's text."
    )


class BandAnnotationMarksStyle(_AnnotationMarksStyle):
    rect: BandRectMarkStyle = Field(
        default_factory=BandRectMarkStyle, description="The shaded region."
    )
    text: BandTextMarkStyle = Field(
        default_factory=BandTextMarkStyle, description="The band's text."
    )


class NoteAnnotationMarksStyle(_AnnotationMarksStyle):
    text: NoteTextMarkStyle = Field(
        default_factory=NoteTextMarkStyle, description="The note's text."
    )
    line: NoteLineMarkStyle = Field(
        default_factory=NoteLineMarkStyle,
        description="The connector from the anchor to the text.",
    )
    circle: AnnotationCircleMarkStyle = Field(
        default_factory=AnnotationCircleMarkStyle,
        description="The circle around the anchor.",
    )
    rect: NoteRectMarkStyle = Field(
        default_factory=NoteRectMarkStyle,
        description="The box around the note.",
    )


class TrendAnnotationMarksStyle(_AnnotationMarksStyle):
    line: AnnotationLineMarkStyle = Field(
        default_factory=AnnotationLineMarkStyle, description="The fitted curve."
    )
    area: AnnotationAreaMarkStyle = Field(
        default_factory=AnnotationAreaMarkStyle, description="The interval."
    )
    text: TrendTextMarkStyle = Field(
        default_factory=TrendTextMarkStyle, description="The trend's text."
    )


# InheritSlot: any mark leaf left unset falls back to style.charts.marks.
class RuleAnnotationStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    marks: Annotated[
        RuleAnnotationMarksStyle, InheritSlot(from_path="Style.charts.marks")
    ] = Field(default_factory=RuleAnnotationMarksStyle, description="Marks of a rule.")


class BandAnnotationStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    marks: Annotated[
        BandAnnotationMarksStyle, InheritSlot(from_path="Style.charts.marks")
    ] = Field(default_factory=BandAnnotationMarksStyle, description="Marks of a band.")


class NoteAnnotationStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    marks: Annotated[
        NoteAnnotationMarksStyle, InheritSlot(from_path="Style.charts.marks")
    ] = Field(default_factory=NoteAnnotationMarksStyle, description="Marks of a note.")


class TrendAnnotationStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    marks: Annotated[
        TrendAnnotationMarksStyle, InheritSlot(from_path="Style.charts.marks")
    ] = Field(
        default_factory=TrendAnnotationMarksStyle, description="Marks of a trend."
    )


class NoteFrameTextStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    position: AnnotationPositionStyle = Field(
        description="Where a note with no anchor sits on the plot frame."
    )


class NoteFrameStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: NoteFrameTextStyle = Field(description="Text of a note with no anchor.")


class NoteAnnotationThemeStyle(NoteAnnotationStyle):
    """Theme defaults for notes, plus the placement of a note with no anchor."""

    frame: NoteFrameStyle = Field(
        description="Defaults for a note pinned to the plot frame."
    )


class TrendLabelStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_share: Annotated[float, Field(gt=0, le=1)] = Field(
        description=(
            "Most of the plot's width a trend's text may take beside the plot; the "
            "wrap measure shrinks to fit."
        )
    )


class TrendAnnotationThemeStyle(TrendAnnotationStyle):
    """Theme defaults for trends, plus the wrap of their text."""

    label: TrendLabelStyle = Field(description="Defaults for a trend's text wrap.")


class AnnotationsStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule: RuleAnnotationStyle = Field(description="Defaults for rules.")
    band: BandAnnotationStyle = Field(description="Defaults for bands.")
    note: NoteAnnotationThemeStyle = Field(description="Defaults for notes.")
    trend: TrendAnnotationThemeStyle = Field(description="Defaults for trends.")
