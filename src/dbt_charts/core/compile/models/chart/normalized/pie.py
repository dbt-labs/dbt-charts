"""Normalized pie / donut chart."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dbt_charts.core.compile.models.chart.authored import ChartTotal
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.compile.models.style.authored import PieChartStylePatch

from ._base import _FieldLabelsField, _SharedChartFields


class PieChart(_SharedChartFields, _FieldLabelsField):
    """Normalized pie or donut chart."""

    type: Literal["pie", "donut"]
    theta: str = Field(description="Field mapped to arc angle (required).")
    color: str | None = Field(default=None, description="Field mapped to arc color.")
    total: ChartTotal | None = Field(
        default=None, description="Donut center total config."
    )
    format: str | FormatConfig | None = Field(
        default=None, description="Number format override."
    )
    height: int | float | None = Field(default=None, description="Chart height.")
    width: int | float | None = Field(default=None, description="Chart width.")
    style: PieChartStylePatch | None = Field(
        default=None, description="Chart-local style overrides."
    )
