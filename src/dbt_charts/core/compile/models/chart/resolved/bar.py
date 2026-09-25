"""Resolved bar chart model."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLayer
from dbt_charts.core.compile.models.style.resolved import ResolvedBarStyle

from ._base import _CartesianResolvedChartFields


class ResolvedBarChart(_CartesianResolvedChartFields):
    """Render-ready bar (and histogram) chart — all resolve-time values baked in."""

    # Narrows base y from str | list[str] | None to str | None — bar never
    # carries a list-y at render time; wide measures are normalized at resolve
    # time into wide_measures + WIDE_VALUE_FIELD.
    y: str | None = Field(default=None, description="Y-axis data column.")
    y_start: str | None = Field(
        default=None,
        description="Column each bar starts from; absent, bars start at zero.",
    )
    measure_type: Literal["quantitative", "temporal"] = Field(
        default="quantitative",
        description=(
            "Value-axis data type: temporal only for a bar from one date "
            "column to another."
        ),
    )
    # Original wide-measure column names when y was authored as a list.
    # Empty tuple for single-series charts.  The emitter reads this to call
    # fold_wide_measures instead of a plain y-channel encoding.
    wide_measures: tuple[str, ...] = Field(
        default=(),
        description="Authored y: list measures, stored after resolve-time normalization.",
    )
    chart_type: Literal["bar", "histogram"] = Field(
        description="Discriminator key: 'bar' or 'histogram'.",
    )
    orientation: Literal["vertical", "horizontal"] | None = Field(
        default=None,
        description=(
            "Bar orientation resolved at resolve time from authored config + "
            "data type. None defers to theme default (vertical)."
        ),
    )
    stack: Literal["none", "zero", "normalize", "center"] | None = Field(
        default=None,
        description="Stack mode. 'none' = grouped side-by-side; None = theme default not yet resolved.",
    )
    stacked_domain_max: float | None = Field(
        default=None,
        description=(
            "The measure axis's top edge for a stacked bar: an authored axis "
            "domain if one is set, otherwise the stacked total with headroom. "
            "Used as the axis's domainMax so it spans the full stack. "
            "None for grouped, non-stacked, normalize, or single-series bars."
        ),
    )
    style: ResolvedBarStyle = Field(
        description="Bar family style slice — self-contained, globals merged down by resolve.",
    )
    layers: tuple[ResolvedLayer, ...] = Field(
        default=(),
        description="Typed overlay layers, each carrying its own resolved mark style.",
    )
