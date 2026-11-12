"""Normalized pie / donut chart."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dbt_charts.core.compile.models.chart.authored import ChartTotal
from dbt_charts.core.compile.models.markers import ColumnRef
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.compile.models.style.authored import PieChartStylePatch

from ._base import _FieldLabelsField, _PatternField, _SharedChartFields, _TooltipFields


class PieChart(_SharedChartFields, _FieldLabelsField, _TooltipFields, _PatternField):
    """Normalized pie or donut chart."""

    type: Literal["pie", "donut"]
    is_donut: bool = Field(
        default=False, description="Authored as `type: donut`; styled by `donut`."
    )
    # None until resolve binds it from the query's columns by position.
    theta: ColumnRef | None = Field(
        default=None, description="Field mapped to arc angle."
    )
    color: ColumnRef | None = Field(
        default=None, description="Field mapped to arc color."
    )
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
