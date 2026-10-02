"""Resolved support_table: the authored entries with each format baked."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.chart.authored._support_table import (
    ChartSupportTableAggregate,
    ChartSupportTablePerSeries,
    ChartSupportTableSource,
)
from dbt_charts.core.compile.models.primitives import ResolvedFormat


class ResolvedSupportTableSource(ChartSupportTableSource):
    format: ResolvedFormat | None = Field(
        default=None, description="Resolved row value format."
    )


class ResolvedSupportTableAggregate(ChartSupportTableAggregate):
    format: ResolvedFormat | None = Field(
        default=None, description="Resolved row value format."
    )


class ResolvedSupportTablePerSeries(ChartSupportTablePerSeries):
    format: ResolvedFormat | None = Field(
        default=None, description="Resolved row value format."
    )


ResolvedSupportTableEntry = (
    ResolvedSupportTableSource
    | ResolvedSupportTableAggregate
    | ResolvedSupportTablePerSeries
)


class ResolvedSupportTable(BaseModel):
    """A chart's support_table with every entry's format final."""

    model_config = ConfigDict(extra="forbid")

    entries: list[ResolvedSupportTableEntry] = Field(
        min_length=1, description="Support-table entries, formats resolved."
    )
