"""Resolved KPI chart model."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dbt_charts.core.compile.models.chart.authored import KpiSupportConfig
from dbt_charts.core.compile.models.primitives import ResolvedFormat
from dbt_charts.core.compile.models.style.resolved import ResolvedKpiStyle

from ._base import _BaseResolvedChartFields


class ResolvedKpiSupportConfig(KpiSupportConfig):
    format: ResolvedFormat | None = Field(
        default=None,
        description="Resolved support value format; a date value defaults to date_short when None.",
    )


class ResolvedKpiChart(_BaseResolvedChartFields):
    """Render-ready KPI card chart.

    Inherits _BaseResolvedChartFields directly, not _SharedResolvedChartFields
    — the KPI card has its own label field instead. title/subtitle are always
    None here (the authoring validator rejects them on `type: kpi`); they
    exist purely so every ResolvedChart union member carries the same two
    fields and render/chart/session.py can read chart.title/chart.subtitle
    directly, with no getattr.
    """

    chart_type: Literal["kpi"] = Field(
        description="Discriminator key; always 'kpi'.",
    )
    title: None = Field(
        default=None,
        description="Always None — KPI has no title; use `label` instead.",
    )
    subtitle: None = Field(
        default=None,
        description="Always None — KPI has no subtitle.",
    )
    value: str = Field(
        description="Data column supplying the headline KPI value (required).",
    )
    label: str = Field(
        default="",
        description="KPI card label text rendered below the headline.",
    )
    support: ResolvedKpiSupportConfig | None = Field(
        default=None,
        description="Optional support line below the headline.",
    )
    variant: Literal["stacked", "inline", "compact"] = Field(
        default="stacked",
        description="Layout variant — dispatches to a distinct SVG emit path.",
    )
    format: ResolvedFormat | None = Field(
        default=None,
        description=(
            "Final headline value format -- resolve() has already applied the "
            "narrative-notation and SI-compaction defaults against this "
            "chart's row; render consumes it as-is."
        ),
    )
    format_native: bool = Field(
        default=False,
        description=(
            "True when the authored format was a literal d3 SI spec (not a theme "
            "alias), so render should pass it straight to the formatter and keep "
            "raw d3 SI suffixes (M, k) instead of the house narrative forms (mn, k)."
        ),
    )
    style: ResolvedKpiStyle = Field(
        description="KPI family style slice.",
    )
