"""Normalized spark bar chart."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dbt_charts.core.compile.models.chart.authored import ChartSort
from dbt_charts.core.compile.models.markers import ColumnRef
from dbt_charts.core.compile.models.style.authored import SparkBarChartStylePatch

from ._base import _SharedChartFields


class SparkBarChart(_SharedChartFields):
    """Normalized spark bar chart."""

    type: Literal["spark_bar"]
    x: ColumnRef | None = Field(
        default=None, description="Bar-magnitude (numeric) column name."
    )
    y: ColumnRef | list[ColumnRef] | None = Field(
        default=None, description="Bar-label (category) column name(s)."
    )
    color: ColumnRef | None = Field(default=None, description="Color-encoding field.")
    sort: ChartSort | None = Field(default=None, description="Chart-level sort config.")
    style: SparkBarChartStylePatch | None = Field(
        default=None, description="Chart-local style overrides."
    )
