"""Resolved scatter chart model."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dbt_charts.core.compile.models.chart.resolved._annotation import (
    ResolvedAnnotation,
    ResolvedAnnotationGap,
)
from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLayer
from dbt_charts.core.compile.models.primitives import ResolvedFormat
from dbt_charts.core.compile.models.style.resolved import ResolvedScatterStyle

from ._base import _CartesianResolvedChartFields, _TooltipResolvedField


class ResolvedScatterChart(_CartesianResolvedChartFields, _TooltipResolvedField):
    """Render-ready scatter chart."""

    # Narrows base y from str | list[str] | None to str | None — scatter
    # never carries a list-y at render time; wide measures are normalized at
    # resolve time into wide_measures + WIDE_VALUE_FIELD, same as
    # ResolvedBarChart/ResolvedAreaChart/ResolvedLineChart. Unlike heatmap
    # (which keeps the base's wider type for its own, different
    # one-rect-layer-per-measure path).
    y: str | None = Field(default=None, description="Y-axis data column.")
    # Original wide-measure column names when y was authored as a list.
    # Empty tuple for single-series charts. The emitter reads this to call
    # fold_wide_measures instead of a plain y-channel encoding.
    wide_measures: tuple[str, ...] = Field(
        default=(),
        description="Authored y: list measures, stored after resolve-time normalization.",
    )
    chart_type: Literal["scatter"] = Field(
        description="Discriminator key; always 'scatter'.",
    )
    x_tooltip_format: ResolvedFormat | None = Field(
        default=None,
        description=(
            "Tooltip format for a numeric x the query names a format for; None "
            "when x takes the measure's digit spec."
        ),
    )
    size: str | None = Field(
        default=None,
        description="Data column driving point size.",
    )
    shape: str | None = Field(
        default=None,
        description="Data column driving point shape.",
    )
    style: ResolvedScatterStyle = Field(
        description="Scatter family style slice.",
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
