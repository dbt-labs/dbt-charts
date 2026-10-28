"""Resolved line chart model."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dbt_charts.core.compile.models.chart.resolved._annotation import (
    ResolvedAnnotation,
    ResolvedAnnotationGap,
)
from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLayer
from dbt_charts.core.compile.models.style.resolved import ResolvedLineStyle

from ._base import _DateAxisResolvedChartFields, _TooltipResolvedField


class ResolvedLineChart(_DateAxisResolvedChartFields, _TooltipResolvedField):
    """Render-ready line chart."""

    # Narrows base y from str | list[str] | None to str | None — line never
    # carries a list-y at render time after resolve-time normalization.
    y: str | None = Field(default=None, description="Y-axis data column.")
    # Original wide-measure column names when y was authored as a list.
    wide_measures: tuple[str, ...] = Field(
        default=(),
        description="Authored y: list measures, stored after resolve-time normalization.",
    )
    chart_type: Literal["line"] = Field(
        description="Discriminator key; always 'line'.",
    )
    style: ResolvedLineStyle = Field(
        description="Line family style slice — self-contained, globals merged down by resolve.",
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
