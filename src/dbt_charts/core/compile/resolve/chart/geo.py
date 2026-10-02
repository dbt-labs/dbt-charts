"""Geoshape and point-map chart resolvers and geo field detection."""

from __future__ import annotations

import dataclasses
from typing import Any

from dbt_charts.core.compile.config import get_config
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.format import (
    resolve_format_parts,
    resolve_format_parts_for_values,
)
from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.chart.normalized import (
    GeoshapeChart,
    PointMapChart,
)
from dbt_charts.core.compile.models.chart.normalized._base import (
    _GeoChartFields,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedGeoshapeChart,
    ResolvedPointMapChart,
)
from dbt_charts.core.compile.models.config import ConfigNode
from dbt_charts.core.compile.models.primitives import bake_scale_target_stops
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedGeoshapeChartStyle,
    ResolvedGeoshapeStyle,
    ResolvedPointMapStyle,
    ResolvedStaticGradientColorStyle,
)
from dbt_charts.core.compile.models.style.theme import GeoshapeChartStyle
from dbt_charts.core.compile.models.vega_lite.contracts import Projection
from dbt_charts.core.compile.resolve.chart._channels import (
    _channels_for,
    _column_numeric_values,
)
from dbt_charts.core.compile.resolve.chart._kwargs import (
    _EMPTY_CHART_TEXT_VARIABLES,
    AutomaticLinkCandidate,
    ChartTextVariables,
    _base_kwargs,
    _geo_kwargs,
    _title_font,
)
from dbt_charts.core.compile.resolve.chart._marks import _resolved_point_mark
from dbt_charts.core.compile.resolve.chart._palette import (
    _effective_requested_alias_palette,
    _effective_single_series_fill,
    _with_color_tokens,
)
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_FORMAT_AFFIX_GEO_TOOLTIP_UNSUPPORTED,
)

__all__ = [
    "_resolve_geo_projection",
    "_resolve_geo_source_fields",
    "_resolve_geoshape",
    "_resolve_point_map",
]


# Lat/lon field name candidates for auto-detection when chart.latitude/longitude is None.
# Baked at resolve time (ADR-008): emitter reads chart.latitude/longitude, never re-derives.
_LAT_CANDIDATES: tuple[str, ...] = ("latitude", "lat", "Latitude", "Lat", "y")


_LON_CANDIDATES: tuple[str, ...] = (
    "longitude",
    "lng",
    "lon",
    "Longitude",
    "Lng",
    "Lon",
    "x",
)


@dataclasses.dataclass(frozen=True)
class _GeoSourceFields:
    """Baked geo-source descriptor fields — output of _resolve_geo_source_fields."""

    geo_url: str
    geo_format_type: str
    geo_feature: str | None
    geo_property: str | None
    geo_join_key: str
    geo_key_format: str
    geo_key_examples: tuple[str, ...]
    # Registry key name (e.g. "world-countries") for user-facing error messages.
    geo_source_name: str


def _resolve_geo_format_fields(
    geo_info: ConfigNode | None,
    geo_feature_override: str | None,
) -> tuple[str, str | None, str | None]:
    """Return (geo_format_type, resolved_feature, resolved_property) from geo_info.

    When geo_info is None (raw-URL source), falls back to topojson/"features"/None
    — matching the oracle behavior for unknown sources.
    """
    if geo_info is not None:
        fmt_type: str = str(geo_info.get("format", "topojson"))
        resolved_feature: str | None = geo_feature_override or geo_info.get("feature")
        resolved_property: str | None = geo_info.get("property")
    else:
        fmt_type = "topojson"
        # Unknown source used as raw URL: fall back to "features" (matches oracle).
        resolved_feature = (
            geo_feature_override if geo_feature_override is not None else "features"
        )
        resolved_property = None
    geo_format_type: str = (
        fmt_type if fmt_type in ("topojson", "geojson", "json") else "topojson"
    )
    return geo_format_type, resolved_feature, resolved_property


def _resolve_geo_join_key(explicit_key: str | None, geo_info: ConfigNode | None) -> str:
    """Resolve the geographic join key: explicit override → registry key → "id"."""
    if explicit_key is not None:
        return explicit_key
    if geo_info is not None and geo_info.get("key"):
        return str(geo_info.get("key"))
    return "id"


def _resolve_geo_source_fields(
    geo_info: ConfigNode | None,
    geo_source: str | None,
    geo_feature_override: str | None,
    explicit_key: str | None,
) -> _GeoSourceFields:
    """Resolve URL, format, feature, property, and join-key from a geo source.

    Raises CompilationError when both geo_source and geo_info are absent — a
    geoshape with no URL renders nothing and should fail at compile time.
    """
    if geo_info is not None:
        geo_url: str = str(geo_info["url"])
        # When a registry entry exists, the geo_source key is the friendly name.
        geo_source_name: str = geo_source or ""
    elif geo_source is not None:
        geo_url = geo_source
        # Raw URL source — no friendly name available.
        geo_source_name = ""
    else:
        raise CompilationError(
            "geoshape chart requires a geo_source or geo.source; none provided"
        )

    geo_format_type, resolved_feature, resolved_property = _resolve_geo_format_fields(
        geo_info, geo_feature_override
    )
    geo_key_format: str = (
        str(geo_info["key_format"]) if geo_info and geo_info.get("key_format") else ""
    )
    geo_key_examples: tuple[str, ...] = (
        tuple(str(k) for k in geo_info.get("key_examples", [])) if geo_info else ()
    )
    return _GeoSourceFields(
        geo_url=geo_url,
        geo_format_type=geo_format_type,
        geo_feature=resolved_feature,
        geo_property=resolved_property,
        geo_join_key=_resolve_geo_join_key(explicit_key, geo_info),
        geo_key_format=geo_key_format,
        geo_key_examples=geo_key_examples,
        geo_source_name=geo_source_name,
    )


def _resolve_geo_projection(
    explicit_proj: str | Projection | None,
    geo_info: ConfigNode | None,
) -> tuple[str, dict[str, Any] | None]:
    """Resolve (proj_type, proj_params) for a geoshape.

    Precedence: explicit str/Projection > geo_source registry default > "mercator".
    """
    if isinstance(explicit_proj, str):
        return explicit_proj, None
    if isinstance(explicit_proj, Projection):
        # model_dump for forward-compat if Projection fields expand beyond `type`.
        proj_dict = explicit_proj.model_dump(exclude_none=True)
        proj_type = str(proj_dict.pop("type", "mercator"))
        return proj_type, proj_dict if proj_dict else None
    # No explicit projection: fall back to the registry default.
    if geo_info is not None:
        raw_default = geo_info.get("projection", "mercator")
        default_proj: str | dict[str, Any] = (
            raw_default.to_plain_dict(exclude_none=True)
            if hasattr(raw_default, "to_plain_dict")
            else raw_default
        )
    else:
        default_proj = "mercator"
    if isinstance(default_proj, dict):
        remainder = {k: v for k, v in default_proj.items() if k != "type"}
        return str(default_proj.get("type", "mercator")), (
            remainder if remainder else None
        )
    return str(default_proj) if default_proj else "mercator", None


def _resolve_choropleth_value_field(
    normalized: _GeoChartFields, channels: dict[str, Any]
) -> str | None:
    """Choropleth value field: explicit value → channel-resolved color.

    Replicates the oracle's effective_color_field: a literal-mode color channel
    (e.g. a CSS color string) does not count as a value field.
    """
    color_ch = channels.get("color")
    color_field = (
        color_ch.data_field
        if color_ch is not None and color_ch.mode != "literal"
        else None
    )
    return normalized.value or color_field


def _geoshape_kwargs(
    normalized: _GeoChartFields,
    channels: dict[str, Any],
    variables: ChartTextVariables,
    chart_style_context: ChartStyleContext,
) -> dict[str, Any]:
    """Bake all geoshape geo-descriptor fields at resolve time (ADR-008).

    Performs the single config lookup here (compile layer) so the render layer
    needs no compile.config reach-back.
    """
    # --- Extract geo_source / geo_feature / explicit_key from normalized.geo ---
    geo = normalized.geo
    geo_source = normalized.geo_source
    geo_feature_override: str | None = None
    explicit_key: str | None = None
    if isinstance(geo, str):
        # String form of geo IS the source name (model: "Named geographic data source").
        geo_source = geo_source or geo
    elif isinstance(geo, dict):
        geo_source = geo.get("source", geo_source)
        geo_feature_override = geo.get("feature")
        explicit_key = geo.get("key")

    geo_sources = get_config().geo_sources
    geo_info = geo_sources.get(geo_source) if geo_source else None
    fields = _resolve_geo_source_fields(
        geo_info, geo_source, geo_feature_override, explicit_key
    )
    proj_type, proj_params = _resolve_geo_projection(normalized.projection, geo_info)

    # --- Choropleth fields ---
    lookup_field = normalized.lookup
    value_field = _resolve_choropleth_value_field(normalized, channels)

    return {
        **_geo_kwargs(normalized, chart_style_context, variables),
        "geo_url": fields.geo_url,
        "geo_format_type": fields.geo_format_type,
        "geo_feature": fields.geo_feature,
        "geo_property": fields.geo_property,
        "geo_join_key": fields.geo_join_key,
        "geo_projection_type": proj_type,
        "geo_projection_params": proj_params,
        "lookup_field": lookup_field,
        "value_field": value_field,
        "geo_key_format": fields.geo_key_format,
        "geo_key_examples": list(fields.geo_key_examples),
        "geo_source_name": fields.geo_source_name,
    }


def _with_baked_color_gradient(
    geoshape: GeoshapeChartStyle,
) -> ResolvedGeoshapeChartStyle:
    """Construct a ResolvedGeoshapeChartStyle with the gradient baked.

    Threads the resolved subtype down the existing nesting so the baked
    gradient's resolved_stops survive pydantic serialization (board artifact
    round-trip). `emitters/geo.py` reads `chart.style.geoshape.color.gradient`
    from the same path — the access is unchanged, now provably resolved.
    """
    if geoshape.color is None or geoshape.color.gradient is None:
        return ResolvedGeoshapeChartStyle.model_validate(
            geoshape.model_dump(exclude_none=True)
        )
    baked = bake_scale_target_stops(geoshape.color.gradient)
    resolved_color = ResolvedStaticGradientColorStyle.model_validate(
        {
            **geoshape.color.model_dump(exclude_none=True),
            "gradient": baked.model_dump(exclude_unset=True),
        }
    )
    return ResolvedGeoshapeChartStyle.model_validate(
        {
            **geoshape.model_dump(exclude_none=True),
            "color": resolved_color.model_dump(exclude_unset=True),
        }
    )


def _reject_geo_tooltip_affix(
    chart_style_context: ChartStyleContext, chart_id: str, chart_type: str
) -> None:
    """Raise when the tooltip format carries an affix a geo painter cannot compose.

    Geo charts use a VL-native ``encoding.tooltip`` whose ``format`` is a plain
    d3 string. ``style.tooltip.format`` is chart-type-agnostic, so only resolve
    knows the consuming chart type.
    """
    fmt = chart_style_context.tooltip.format
    if resolve_format_parts(
        fmt, chart_style_context.formats, no_format_default=None
    ).has_affix:
        raise CompilationError.from_code(
            ERR_FORMAT_AFFIX_GEO_TOOLTIP_UNSUPPORTED,
            field_path=f"charts.{chart_id}.style.tooltip.format",
            chart_type=chart_type,
        )


def _resolve_geoshape(
    normalized: GeoshapeChart,
    data: list[dict[str, Any]],
    chart_style_context: ChartStyleContext,
    width: float,
    automatic_link_candidate: AutomaticLinkCandidate,
    variables: ChartTextVariables = _EMPTY_CHART_TEXT_VARIABLES,
) -> ResolvedGeoshapeChart:
    _reject_geo_tooltip_affix(chart_style_context, normalized.id, "geoshape")
    chart_local_style_context = build_chart_style_context(
        chart_style_context, normalized
    )
    primary = _with_color_tokens(normalized.style, chart_style_context)
    geoshape = merge_onto_base(chart_style_context.geoshape, primary)
    channels = _channels_for(normalized, data)
    _tf = _title_font(normalized, chart_local_style_context, width)
    # The tooltip formats chart.value_field (emitters/geo.py:357-364), which
    # is normalized.value when authored, else the color channel
    # (_resolve_choropleth_value_field) -- vote on that same field, not the
    # color channel directly, or an authored `value:` silently outvotes it.
    tooltip_format_values = _column_numeric_values(
        data, _resolve_choropleth_value_field(normalized, channels)
    )
    return ResolvedGeoshapeChart(
        **_base_kwargs(
            normalized,
            chart_style_context,
            channels,
            geoshape.legend,
            requested_alias_palette=_effective_requested_alias_palette(
                chart_style_context, primary
            ),
            automatic_link_candidate=automatic_link_candidate,
            layout_padding=geoshape.padding,
        ),
        **_geoshape_kwargs(normalized, channels, variables, chart_local_style_context),
        chart_type="geoshape",
        style=ResolvedGeoshapeStyle(
            geoshape=_with_baked_color_gradient(geoshape),
            scatter=chart_style_context.scatter,
            tooltip_format=resolve_format_parts_for_values(
                chart_style_context.tooltip.format,
                chart_style_context.formats,
                tooltip_format_values,
                no_format_default=None,
            ).spec,
            title_font=_tf,
        ),
    )


def _resolve_point_map(
    normalized: PointMapChart,
    data: list[dict[str, Any]],
    chart_style_context: ChartStyleContext,
    width: float,
    automatic_link_candidate: AutomaticLinkCandidate,
    variables: ChartTextVariables,
) -> ResolvedPointMapChart:
    _reject_geo_tooltip_affix(chart_style_context, normalized.id, "point_map")
    chart_local_style_context = build_chart_style_context(
        chart_style_context, normalized
    )
    primary = _with_color_tokens(normalized.style, chart_style_context)
    point_map = merge_onto_base(chart_style_context.point_map, primary)
    channels = _channels_for(normalized, data)
    # geo_tooltip.py formats only size_field with tooltip_format -- the
    # color entry gets no format at all, so voting on color would drag an
    # unformatted column's register onto size. A negative size row never
    # paints either (emitters/geo.py's row_has_negative_size filter drops it
    # before Vega-Lite sees it, mark AREA cannot be negative), so it must
    # not vote.
    tooltip_format_values = [
        v for v in _column_numeric_values(data, normalized.size) if v >= 0
    ]

    # Lat/lon: authored value takes precedence; auto-detect from data column names as fallback.
    # Baked here (ADR-008) so the emitter reads chart.latitude/longitude without re-deriving.
    lat_field: str | None = normalized.latitude or (
        next((f for f in _LAT_CANDIDATES if data and f in data[0]), None)
        if data
        else None
    )
    lon_field: str | None = normalized.longitude or (
        next((f for f in _LON_CANDIDATES if data and f in data[0]), None)
        if data
        else None
    )

    # Resolve background geo layer (geo_source or basemap.source).
    geo_sources = get_config().geo_sources
    basemap = normalized.basemap
    raw_geo_source: str | None = normalized.geo_source
    bg_source: str | None = (
        basemap.source
        if basemap is not None and basemap.source is not None
        else raw_geo_source
    )
    bg_geo_info = geo_sources.get(bg_source) if bg_source else None
    basemap_geo_url: str | None = None
    basemap_geo_format: dict[str, Any] | None = None
    if bg_source is not None:
        bg_url: str = str(bg_geo_info["url"]) if bg_geo_info else bg_source
        basemap_geo_url = bg_url
        bg_fmt_type, bg_feature, bg_property = _resolve_geo_format_fields(
            bg_geo_info, None
        )
        bg_fmt: dict[str, Any] = {"type": bg_fmt_type}
        if bg_feature is not None:
            bg_fmt["feature"] = bg_feature
        if bg_property is not None:
            bg_fmt["property"] = bg_property
        basemap_geo_format = bg_fmt

    # Bake basemap fill/stroke from the authored basemap (source already folded into basemap_geo_url).
    basemap_fill: str | None = basemap.fill if basemap is not None else None
    basemap_stroke: str | None = basemap.stroke if basemap is not None else None
    # Fall back to the theme's neutral geoshape fill when a background layer is
    # present but no explicit fill was authored; without this VL defaults to its
    # own fill (#4c78a8) which is never the intent for a geographic basemap.
    if basemap_fill is None and basemap_geo_url is not None:
        basemap_fill = chart_style_context.marks.geoshape.fill

    # Resolve projection: explicit author value > geo_source registry default > "albersUsa".
    # _resolve_geo_projection falls back to "mercator" when no geo_info; point_map uses "albersUsa".
    proj_type, proj_params = _resolve_geo_projection(normalized.projection, bg_geo_info)
    if normalized.projection is None and bg_geo_info is None:
        proj_type = "albersUsa"
        proj_params = None

    geo_kw = _geo_kwargs(normalized, chart_local_style_context, variables)
    # Keep projection on the shared geo field (used by existing callers).
    geo_kw["projection"] = (
        normalized.projection if normalized.projection is not None else "albersUsa"
    )

    _tf = _title_font(normalized, chart_local_style_context, width)
    return ResolvedPointMapChart(
        **_base_kwargs(
            normalized,
            chart_style_context,
            channels,
            point_map.legend,
            requested_alias_palette=_effective_requested_alias_palette(
                chart_style_context, primary
            ),
            automatic_link_candidate=automatic_link_candidate,
            layout_padding=point_map.padding,
        ),
        **geo_kw,
        chart_type="point_map",
        latitude=lat_field,
        longitude=lon_field,
        size=normalized.size,
        collapse=normalized.collapse,
        geo_source=raw_geo_source,
        basemap_fill=basemap_fill,
        basemap_stroke=basemap_stroke,
        basemap_geo_url=basemap_geo_url,
        basemap_geo_format=basemap_geo_format,
        geo_projection_type=proj_type,
        geo_projection_params=proj_params,
        style=ResolvedPointMapStyle(
            point_map=point_map,
            # point_map's own marks.point slot (not scatter's) — it cascades
            # from the same Style.charts.marks global default but also picks
            # up point_map-family and chart-local overrides, which the
            # scatter family's unmerged slot does not.
            point_mark=_resolved_point_mark(
                point_map.marks.point, chart_style_context.formats
            ),
            # Geo families are not slot-eligible in single_series_allocation
            # (that walk is cartesian-only), so they always take the first
            # single-series stop rather than a rhythm slot.
            single_series_fill=_effective_single_series_fill(
                chart_style_context, primary
            ),
            tooltip_format=resolve_format_parts_for_values(
                chart_style_context.tooltip.format,
                chart_style_context.formats,
                tooltip_format_values,
                no_format_default=None,
            ).spec,
            title_font=_tf,
        ),
    )
