"""Resolved annotation: the render payload for one drawn annotation.

Every leaf is set: computed anchors, clock periods, trend samples, series
expansion, template text and color roles are already resolved, so the engine
maps values to pixels, places text and draws. The shape is pinned by the engine's
annotation contract fixture.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field

from dbt_charts.core.compile.models.style.theme.legend import LegendAlign, LegendEdge

# A number, or an ISO date (`YYYY-MM-DD`, `YYYY-MM-DDTHH:MM[:SS]`). Dates are
# never numbers.
ResolvedScalar = float | str


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ResolvedValueAnchor(_Frozen):
    kind: Literal["value"] = Field(description="A single value on the axis.")
    value: ResolvedScalar = Field(description="Where the annotation sits.")


class ResolvedRangeAnchor(_Frozen):
    model_config = ConfigDict(frozen=True, extra="forbid", serialize_by_alias=True)

    kind: Literal["range"] = Field(description="A span on the axis.")
    from_: ResolvedScalar | None = Field(
        alias="from", description="Start of the span; empty runs to the axis edge."
    )
    to: ResolvedScalar | None = Field(
        description="End of the span; empty runs to the axis edge."
    )


class ResolvedCategoryAnchor(_Frozen):
    kind: Literal["category"] = Field(description="One slot on a discrete axis.")
    value: str = Field(description="The category whose slot is filled.")


ResolvedAnchor = Annotated[
    ResolvedValueAnchor | ResolvedRangeAnchor | ResolvedCategoryAnchor,
    Discriminator("kind"),
]


class ResolvedLayerAxis(_Frozen):
    layer: int = Field(description="Position of the layer whose y scale is read.")


class ResolvedTrend(_Frozen):
    line: tuple[tuple[ResolvedScalar, float], ...] = Field(
        min_length=2, description="Fitted points, x ascending."
    )
    interval: tuple[tuple[ResolvedScalar, float, float], ...] | None = Field(
        description="Low and high bounds at the same x values as the line."
    )


class ResolvedAnnotationStroke(_Frozen):
    color: str = Field(description="Stroke color.")
    width: float = Field(description="Stroke width in pixels.")
    dasharray: str | None = Field(
        description="Dash pattern in pixels, such as `6 3`; none draws solid."
    )


class ResolvedAnnotationRule(_Frozen):
    opacity: float = Field(description="Rule opacity.")
    stroke: ResolvedAnnotationStroke = Field(description="Rule stroke.")


class ResolvedAnnotationRect(_Frozen):
    opacity: float = Field(description="Fill opacity.")
    fill: str = Field(description="Fill color.")
    stroke: ResolvedAnnotationStroke | None = Field(
        description="Outline; empty draws none."
    )
    padding: float | None = Field(
        description="Space between an enclosed note's text and the box; empty on a band."
    )


class ResolvedAnnotationLine(_Frozen):
    opacity: float = Field(description="Line opacity.")
    curve: Literal["linear", "natural"] = Field(description="How points are joined.")
    stroke: ResolvedAnnotationStroke = Field(description="Line stroke.")
    length: float | None = Field(
        description="Anchor-to-text distance of a connected note; empty on a trend."
    )


class ResolvedAnnotationArea(_Frozen):
    opacity: float = Field(description="Fill opacity.")
    fill: str = Field(description="Fill color.")


class ResolvedAnnotationCircle(_Frozen):
    opacity: float = Field(description="Circle opacity.")
    radius: float = Field(description="Circle radius in pixels.")
    point_radius: float = Field(
        description="Outer radius of the point marks a connector clears, in pixels; "
        "0 when there are none."
    )
    stroke: ResolvedAnnotationStroke = Field(description="Circle stroke.")


class ResolvedAnnotationFont(_Frozen):
    family: tuple[str, ...] = Field(description="Font families, first match wins.")
    size: float = Field(description="Font size in pixels.")
    weight: float = Field(description="Font weight.")
    style: Literal["normal", "italic"] = Field(description="Upright or italic.")
    line_height: float = Field(description="Line height as a multiple of the size.")
    color: str = Field(description="Text color.")


class ResolvedAnnotationBoxStroke(_Frozen):
    color: str = Field(description="Outline color.")
    width: float = Field(description="Outline width in pixels.")


class ResolvedAnnotationBox(_Frozen):
    visible: bool = Field(description="Whether a box is drawn behind the text.")
    fill: str = Field(description="Box fill.")
    opacity: float = Field(description="Box opacity.")
    padding: float = Field(description="Space between the text and the box edge.")
    radius: float = Field(description="Corner radius in pixels.")
    stroke: ResolvedAnnotationBoxStroke | None = Field(
        description="Outline; empty draws none."
    )


class ResolvedAnnotationPosition(_Frozen):
    edge: LegendEdge = Field(description="Side of the geometry the text sits on.")
    align: LegendAlign = Field(description="Where along that side.")
    overlay: bool = Field(description="On or inside the geometry, rather than outside.")
    auto_edge: bool = Field(
        description=(
            "The edge came from `auto`: the text may leave it for another side "
            "to clear marks and other text."
        )
    )
    auto_align: bool = Field(
        description=(
            "The align came from `auto`: the text may take another spot along "
            "an edge to clear marks and other text."
        )
    )


class ResolvedAnnotationText(_Frozen):
    font: ResolvedAnnotationFont = Field(description="Text font.")
    halo: float = Field(description="Canvas-colored outline width in pixels.")
    halo_opacity: float = Field(description="Opacity of the halo as a whole, 0 to 1.")
    halo_over_marks: bool = Field(
        description="Draw the halo only on text that overlaps a data mark."
    )
    box: ResolvedAnnotationBox = Field(description="Box behind the text.")
    position: ResolvedAnnotationPosition = Field(
        description="Where the text is placed."
    )
    dx: float = Field(description="Horizontal nudge in pixels.")
    dy: float = Field(description="Vertical nudge in pixels.")
    max_width: float | None = Field(
        description="Wrap width in pixels, kept inside the plot by the engine; set only on a frame note or a trend."
    )
    max_share: float | None = Field(
        description="Most of the plot's width the text may take beside the plot; set only on a trend."
    )


class ResolvedAnnotationStyle(_Frozen):
    """The marks the annotation's type draws; the rest are empty."""

    rule: ResolvedAnnotationRule | None = Field(
        default=None, description="Rule mark style."
    )
    rect: ResolvedAnnotationRect | None = Field(
        default=None, description="Band and enclosure rect style."
    )
    line: ResolvedAnnotationLine | None = Field(
        default=None, description="Connector and trend line style."
    )
    area: ResolvedAnnotationArea | None = Field(
        default=None, description="Trend interval style."
    )
    circle: ResolvedAnnotationCircle | None = Field(
        default=None, description="Enclosure circle style."
    )
    text: ResolvedAnnotationText = Field(description="Text style.")


class ResolvedAnnotationGap(_Frozen):
    """An annotation that draws nothing in some scope; the chart still renders."""

    index: int = Field(description="Position in the authored list.")
    reason: str = Field(description="Which scopes are empty, and why.")


class ResolvedAnnotation(_Frozen):
    index: int = Field(description="Position in the authored list.")
    type: Literal["rule", "band", "note", "trend"] = Field(description="What is drawn.")
    plane: Literal["background", "foreground"] = Field(
        description="Below or above the data marks."
    )
    panel: str | None = Field(
        description="The one panel drawn on; empty draws on every panel."
    )
    series: str | None = Field(
        description="The series this annotation was computed for."
    )
    y_axis: Literal["base"] | ResolvedLayerAxis = Field(
        description="Which y scale the y anchor reads."
    )
    x: ResolvedAnchor | None = Field(description="Anchor on the x axis.")
    y: ResolvedAnchor | None = Field(description="Anchor on the y axis.")
    text: str | None = Field(description="Text drawn; empty draws none.")
    connector: Literal["none", "line", "arrow"] = Field(
        description="Line from a note's anchor to its text."
    )
    enclose: Literal["circle", "rect"] | None = Field(
        description="Shape drawn around a note's anchor."
    )
    trend: ResolvedTrend | None = Field(description="Fitted curve of a trend.")
    style: ResolvedAnnotationStyle = Field(description="Fully resolved style.")
