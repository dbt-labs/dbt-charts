"""Theme-stage style classes: layout containers (rows, cols, grid, tabs, details)."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.markers import (
    Absent,
    InheritSlot,
)
from dbt_charts.core.compile.models.primitives import (
    BorderStyle,
    FontStyle,
)


class ViewStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stroke: Annotated[str | None, Absent("no border")] = Field(
        description="Plot area border stroke color; None means no border."
    )
    continuous_width: float = Field(
        description="Default plot width for continuous (quantitative) scales in pixels."
    )
    continuous_height: float = Field(
        description="Default plot height for continuous (quantitative) scales in pixels."
    )
    discrete_width: float | None = Field(
        default=None,
        description="Default plot width for discrete (ordinal/nominal) scales in pixels; None means auto.",
    )
    discrete_height: float | None = Field(
        default=None,
        description="Default plot height for discrete (ordinal/nominal) scales in pixels; None means auto.",
    )


class LayoutGapStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gap: float = Field(description="Gap between layout items in pixels.")


class GridLayoutStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    columns: int = Field(description="Number of columns in the grid layout.")
    gap: float = Field(description="Gap between grid cells in pixels.")


class TabsStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bar_height: float = Field(description="Tab bar height in pixels.")
    border: BorderStyle = Field(description="Tab bar border style.")
    font: Annotated[FontStyle, InheritSlot(from_path="Style.font")] = Field(
        default_factory=FontStyle, description="Tab label font style overrides."
    )
    active_weight: str = Field(description="Font weight for the active tab label.")
    inactive_weight: str = Field(description="Font weight for inactive tab labels.")
    # SVG layout constants
    title_baseline_offset: float = Field(
        description="Vertical offset to align SVG tab label baseline in pixels."
    )


class DetailsArrowFontStyle(BaseModel):
    """Font style for the expand/collapse arrow glyph."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    size: float = Field(description="Font size of the arrow glyph in pixels.")


class DetailsArrowStyle(BaseModel):
    """Layout and font style for the expand/collapse arrow chevron."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    x: float = Field(description="X position of the arrow in pixels.")
    font: DetailsArrowFontStyle = Field(description="Arrow glyph font style.")


class DetailsStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary_height: float = Field(
        description="Height of the details summary (collapsed) row in pixels."
    )
    border: BorderStyle = Field(description="Details element border style.")
    font: Annotated[FontStyle, InheritSlot(from_path="Style.font")] = Field(
        default_factory=FontStyle,
        description="Details summary font style overrides.",
    )
    # SVG layout constants
    arrow: DetailsArrowStyle = Field(
        description="Expand/collapse arrow glyph layout and font style."
    )
    label_x: float = Field(
        description="X position of the details summary label text in pixels."
    )
    text_baseline_offset: float = Field(
        description="Vertical offset to align SVG details text baseline in pixels."
    )
    content_y_offset: float = Field(
        description="Y offset of the expanded details content area in pixels."
    )


class LayoutStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rows: LayoutGapStyle = Field(description="Gap sizing between rows.")
    cols: LayoutGapStyle = Field(description="Gap sizing between columns.")
    grid: GridLayoutStyle = Field(description="Grid layout configuration.")
    tabs: TabsStyle = Field(description="Tabs layout style.")
    details: DetailsStyle = Field(description="Details (accordion) layout style.")
