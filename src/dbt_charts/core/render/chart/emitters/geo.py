"""Geo chart emitters for render-v2 (geoshape and point_map)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dbt_charts.core.compile.models.chart.resolved.geoshape import ResolvedGeoshapeChart
from dbt_charts.core.compile.models.chart.resolved.point_map import (
    ResolvedPointMapChart,
)
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedGeoshapeChartStyle,
)
from dbt_charts.core.compile.models.style.theme import (
    GeoshapeMarkStyle,
)
from dbt_charts.core.compile.models.style.theme.category_colors import (
    category_scale_for,
    color_at,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import ChartDataset
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.emitters._cartesian import (
    distinct_series_values,
    spatial_color_scale,
)
from dbt_charts.core.render.chart.emitters._channels import (
    apply_color_legend,
    apply_geo_choropleth_legend_endpoint_labels,
    apply_legend_entry_order,
    channel_to_encoding,
    gradient_scale_to_vl,
)
from dbt_charts.core.render.chart.geo_tooltip import (
    build_point_tooltip_fields,
    validate_map_lookup_key_contract,
)
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox
from dbt_charts.core.render.chart.spec_builders import tooltip_entry
from dbt_charts.core.render.chart.type_inference import infer_vega_type_from_data
from dbt_charts.core.render.utils import normalize_data_types
from dbt_charts.core.text.case import field_display_text, field_title

# (min_lat, min_lon, max_lat, max_lon) bounding boxes for albersUsa's three regions.
# Heuristic: rectangles approximate d3-geo's actual albersUsa clip boundaries; they
# will disagree at the edges (e.g. a northern-Mexico point inside the CONUS box that
# d3 still clamps). This trades exactness for simplicity — the filter only matters
# when coordinates are clearly outside the US landmass.
#
# Longitude sign convention: standard decimal degrees (western hemisphere negative).
# The Alaska box [-180, -130] covers the full Aleutian chain including Attu at -172.9°.
# Coordinates stored with the wrong sign convention (e.g. +172.9 to mean 172.9°W)
# represent a distinct eastern-hemisphere location that d3 also clamps to (0,0);
# those are a data-encoding error, not an albersUsa projection limitation.
_ALBERS_USA_REGIONS: tuple[tuple[float, float, float, float], ...] = (
    (24.0, -125.0, 50.0, -66.0),  # CONUS
    (51.0, -180.0, 72.0, -130.0),  # Alaska (full Aleutian chain to Attu at -172.9°)
    (18.0, -162.0, 23.0, -154.0),  # Hawaii
)

# Public — also imported by the POINT_MAP_OUT_OF_PROJECTION warning detector.
# Projections known to have finite, regional bounds checked at emit time.
# Unbounded projections (mercator, equalEarth, …) cover the world — no filter.
BOUNDED_PROJECTIONS: frozenset[str] = frozenset({"albersUsa"})

# Area-proportional size scale for point-map size channels (bubble_map's
# `size:` measure and `collapse`'s count aggregate). VL's circle-mark `size`
# is the mark's rendered AREA in square pixels. A range with a non-zero floor
# (the previous [50, 1000]) makes area an AFFINE function of the domain value,
# not a proportional one — e.g. with a zero-based domain [0, 20] a 20-count
# mark drew only ~10x a 1-count mark's area, not 20x. Anchoring both domain
# and range at zero makes area scale linearly with value: double the value,
# double the area.
#
# domainMin: 0 (not just zero: true) — `zero: true` only EXTENDS an inferred
# domain to include 0 if it doesn't already; it does not CLAMP the floor
# there. A negative measure row would otherwise widen VL's inferred domain
# to [min<0, max], so a value of exactly 0 no longer maps to area 0 (the
# "double the value, double the area" invariant above assumes a domain floor
# pinned at literal 0).
_AREA_PROPORTIONAL_SIZE_RANGE_MAX: float = 1000.0
_AREA_PROPORTIONAL_SIZE_SCALE: VLDict = {
    "range": [0, _AREA_PROPORTIONAL_SIZE_RANGE_MAX],
    "zero": True,
    "domainMin": 0,
}


# Legend tick STRIDE for collapse's count aggregate, as a Vega expression
# evaluated client-side against the runtime-known domain max (`domain('size')
# [1]`) — no Python-side aggregation. `tickMinStep` alone does not shrink
# VL's tick SET for a symbol/size legend, only reformats each generated tick
# — so a small domain (e.g. [0, 2]) still gets half-integer ticks (0.5, 1.5)
# that `format: "d"` then rounds onto the SAME label as a neighboring tick
# (two differently-sized swatches both reading "1"). Generating the tick
# VALUES directly with `sequence(start, stop, step)` at a stride that grows
# with the domain (mirrors d3's 1/2/5/10... ladder, but anchored to always
# divide evenly so no rounding collision is possible) fixes the set, not
# just its formatting.
#
# The stop bound is `ceil(domain_max) + 1`, NOT `+ stride`: `sequence()`
# excludes its own stop, and `+ stride` only clamps correctly when
# domain_max is an exact multiple of stride — max 11 at stride 5 produced
# 5, 10, 15 (15 > 11, a legend advertising a pile bigger than any on the
# map). `+ 1` is exclusive of domain_max+1 itself but inclusive of every
# multiple of stride up to and including domain_max, so the generated set
# can never exceed the real domain.
_COUNT_LEGEND_STRIDE = (
    "domain('size')[1] <= 5 ? 1 "
    ": domain('size')[1] <= 10 ? 2 "
    ": domain('size')[1] <= 25 ? 5 "
    ": domain('size')[1] <= 50 ? 10 "
    ": domain('size')[1] <= 100 ? 20 "
    ": domain('size')[1] <= 250 ? 50 "
    ": domain('size')[1] <= 500 ? 100 "
    ": domain('size')[1] <= 1000 ? 200 "
    ": 500"
)
COUNT_LEGEND_VALUES_SIGNAL: VLDict = {
    "signal": (
        f"sequence(({_COUNT_LEGEND_STRIDE}), "
        f"ceil(domain('size')[1]) + 1, "
        f"({_COUNT_LEGEND_STRIDE}))"
    )
}


def _in_albers_usa(lat: float, lon: float) -> bool:
    """Return True if (lat, lon) falls within any of the three albersUsa regions."""
    return any(
        min_lat <= lat <= max_lat and min_lon <= lon <= max_lon
        for min_lat, min_lon, max_lat, max_lon in _ALBERS_USA_REGIONS
    )


def row_in_projection(
    row: dict[str, Any], lat_field: str, lon_field: str
) -> bool | None:
    """Classify one row against the albersUsa projection bounds.

    Returns:
        True  — coordinates present and inside a known albersUsa region.
        False — coordinates present and outside all regions.
        None  — lat or lon absent or unparseable; caller must decide.

    Public — also imported by the POINT_MAP_OUT_OF_PROJECTION warning detector so
    the parse+guard logic stays in one place.
    """
    lat = row.get(lat_field)
    lon = row.get(lon_field)
    if lat is None or lon is None:
        return None
    try:
        return _in_albers_usa(float(lat), float(lon))
    except (TypeError, ValueError):
        return None


def row_has_negative_size(row: VLDict, size_field: str) -> bool:
    """True if row's size_field value is present, parseable, and strictly < 0.

    A bubble_map's `size:` measure drives mark AREA, which cannot be negative.
    Zero is not negative — a zero-valued row is legitimate data with a
    legitimate area of nothing, and this returns False for it.

    Public — also imported by the WARN_POINT_MAP_NEGATIVE_SIZE_VALUES warning
    detector so the drop predicate has one owner: the emitter filters with it,
    the detector counts with it, and the two counts can never diverge.
    """
    value = row.get(size_field)
    if value is None:
        return False
    try:
        return float(value) < 0
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Geo helpers
# ---------------------------------------------------------------------------


def _geo_format(chart: ResolvedGeoshapeChart) -> dict[str, Any]:
    """Assemble the VL-format dict from baked semantic fields (ADR-011: VL shape is render-local)."""
    fmt: dict[str, Any] = {"type": chart.geo_format_type}
    if chart.geo_feature is not None:
        fmt["feature"] = chart.geo_feature
    if chart.geo_property is not None:
        fmt["property"] = chart.geo_property
    return fmt


def _geo_projection(chart: ResolvedGeoshapeChart) -> dict[str, Any]:
    """Assemble the VL-projection dict from baked semantic fields."""
    proj: dict[str, Any] = {"type": chart.geo_projection_type}
    if chart.geo_projection_params:
        proj.update(chart.geo_projection_params)
    return proj


def _geoshape_mark(
    mark_config: GeoshapeMarkStyle | None,
    fill: str | None = None,
    tooltip: bool = False,
) -> dict[str, Any]:
    """Build a geoshape mark dict matching the oracle's _geoshape_mark output."""
    mark: dict[str, Any] = {"type": "geoshape"}
    if mark_config is not None:
        mark["stroke"] = mark_config.stroke.color if mark_config.stroke else None
        mark["strokeWidth"] = mark_config.stroke.width if mark_config.stroke else None
    if fill is not None:
        mark["fill"] = fill
    if tooltip:
        mark["tooltip"] = True
    return mark


@dataclass
class GeoshapeEmitter:
    def emit(
        self,
        chart: ResolvedGeoshapeChart,
        box: RenderBox,
        dataset: ChartDataset,
    ) -> ChartSpec:
        data = dataset.all_rows()
        # All geo descriptor fields were baked at resolve time (ADR-008).
        geo_data = {"url": chart.geo_url, "format": _geo_format(chart)}
        proj = _geo_projection(chart)

        map_config = chart.style.geoshape
        geoshape_mark_config = map_config.marks.geoshape if map_config else None

        data = normalize_data_types(data)

        color_ch = chart.resolved_channels.get("color")

        # Choropleth when: data + lookup + (explicit value OR any authored color channel).
        if data and chart.lookup_field and (chart.value_field or color_ch is not None):
            return self._emit_choropleth(
                chart=chart,
                data=data,
                geo_data=geo_data,
                geo_key=chart.geo_join_key,
                proj=proj,
                map_config=map_config,
                geoshape_mark_config=geoshape_mark_config,
            )

        # No data — base map with neutral fill.
        mark_props = _geoshape_mark(geoshape_mark_config, tooltip=True)
        if geoshape_mark_config is not None:
            mark_props["fill"] = geoshape_mark_config.fill
        return ChartSpec(
            mark="geoshape",
            geo_data=geo_data,
            projection=proj,
            mark_props={k: v for k, v in mark_props.items() if k != "type"},
            encoding={},
        )

    def _emit_choropleth(
        self,
        *,
        chart: ResolvedGeoshapeChart,
        data: list[dict[str, Any]],
        geo_data: dict[str, Any],
        geo_key: str,
        proj: dict[str, Any],
        map_config: ResolvedGeoshapeChartStyle | None,
        geoshape_mark_config: GeoshapeMarkStyle | None,
    ) -> ChartSpec:
        data_fields = list(data[0].keys())

        # Validate that lookup key values are format-compatible with geo feature keys.
        geo_info_for_validation: dict[str, Any] | None = (
            {
                "key_format": chart.geo_key_format,
                "key_examples": chart.geo_key_examples,
            }
            if chart.geo_key_format
            else None
        )
        validate_map_lookup_key_contract(
            chart,
            data,
            chart.geo_source_name or chart.geo_url,
            geo_info_for_validation,
            geo_key,
            chart.lookup_field,
        )

        # Lookup transform: join user data onto TopoJSON features by geo_key.
        lookup_transform: dict[str, Any] = {
            "lookup": geo_key,
            "from": {
                "data": {"values": data},
                "key": chart.lookup_field,
                "fields": data_fields,
            },
        }

        # Color encoding: value_field_type tracks color_enc's own "type" directly — it drives the
        # tooltip entry below, which must never format the categorical-slot
        # branch's string values ("nominal") with a numeric d3 format (that
        # renders NaN, not the category name).
        value_field_type = "quantitative"
        value_field = chart.value_field
        if value_field is None:
            raise ValueError("value_field must be set at resolve time for choropleth")
        # Nominal vs quantitative is THIS chart's own data shape, never a
        # sibling chart's binding decision -- category_scale_for used to
        # gate the branch directly, so the identical string data
        # rendered as a numeric gradient when this chart was alone on a
        # board, and as a nominal discrete scale once some OTHER chart
        # happened to bind the same field (the two-chart threshold, or
        # an authored pin, neither of which says anything about THIS
        # field's shape). A board scale, when one exists, now only ever
        # supplies the RANGE below -- never whether this renders nominal
        # at all.
        own_type = infer_vega_type_from_data(data, value_field)
        if own_type != "quantitative":
            # A genuinely categorical `color:` field (string values) --
            # paint the choropleth by board slot when bound, else fall
            # back to the same alphabetical-by-palette-position default
            # spatial_color_scale uses for an unbound cartesian series.
            # Distinct from the quantitative gradient path below, which
            # is the ordinary numeric choropleth. `data` is pre-lookup-
            # join, so this chart's own drawn values come straight from
            # it, in row order — same pattern as the pie emitter's
            # wedge/label ink.
            value_field_type = "nominal"
            seen: list[str] = []
            for row in data:
                v = row.get(value_field)
                if isinstance(v, str) and v not in seen:
                    seen.append(v)
            category_scale = category_scale_for(chart.category_colors, value_field)
            if category_scale is not None:
                range_ = [color_at(category_scale, v, chart.palette) for v in seen]
            else:
                range_ = [
                    chart.palette[i % len(chart.palette)] for i in range(len(seen))
                ]
            color_enc = {
                "field": value_field,
                "type": "nominal",
                "title": field_display_text(
                    value_field, chart.field_labels, chart.legend.title.font
                ),
                "scale": {"domain": seen, "range": range_},
            }
            apply_color_legend(color_enc, chart.legend)
            # Only fires when authored, matching every other non-stacked
            # family: `color_enc["scale"]["domain"]` is already
            # unconditionally `seen` (set just above), so there is no
            # Vega alphabetical-fallback bug to close for the
            # unauthored case here either.
            if chart.legend.values is not None:
                apply_legend_entry_order(
                    color_enc,
                    seen,
                    authored=chart.legend.values,
                )
        else:
            _geo_gradient = (
                map_config.color.gradient
                if map_config is not None and map_config.color is not None
                else None
            )
            # No `data`/`field` passed: geoshape's data is pre-lookup-join, so
            # it must never derive a nice-widened domain from it — Omitting
            # `data` is the opt-out signal `gradient_scale_to_vl` reads,
            # matching how _channels.py's bar/line/etc. gradient path (no
            # `data` passed either) already opts out the same way. Domain
            # widening stays unimplemented for geoshape; only the endpoint
            # LABELS get a (safe, client-side) fix below.
            _geo_scale: VLDict = (
                gradient_scale_to_vl(_geo_gradient) if _geo_gradient is not None else {}
            )
            color_enc = {
                "field": value_field,
                "type": "quantitative",
                "title": field_display_text(
                    value_field, chart.field_labels, chart.legend.title.font
                ),
                "scale": _geo_scale,
            }
            # Wire the shared ResolvedLegendStyle styling through, same as
            # heatmap.py — before this, geoshape's legend got whatever VL
            # defaults to instead of our theme's legend style.
            apply_color_legend(color_enc, chart.legend)
            if _geo_gradient is not None:
                # Labels are computed CLIENT-SIDE (a Vega signal reading the
                # scale's own resolved, post-join domain) rather than from
                # Python's pre-join `data` — see the function's docstring for
                # why `apply_gradient_legend_endpoint_labels` (heatmap's
                # server-side version) isn't safe here.
                apply_geo_choropleth_legend_endpoint_labels(color_enc, _geo_gradient)

        # Tooltip encoding (matching oracle field order).
        tooltip_fields: list[dict[str, Any]] = []
        if chart.lookup_field in data_fields:
            tooltip_fields.append(
                tooltip_entry(
                    chart.lookup_field,
                    "nominal",
                    title=field_title(chart.lookup_field, chart.field_labels),
                )
            )
        if value_field != chart.lookup_field:
            tooltip_fields.append(
                tooltip_entry(
                    value_field,
                    value_field_type,
                    title=field_title(value_field, chart.field_labels),
                    format=(
                        chart.style.tooltip_format.spec
                        if value_field_type == "quantitative"
                        else None
                    ),
                )
            )

        choropleth_encoding: dict[str, Any] = {"color": color_enc}
        if tooltip_fields:
            choropleth_encoding["tooltip"] = tooltip_fields

        # Background layer: geo outline with neutral fill, no tooltip.
        bg_fill = (
            geoshape_mark_config.fill if geoshape_mark_config is not None else None
        )
        bg_mark = _geoshape_mark(geoshape_mark_config, fill=bg_fill)
        bg_layer = ChartSpec(
            mark="geoshape",
            geo_data=geo_data,
            mark_props={k: v for k, v in bg_mark.items() if k != "type"},
        )

        # Choropleth overlay: data join + color encoding.
        choropleth_mark = _geoshape_mark(geoshape_mark_config, tooltip=True)
        choropleth_layer = ChartSpec(
            mark="geoshape",
            geo_data=geo_data,
            mark_props={k: v for k, v in choropleth_mark.items() if k != "type"},
            transforms=[lookup_transform],
            encoding=choropleth_encoding,
        )

        return ChartSpec(
            mark="geoshape",
            projection=proj,
            layers=[bg_layer, choropleth_layer],
        )


@dataclass
class PointMapEmitter:
    def emit(
        self,
        chart: ResolvedPointMapChart,
        box: RenderBox,
        dataset: ChartDataset,
    ) -> ChartSpec:
        point_mark = chart.style.point_mark
        data = normalize_data_types(dataset.all_rows())

        # Lat/lon baked at resolve time (ADR-008); use directly.
        lat_field = chart.latitude
        lon_field = chart.longitude

        color_ch = chart.resolved_channels.get("color")
        color_field = color_ch.data_field if color_ch is not None else None
        size_field: str | None = chart.size

        # Projection: baked as type + params at resolve time.
        proj: str | dict[str, Any]
        if chart.geo_projection_params:
            proj = {"type": chart.geo_projection_type, **chart.geo_projection_params}
        else:
            proj = chart.geo_projection_type

        # Drop out-of-projection rows for bounded projections before Vega-Lite sees
        # them. albersUsa maps external coords to (0,0); clip:true removes viewport
        # overflow, but a mark CENTERED at exactly (0,0) still paints its inner
        # quarter. A native VL `filter` transform could also reach zero rendering,
        # but it would duplicate the region bounds as a VL expression string; doing
        # it here keeps the region logic in one place, shared with the warning
        # detector below via `row_in_projection`. The warning detector reads
        # chart_results (pre-filter) so warning counts match exactly what was
        # removed here.
        if chart.geo_projection_type in BOUNDED_PROJECTIONS and lat_field and lon_field:
            data = [
                r
                for r in data
                if row_in_projection(r, lat_field, lon_field) is not False
            ]

        # Mark AREA cannot be negative. A bubble_map's `size:` measure going
        # negative has no honest rendering — the area-proportional scale's
        # zero-anchored domain floor would otherwise clamp it to the smallest
        # visible size, reading as "nearly zero" instead of a large-magnitude
        # negative value. Drop those rows before Vega-Lite sees them, the same
        # way the out-of-projection filter above does; the
        # WARN_POINT_MAP_NEGATIVE_SIZE_VALUES detector counts with the same
        # row_has_negative_size predicate against the pre-filter chart_results,
        # so its count always matches what this filter removes.
        if size_field:
            data = [r for r in data if not row_has_negative_size(r, size_field)]

        # mark_props: size goes into mark when there is no size encoding channel
        # (neither an authored `size:` measure nor `collapse`'s count aggregate).
        # clip: true — VL clips marks straddling the projection's viewport boundary
        # so partial-overlap marks are cleanly clipped (belt-and-suspenders with the
        # data filter above for marks near the edge).
        mark_props: dict[str, Any] = {
            "opacity": point_mark.opacity,
            "tooltip": True,
            "clip": True,
        }
        if size_field is None and not chart.collapse:
            mark_props["size"] = point_mark.size
        if color_ch is None:
            mark_props["fill"] = chart.style.single_series_fill

        encoding: dict[str, Any] = {}
        if lon_field:
            encoding["longitude"] = {"field": lon_field, "type": "quantitative"}
        if lat_field:
            encoding["latitude"] = {"field": lat_field, "type": "quantitative"}

        if size_field:
            encoding["size"] = {
                "field": size_field,
                "type": "quantitative",
                "title": field_display_text(
                    size_field, chart.field_labels, chart.legend.title.font
                ),
                "scale": dict(_AREA_PROPORTIONAL_SIZE_SCALE),
            }
        elif chart.collapse:
            # Native VL aggregation, not a chart-layer transform: `aggregate:
            # count` with no `field` on the size channel is the whole
            # mechanism — VL groups rows by every other non-aggregated
            # encoded field (here, latitude/longitude), so marks sharing an
            # exact coordinate collapse into one, sized by how many rows
            # shared it. No row grouping happens in this Python code.
            encoding["size"] = {
                "aggregate": "count",
                "type": "quantitative",
                "title": "Count",
                "scale": dict(_AREA_PROPORTIONAL_SIZE_SCALE),
                # A count is never fractional, and every shown tick must be
                # unique — see COUNT_LEGEND_VALUES_SIGNAL's docstring for why
                # `format: "d"` alone is not enough.
                "legend": {
                    "format": "d",
                    "values": dict(COUNT_LEGEND_VALUES_SIGNAL),
                },
            }
            # A small pile adjacent to a large one must never be wholly
            # swallowed: with no order channel, VL draws in input-row order,
            # which is arbitrary relative to pile size. Sorting descending by
            # count draws the largest pile first (bottom) and the smallest
            # last (top).
            encoding["order"] = {
                "aggregate": "count",
                "type": "quantitative",
                "sort": "descending",
            }

        if color_ch is not None and color_field is not None:
            enc = channel_to_encoding(color_ch, data)
            enc["title"] = field_display_text(
                color_field, chart.field_labels, chart.legend.title.font
            )
            # A bound field still owes every value its board slot's
            # color — VL's own alphabetical default range would
            # otherwise paint this chart from its own local position,
            # not the board's (mirrors bar/line/scatter's series path).
            if (
                color_ch.mode == "series"
                and enc.get("type") == "nominal"
                and chart.palette
            ):
                scale = category_scale_for(chart.category_colors, color_field)
                if scale is not None:
                    series = distinct_series_values(data, color_field)
                    if series:
                        enc["scale"] = spatial_color_scale(
                            series, chart.palette, series, scale
                        )
            encoding["color"] = enc

        tooltip_format = chart.style.tooltip_format.spec
        tooltip_fields = build_point_tooltip_fields(
            data,
            lat_field=lat_field,
            lng_field=lon_field,
            size_field=size_field,
            color_field=color_field,
            tooltip_format=tooltip_format,
            collapsed=chart.collapse,
            field_labels=chart.field_labels,
        )
        if chart.collapse:
            tooltip_fields.append(
                {"aggregate": "count", "type": "quantitative", "title": "Count"}
            )
        if tooltip_fields:
            encoding["tooltip"] = tooltip_fields

        points_spec = ChartSpec(
            mark="circle",
            mark_props=mark_props,
            encoding=encoding,
            projection=proj,
        )
        # Pre-populate data so BoardRenderSession.emit_chart's fallback
        # (`spec.data = normalize_data_types(data)`) doesn't overwrite with
        # the unfiltered original. This mirrors what the pie emitter does.
        points_spec.data = data

        # Background geo layer when geo_source or basemap is configured.
        if chart.basemap_geo_url is not None and chart.basemap_geo_format is not None:
            bg_geo_data: dict[str, Any] = {
                "url": chart.basemap_geo_url,
                "format": chart.basemap_geo_format,
            }
            # Fill/stroke baked at resolve time from authored basemap config.
            bg_mark_props: dict[str, Any] = {}
            if chart.basemap_fill is not None:
                bg_mark_props["fill"] = chart.basemap_fill
            if chart.basemap_stroke is not None:
                bg_mark_props["stroke"] = chart.basemap_stroke
            bg_layer = ChartSpec(
                mark="geoshape",
                geo_data=bg_geo_data,
                mark_props=bg_mark_props,
            )
            # Use "geoshape" as outer mark so the geoshape composition path handles all layers.
            return ChartSpec(
                mark="geoshape",
                projection=proj,
                layers=[bg_layer, points_spec],
            )

        return points_spec
