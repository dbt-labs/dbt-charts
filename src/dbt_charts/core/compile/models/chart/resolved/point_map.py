"""Resolved point_map (and bubble_map) chart model."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from dbt_charts.core.compile.models.style.resolved import ResolvedPointMapStyle

from ._base import _GeoResolvedChartFields


class ResolvedPointMapChart(_GeoResolvedChartFields):
    """Render-ready point map or bubble map."""

    chart_type: Literal["point_map"] = Field(
        description="Discriminator key; always 'point_map'.",
    )
    latitude: str | None = Field(
        default=None,
        description="Latitude data column.",
    )
    longitude: str | None = Field(
        default=None,
        description="Longitude data column.",
    )
    size: str | None = Field(
        default=None,
        description="Quantitative column that scales point area. Mutually exclusive with `collapse`.",
    )
    collapse: bool = Field(
        description="Collapse marks sharing an exact latitude/longitude into one mark sized by count.",
    )
    style: ResolvedPointMapStyle = Field(
        description="Point-map family style slice.",
    )

    # --- Baked geo descriptor (resolved at compile time) ---
    geo_source: str | None = Field(
        default=None,
        description="Named geo boundary source (e.g. 'us-states') for background layer; None = no background.",
    )
    # Fill/stroke extracted from authored basemap config; source already baked into basemap_geo_url.
    basemap_fill: str | None = Field(
        default=None,
        description="Fill color for the background geo layer; None = theme default.",
    )
    basemap_stroke: str | None = Field(
        default=None,
        description="Stroke color for the background geo layer; None = theme default.",
    )
    basemap_geo_url: str | None = Field(
        default=None,
        description="Resolved background geo data URL; None = no background layer.",
    )
    basemap_source_name: str = Field(
        default="",
        description="Registry name of the basemap source; empty for a raw URL.",
    )
    basemap_geo_format: dict[str, Any] | None = Field(
        default=None,
        description="Resolved background geo data format dict; None = no background layer.",
    )
    # Final resolved projection — type name + optional params (center/scale) for city projections.
    geo_projection_type: str = Field(
        default="albersUsa",
        description="Final resolved projection type name.",
    )
    geo_projection_params: dict[str, Any] | None = Field(
        default=None,
        description="Extra projection params (center, scale, …) for city projections; None for simple.",
    )
