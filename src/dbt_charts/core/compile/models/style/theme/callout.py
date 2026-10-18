"""Theme-stage style classes: callout chart family."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.markers import (
    Color,
    InheritSlot,
)
from dbt_charts.core.compile.models.primitives import (
    BorderStyle,
    FontStyle,
    ToneLiteral,
)
from dbt_charts.core.compile.models.style.theme.board import (
    PaddingStyle,
)


class CalloutElementStyle(BaseModel):
    """Font + y_offset for a callout title or message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # InheritSlot: callout title/message font fills from charts.font.
    font: Annotated[FontStyle, InheritSlot(from_path="Style.charts.font")] = Field(
        default_factory=FontStyle,
        description=(
            "Element font style overrides. A color of `by_tone` is the tone's color."
        ),
    )
    y_offset: float = Field(
        description="Vertical offset from the element's anchor in pixels."
    )


class CalloutChartStyle(BaseModel):
    """Chart-family style for ``type: callout`` charts and runtime chart-error fallback cards.

    The theme sets background, border color, and title and message font color to
    ``by_tone``; at resolve time each takes its ``{tone}.*`` palette role (bg,
    border, solid, text), and any color a board or chart authors wins instead.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    preferred_width: float = Field(description="Preferred callout width in pixels.")

    tone: ToneLiteral = Field(
        description="Semantic tone for palette-role lookup (info | positive | negative | warning).",
    )
    background: Annotated[str, Color()] = Field(
        description="Callout card background color. `by_tone` is the tone's color."
    )
    border: BorderStyle = Field(
        description="Callout card border style. A color of `by_tone` is the tone's color."
    )
    padding: Annotated[PaddingStyle, InheritSlot(from_path="Style.charts.padding")] = (
        Field(
            description="Padding as {top, right, bottom, left} in pixels, never one number."
        )
    )
    section_gap: float = Field(
        description="Vertical gap between callout title and message in pixels."
    )
    title: CalloutElementStyle = Field(description="Callout title element style.")
    message: CalloutElementStyle = Field(description="Callout message element style.")
