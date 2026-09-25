"""Normalized bar / histogram chart."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dbt_charts.core.compile.models.chart.authored._layer import BarChartLayer
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

from ._base import _CartesianChartFields


class BarChart(_CartesianChartFields):
    """Normalized bar or histogram chart."""

    type: Literal["bar", "histogram"]
    y_start: str | None = Field(
        default=None,
        description="Column each bar starts from; absent, bars start at zero.",
    )
    stack: Literal["none", "zero", "normalize", "center"] | None = Field(
        default=None,
        description="Stack mode. 'none' = grouped side-by-side; 'zero'/'normalize'/'center' = stacked variants.",
    )
    style: BarChartStylePatch | None = Field(
        default=None, description="Chart-local style overrides."
    )
    layers: list[BarChartLayer] = Field(
        default_factory=list, description="Typed overlay layers on this chart."
    )
