"""Resolved geoshape (and map) chart model."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from dbt_charts.core.compile.models.style.resolved import ResolvedGeoshapeStyle

from ._base import _GeoResolvedChartFields


class ResolvedGeoshapeChart(_GeoResolvedChartFields):
    """Render-ready geoshape (choropleth) or map chart.

    Geo descriptor fields are baked at resolve time (compile layer) so the
    render layer needs no config reach-back.
    """

    chart_type: Literal["geoshape"] = Field(
        description="Discriminator key; always 'geoshape'.",
    )
    style: ResolvedGeoshapeStyle = Field(
        description="Geoshape family style slice.",
    )

    # --- Baked geo descriptor (resolved at compile time) ---
    geo_url: str = Field(
        default="",
        description="Resolved geo data URL.",
    )
    geo_format_type: Literal["topojson", "geojson", "json"] = Field(
        default="topojson",
        description="Resolved geo data format type.",
    )
    geo_feature: str | None = Field(
        default=None,
        description="Resolved TopoJSON feature name (None = omit from VL format dict).",
    )
    geo_property: str | None = Field(
        default=None,
        description="Resolved GeoJSON property filter (None = omit from VL format dict).",
    )
    geo_join_key: str = Field(
        default="id",
        description="Resolved feature join key (config 'key' field, else 'id').",
    )
    # Final resolved projection — type name + optional params (center/scale) for complex projections.
    geo_projection_type: str = Field(
        default="mercator",
        description="Final resolved projection type name.",
    )
    geo_projection_params: dict[str, Any] | None = Field(
        default=None,
        description="Extra projection params (center, scale, …) for complex projections; None for simple.",
    )

    # --- Choropleth join fields ---
    lookup_field: str | None = Field(
        default=None,
        description="Data column joined against geographic feature keys.",
    )
    value_field: str | None = Field(
        default=None,
        description="Data column mapped to fill color (choropleth value).",
    )

    # --- Key validation metadata (baked from geo config at resolve time) ---
    geo_key_format: str = Field(
        default="",
        description="Feature key format ('numeric' = expects integer-like values); '' = no format constraint.",
    )
    geo_key_examples: list[str] = Field(
        default_factory=list,
        description="Example geo feature key values surfaced in ChartDataError messages.",
    )
    # Source name (e.g. "world-countries") for user-facing error messages; empty for raw-URL sources.
    geo_source_name: str = Field(
        default="",
        description="Geo source registry key (e.g. 'world-countries'); empty when a raw URL was used.",
    )
