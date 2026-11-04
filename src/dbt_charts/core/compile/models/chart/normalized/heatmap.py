"""Normalized heatmap chart."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dbt_charts.core.compile.models.style.authored import HeatmapChartStylePatch

from ._base import _CartesianChartFields, _PatternField


class HeatmapChart(_CartesianChartFields, _PatternField):
    """Normalized heatmap chart."""

    type: Literal["heatmap"]
    style: HeatmapChartStylePatch | None = Field(
        default=None, description="Chart-local style overrides."
    )
