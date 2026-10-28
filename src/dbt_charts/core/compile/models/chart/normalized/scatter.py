"""Normalized scatter chart."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dbt_charts.core.compile.models.chart.authored._annotation import Annotation
from dbt_charts.core.compile.models.chart.authored._layer import CartesianLayer
from dbt_charts.core.compile.models.style.authored import ScatterChartStylePatch

from ._base import _CartesianChartFields, _TooltipFields


class ScatterChart(_CartesianChartFields, _TooltipFields):
    """Normalized scatter chart."""

    type: Literal["scatter"]
    size: str | None = Field(default=None, description="Field mapped to point size.")
    shape: str | None = Field(default=None, description="Field mapped to point shape.")
    style: ScatterChartStylePatch | None = Field(
        default=None, description="Chart-local style overrides."
    )
    layers: list[CartesianLayer] = Field(
        default_factory=list, description="Typed overlay layers on this chart."
    )
    annotations: list[Annotation] = Field(
        default_factory=list, description="Annotations drawn on this chart."
    )
