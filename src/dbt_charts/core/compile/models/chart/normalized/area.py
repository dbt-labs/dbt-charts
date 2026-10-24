"""Normalized area chart."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dbt_charts.core.compile.models.chart.authored._annotation import Annotation
from dbt_charts.core.compile.models.chart.authored._layer import CartesianLayer
from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch

from ._base import _CartesianChartFields


class AreaChart(_CartesianChartFields):
    """Normalized area chart."""

    type: Literal["area"]
    stack: Literal["none", "zero", "normalize", "center"] | None = Field(
        default=None,
        description="Stack mode. 'none' = overlapping; 'zero'/'normalize'/'center' = stacked variants.",
    )
    style: AreaChartStylePatch | None = Field(
        default=None, description="Chart-local style overrides."
    )
    layers: list[CartesianLayer] = Field(
        default_factory=list, description="Typed overlay layers on this chart."
    )
    annotations: list[Annotation] = Field(
        default_factory=list, description="Annotations drawn on this chart."
    )
