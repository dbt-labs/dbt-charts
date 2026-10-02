"""Point-map family resolved style slice."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
from dbt_charts.core.compile.models.style.resolved._marks import ResolvedPointMarkStyle
from dbt_charts.core.compile.models.style.theme import PointMapChartStyle


class ResolvedPointMapStyle(BaseModel):
    """Point-map family style slice projected from the style cascade."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    point_map: PointMapChartStyle | None = Field(
        default=None,
        description="Resolved point-map style from the cascade.",
    )
    point_mark: ResolvedPointMarkStyle = Field(
        description="Baked point mark style for scatter-style points on maps.",
    )
    single_series_fill: str = Field(
        description="Mark ink for a map with no color channel, baked at resolve time.",
    )
    tooltip_format: str = Field(
        description="Pre-resolved tooltip format string (empty = no format).",
    )
    title_font: ResolvedFontStyle | None = Field(
        default=None,
        description="Baked title font (size/weight/family/…) from the width tier.",
    )


__all__ = ["ResolvedPointMapStyle"]
