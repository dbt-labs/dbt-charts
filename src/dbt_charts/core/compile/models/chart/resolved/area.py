"""Resolved area chart model."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dbt_charts.core.compile.models.chart.resolved._annotation import (
    ResolvedAnnotation,
    ResolvedAnnotationGap,
)
from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLayer
from dbt_charts.core.compile.models.style.resolved import ResolvedAreaStyle

from ._base import _DateAxisResolvedChartFields, _TooltipResolvedField


class ResolvedAreaChart(_DateAxisResolvedChartFields, _TooltipResolvedField):
    """Render-ready area chart."""

    # Narrows base y from str | list[str] | None to str | None — area never
    # carries a list-y at render time after resolve-time normalization.
    y: str | None = Field(default=None, description="Y-axis data column.")
    # Original wide-measure column names when y was authored as a list.
    wide_measures: tuple[str, ...] = Field(
        default=(),
        description="Authored y: list measures, stored after resolve-time normalization.",
    )
    chart_type: Literal["area"] = Field(
        description="Discriminator key; always 'area'.",
    )
    stack: Literal["none", "zero", "normalize", "center"] | None = Field(
        default=None,
        description="Stack mode. 'none' = overlapping fills; None = theme default not yet resolved.",
    )
    style: ResolvedAreaStyle = Field(
        description="Area family style slice — self-contained, globals merged down by resolve.",
    )
    layers: tuple[ResolvedLayer, ...] = Field(
        default=(),
        description="Typed overlay layers, each carrying its own resolved mark style.",
    )
    annotations: tuple[ResolvedAnnotation, ...] = Field(
        default=(),
        description="Rules, bands, notes and trends drawn on the chart.",
    )
    annotation_gaps: tuple[ResolvedAnnotationGap, ...] = Field(
        default=(),
        description="Annotations with a scope that has nothing to draw.",
    )
    annotation_bar_trends: tuple[int, ...] = Field(
        default=(),
        description="Trends that fit a bar mark; a warning input, not authored.",
    )
