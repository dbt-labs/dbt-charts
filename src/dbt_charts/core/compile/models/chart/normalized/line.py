"""Normalized line chart."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dbt_charts.core.compile.models.chart.authored._annotation import Annotation
from dbt_charts.core.compile.models.chart.authored._layer import CartesianLayer
from dbt_charts.core.compile.models.style.authored import LineChartStylePatch

from ._base import _CartesianChartFields, _TooltipFields


class LineChart(_CartesianChartFields, _TooltipFields):
    """Normalized line chart."""

    type: Literal["line"]
    style: LineChartStylePatch | None = Field(
        default=None, description="Chart-local style overrides."
    )
    layers: list[CartesianLayer] = Field(
        default_factory=list, description="Typed overlay layers on this chart."
    )
    annotations: list[Annotation] = Field(
        default_factory=list, description="Annotations drawn on this chart."
    )
