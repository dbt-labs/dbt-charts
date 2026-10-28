"""Normalized KPI chart."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from dbt_charts.core.compile.models.chart.authored import KpiSupportConfig
from dbt_charts.core.compile.models.style.authored import KpiChartStylePatch

from ._base import _BaseChartFields


class KpiChart(_BaseChartFields):
    """Normalized KPI chart.

    Inherits _BaseChartFields directly — KPI uses label: instead
    of title:/subtitle:, mirroring authored KpiChart.
    """

    type: Literal["kpi"]
    value: str = Field(
        description="Column reference for the headline number (required)."
    )
    label: str = Field(
        default="", description="Text rendered above the headline value."
    )
    support: KpiSupportConfig | None = Field(
        default=None, description="Support row beneath the headline value."
    )
    glyph: str | None = Field(
        default=None, description="Text shown before the headline value."
    )
    good: Literal["up", "down"] | None = Field(
        default=None,
        description="Which direction of the headline value is good; drives its glyph and tone.",
    )
    background: str | dict[str, Any] | None = Field(
        default=None,
        description=(
            "KPI gradient background channel — {column, scale} shape. "
            "Parsed at render time via parse_style_channel."
        ),
    )
    style: KpiChartStylePatch | None = Field(
        default=None, description="Chart-local style overrides."
    )
