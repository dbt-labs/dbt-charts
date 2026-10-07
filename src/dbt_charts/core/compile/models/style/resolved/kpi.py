"""KPI family resolved style slice."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.style.theme import KpiChartStyle


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


__all__ = ["ResolvedKpiStyle"]
