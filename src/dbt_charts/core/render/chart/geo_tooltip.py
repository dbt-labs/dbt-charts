"""Geo chart tooltip helpers for the render path."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dbt_charts.core.compile.models.chart.resolved.geoshape import ResolvedGeoshapeChart
from dbt_charts.core.compile.models.chart.resolved.point_map import (
    ResolvedPointMapChart,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import ERR_MAP_LOOKUP_KEY_MISMATCH
from dbt_charts.core.render.chart.spec_builders import tooltip_entry
from dbt_charts.core.render.chart.type_inference import infer_vega_type_from_data
from dbt_charts.core.render.utils import is_integer_key_value
from dbt_charts.core.text.case import field_title


def _sample_lookup_values(
    data: list[dict[str, Any]],
    lookup_field: str,
    limit: int = 5,
) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for row in data:
        if lookup_field not in row or row[lookup_field] is None:
            continue
        rendered = str(row[lookup_field])
        if rendered in seen:
            continue
        seen.add(rendered)
        values.append(rendered)
        if len(values) == limit:
            break
    return values


def validate_map_lookup_key_contract(
    chart: ResolvedGeoshapeChart | ResolvedPointMapChart,
    data: list[dict[str, Any]],
    geo_source: str | None,
    geo_info: Any | None,
    geo_key: str,
    lookup_field: str | None,
) -> None:
    """Reject map lookups whose query keys cannot match a known geo source key.

    Uses static metadata for built-in geo sources. Does not fetch remote
    GeoJSON/TopoJSON during render; browser-side Vega-Lite owns feature
    loading, and render must not become network-dependent.
    """
    if not data or not lookup_field or geo_info is None:
        return

    key_format = geo_info.get("key_format")
    if key_format != "numeric":
        return

    query_samples = _sample_lookup_values(data, lookup_field)
    if not query_samples:
        return

    has_numeric_value = any(
        is_integer_key_value(row.get(lookup_field))
        for row in data
        if lookup_field in row
    )
    if has_numeric_value:
        return

    expected_samples = [str(value) for value in geo_info.get("key_examples", [])]
    raise ChartDataError.from_code(
        ERR_MAP_LOOKUP_KEY_MISMATCH,
        chart_id=chart.id or "unknown",
        geo_source=geo_source or "custom",
        geo_key=geo_key,
        lookup_field=lookup_field,
        expected_format="numeric",
        expected_samples=expected_samples,
        query_samples=query_samples,
    )


def build_point_tooltip_fields(
    data: list[dict[str, Any]],
    *,
    lat_field: str | None = None,
    lng_field: str | None = None,
    size_field: str | None = None,
    color_field: str | None = None,
    max_fields: int = 6,
    tooltip_format: str,
    collapsed: bool = False,
    field_labels: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Build tooltip fields for point and bubble maps.

    ``collapsed=True`` (chart.collapse — the co-located-marks size-by-count
    affordance) omits the raw per-row columns below: VL's `size:
    {aggregate: count}` groups rows by every other non-aggregated encoded
    field, so a raw per-row tooltip field (e.g. a facility name that varies
    within a co-located group) would silently become an extra, unintended
    groupby dimension and break the collapse. The caller adds a `count`
    tooltip entry instead.
    """
    tooltip_fields: list[dict[str, Any]] = []

    if lat_field:
        tooltip_fields.append(
            tooltip_entry(
                lat_field,
                "quantitative",
                title=field_labels.get(
                    lat_field, "Latitude"
                ),  # type-state: silent_fallback — default title
            ),
        )
    if lng_field:
        tooltip_fields.append(
            tooltip_entry(
                lng_field,
                "quantitative",
                title=field_labels.get(
                    lng_field, "Longitude"
                ),  # type-state: silent_fallback — default title
            ),
        )
    if collapsed:
        return tooltip_fields
    if size_field:
        tooltip_fields.append(
            tooltip_entry(
                size_field,
                "quantitative",
                title=field_title(size_field, field_labels),
                format=tooltip_format,
            ),
        )
    if color_field:
        tooltip_fields.append(
            tooltip_entry(
                color_field,
                infer_vega_type_from_data(data, color_field),
                title=field_title(color_field, field_labels),
            ),
        )

    if data:
        for field in data[0]:
            if field in {lat_field, lng_field, size_field, color_field}:
                continue
            tooltip_fields.append(
                tooltip_entry(
                    field,
                    infer_vega_type_from_data(data, field),
                    title=field_title(field, field_labels),
                ),
            )
            if len(tooltip_fields) >= max_fields:
                break

    return tooltip_fields
