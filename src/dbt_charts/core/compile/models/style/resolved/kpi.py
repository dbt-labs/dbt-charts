"""KPI family resolved style slice."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
from dbt_charts.core.compile.models.style.theme import KpiChartStyle, TitleStyle


class ResolvedKpiStyle(BaseModel):
    """KPI family style slice projected from the style cascade."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kpi: KpiChartStyle | None = Field(
        default=None,
        description=(
            "Resolved KPI chart style (tone, font, border) from the cascade. "
            "Not yet populated; value is set during resolution."
        ),
    )
    title_font: ResolvedFontStyle | None = Field(
        default=None,
        description="Baked title font (size/weight/family/…) from the width tier.",
    )
    # kpi.title (above) stays the sparse per-family/chart-local TitleStylePatch
    # authored on this chart — apply_inherit deliberately leaves it unfilled
    # (see _ChartStyleBase.title's SkipInheritSlots). This field is the
    # already-merged, always-complete title: board -> family-theme ->
    # chart-local (build_chart_style_context's Title block), so render reads
    # one final value unconditionally instead of choosing between sources
    # itself.
    title: TitleStyle = Field(
        description="Resolved KPI title style: any family or chart-level override already merged onto the board default."
    )


__all__ = ["ResolvedKpiStyle"]
