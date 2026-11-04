"""Resolved heatmap chart model."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dbt_charts.core.compile.models.style.resolved import ResolvedHeatmapStyle

from ._base import _CartesianResolvedChartFields, _PatternResolvedFields


class ResolvedHeatmapChart(_CartesianResolvedChartFields, _PatternResolvedFields):
    """Render-ready heatmap chart."""

    chart_type: Literal["heatmap"] = Field(
        description="Discriminator key; always 'heatmap'.",
    )
    style: ResolvedHeatmapStyle = Field(
        description="Heatmap family style slice.",
    )
