"""Geoshape family resolved style slice."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.primitives import (
    ResolvedFontStyle,
    ResolvedFormat,
    ResolvedScaleTarget,
    StaticGradientColorStyle,
)
from dbt_charts.core.compile.models.style.theme import (
    GeoshapeChartStyle,
    ScatterChartStyle,
)


class ResolvedStaticGradientColorStyle(StaticGradientColorStyle):
    """StaticGradientColorStyle with a resolved scale target for gradient.

    Used by ResolvedGeoshapeChartStyle to carry the baked gradient scale
    after _with_baked_color_gradient runs in the geoshape resolve path.
    The base StaticGradientColorStyle is kept for pre-bake use (theme
    cascade, authored style); this resolved variant is only constructed
    at resolve time.
    """

    gradient: ResolvedScaleTarget | None = Field(
        default=None,
        description="Continuous gradient scale — resolved to the correct subtype at compile time.",
    )


class ResolvedGeoshapeChartStyle(GeoshapeChartStyle):
    """GeoshapeChartStyle with a resolved color field.

    Carries a ResolvedStaticGradientColorStyle so the gradient's resolved_stops
    are preserved through pydantic serialization (board artifact round-trip).
    emitters/geo.py reads chart.style.geoshape.color.gradient from this field
    unchanged — the path is identical, now provably resolved.
    """

    color: ResolvedStaticGradientColorStyle | None = Field(  # type: ignore[assignment]
        default=None,
        description="Color config — static paint or resolved gradient.",
    )


class ResolvedGeoshapeStyle(BaseModel):
    """Geoshape family style slice projected from the style cascade."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    geoshape: ResolvedGeoshapeChartStyle | None = Field(
        default=None,
        description="Resolved choropleth style from the cascade.",
    )
    scatter: ScatterChartStyle | None = Field(
        default=None,
        description="Scatter overlay style for point marks on geo families.",
    )
    tooltip_format: ResolvedFormat = Field(
        description="Resolved tooltip format; an affix composes on the hover card.",
    )
    title_font: ResolvedFontStyle | None = Field(
        default=None,
        description="Baked title font (size/weight/family/…) from the width tier.",
    )


__all__ = [
    "ResolvedGeoshapeChartStyle",
    "ResolvedGeoshapeStyle",
    "ResolvedStaticGradientColorStyle",
]
