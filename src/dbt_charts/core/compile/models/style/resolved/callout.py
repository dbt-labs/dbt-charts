"""Callout family resolved style slice."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from dbt_charts.core.compile.models.primitives import (
    BorderStyle,
    ResolvedFontStyle,
    ToneLiteral,
)
from dbt_charts.core.compile.models.style.theme import PaddingStyle


class ResolvedCalloutElementStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    font: ResolvedFontStyle
    y_offset: float


class ResolvedCalloutStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    preferred_width: float
    tone: ToneLiteral
    background: str
    border: BorderStyle
    padding: PaddingStyle
    section_gap: float
    bold_font_weight: str
    # The theme's code font: markdown code paints in it, as it is measured.
    code_family: str
    title: ResolvedCalloutElementStyle
    message: ResolvedCalloutElementStyle


class ResolvedErrorTileStyle(ResolvedCalloutStyle):
    """The tile core paints for a failed chart: a callout (its `tone` is inherited
    and paints nothing) plus the stripes' color."""

    stripe_color: str


__all__ = [
    "ResolvedCalloutElementStyle",
    "ResolvedCalloutStyle",
    "ResolvedErrorTileStyle",
]
