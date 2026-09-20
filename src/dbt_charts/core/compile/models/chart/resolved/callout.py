"""Resolved callout chart model."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.chart.resolved._channel import ResolvedStyleChannel
from dbt_charts.core.compile.models.primitives import VariableDependencies
from dbt_charts.core.compile.models.style.resolved.callout import ResolvedCalloutStyle
from dbt_charts.core.compile.models.style.theme.board import PaddingStyle
from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorScale,
)


class ResolvedCalloutChart(BaseModel):
    """Render-ready callout (annotation / error-state) chart.

    Minimal model: no query, no data channels.  Inherits BaseModel directly
    (not _BaseResolvedChartFields) mirroring compiled.CalloutChart.

    Carries query_name=None, notes="", and resolved_channels={} so
    that generic chart-walking code (warning detectors, _wrap_rendered_chart_svg)
    can access these fields without isinstance guards.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chart_type: Literal["callout"] = Field(
        description="Discriminator key; always 'callout'.",
    )
    id: str = Field(description="Unique chart id within the board.")
    source_path: str = Field(
        default="",
        description=(
            "Absolute dotted authoring path of this chart, or '' where compile "
            "decided the subtree has no addressable coordinates. Declared here "
            "rather than inherited: this model deliberately does not extend "
            "_BaseResolvedChartFields, so a field added there does not reach it."
        ),
    )
    defined_in_other_file: bool = Field(
        default=False,
        description=(
            "Internal: True when a ChartRef pulled this definition in from "
            "another file. Declared here rather than inherited: this model "
            "deliberately does not extend _BaseResolvedChartFields."
        ),
    )
    message: str = Field(
        description="Callout message text (required).",
    )
    title: str | None = Field(
        default=None,
        description="Optional callout heading.",
    )
    subtitle: None = Field(
        default=None,
        description=(
            "Always None — callout has no subtitle in its authoring surface. "
            "Present so every ResolvedChart union member carries title/subtitle "
            "and render/chart/session.py can read chart.subtitle directly."
        ),
    )
    variable_dependencies: VariableDependencies = Field(
        description="Variable names this callout depends on.",
    )
    style: ResolvedCalloutStyle = Field(
        description="Callout family style slice.",
    )
    # Structural parity fields — always None/empty for callout.
    # Present so generic chart-iteration code (warning detectors,
    # _wrap_rendered_chart_svg) can access without isinstance guards.
    link: None = Field(
        default=None,
        description="Always None — callout charts are non-navigable annotation messages.",
    )
    query_name: None = Field(
        default=None,
        description="Always None — callout has no query.",
    )
    notes: str = Field(
        default="",
        description="Always empty — callout has no notes. Present so generic "
        "chart-iteration code can access chart.notes without isinstance guards.",
    )
    label: str = Field(
        default="",
        description="Always empty — callout has no KPI label. Present so generic "
        "chart-iteration code can access chart.label without isinstance guards.",
    )
    resolved_channels: dict[str, ResolvedStyleChannel] = Field(
        default_factory=dict,
        description="Always empty — callout has no data channels.",
    )
    layout_padding: PaddingStyle = Field(
        description=(
            "Effective per-chart layout padding, baked in at construction time. "
            "Structural parity with _BaseResolvedChartFields so render_layout_item "
            "can access chart.layout_padding without isinstance guards."
        ),
    )
    palette: tuple[str, ...] = Field(
        default=(),
        description="Always empty — callout has no color encoding. Present so "
        "generic chart-iteration code (warning detectors) can access "
        "chart.palette without isinstance guards.",
    )
    category_colors: tuple[CategoryColorScale, ...] = Field(
        default=(),
        description="Always empty — callout has no color encoding. Present so "
        "generic chart-iteration code (warning detectors) can access "
        "chart.category_colors without isinstance guards.",
    )
    requested_alias_palette: str | None = Field(
        default=None,
        description="Always None — callout has no color encoding. Present so "
        "generic chart-iteration code (warning detectors) can access "
        "chart.requested_alias_palette without isinstance guards.",
    )
    requested_alias_substitute: str | None = Field(
        default=None,
        description="Always None — callout has no color encoding. Present so "
        "generic chart-iteration code (warning detectors) can access "
        "chart.requested_alias_substitute without isinstance guards.",
    )
