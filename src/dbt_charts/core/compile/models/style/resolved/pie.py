"""Pie/donut family resolved style slice."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.primitives import ResolvedFontStyle, ResolvedFormat
from dbt_charts.core.compile.models.style.theme import (
    SliceMarkStyle,
    TotalStyle,
    TotalValueSlotStyle,
)


class ResolvedTotalValueSlot(TotalValueSlotStyle):
    format: ResolvedFormat | None = Field(
        default=None, description="Resolved donut center value format."
    )


class ResolvedTotalStyle(TotalStyle):
    value: ResolvedTotalValueSlot = Field(
        description="Donut center value paint and its resolved format."
    )


class ResolvedPieStyle(BaseModel):
    """Pie/donut family style slice — self-contained, globals merged down."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    inner_radius: float = Field(
        description="Hole-to-disk ratio 0–1; 0.0 renders a full (solid) pie.",
    )
    slice_mark: SliceMarkStyle = Field(
        description="Cascade-merged slice mark geometry (opacity, corner radius, labels).",
    )
    tooltip_format: ResolvedFormat = Field(
        description="Resolved d3 tooltip number format ('' = VL default).",
    )
    total_style: ResolvedTotalStyle = Field(
        description="Donut center total paint: value number (font + resolved format) and caption label (font).",
    )
    title_font: ResolvedFontStyle | None = Field(
        default=None,
        description="Baked title font (size/weight/family/…) from the width tier.",
    )


__all__ = ["ResolvedPieStyle", "ResolvedTotalStyle", "ResolvedTotalValueSlot"]
