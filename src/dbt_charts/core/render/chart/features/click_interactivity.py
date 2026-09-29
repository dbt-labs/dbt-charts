"""ClickInteractivityFeature — href link support for render-v2."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.chart.resolved._base import (
    _BaseResolvedChartFields,
    _CartesianResolvedChartFields,
)
from dbt_charts.core.compile.models.chart.resolved.pie import ResolvedPieChart
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    WIDE_KEY_FIELD,
    WIDE_MEASURE_FAMILIES,
)
from dbt_charts.core.render.chart.feature import chart_rows
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox

# _HREF_SENTINEL is duplicated as a literal in render/converters/chart.py
# (_SENTINEL_PREFIX), which strips it back off the vl_convert output — change both.
_HREF_PLACEHOLDER = re.compile(r"\{\{\s*(x|y|color|theta)\s*\}\}")
_HREF_SENTINEL = "http://dct.invalid"


def _channel_field(chart: ResolvedChart, channel: str) -> str | None:
    """Return the data field name for a given encoding channel."""
    if channel == "x":
        if isinstance(chart, _CartesianResolvedChartFields):
            return chart.x
        return None
    if channel == "y":
        if isinstance(chart, WIDE_MEASURE_FAMILIES):
            # Wide charts use the first authored measure as the y link field;
            # WIDE_VALUE_FIELD is the synthetic fold column, never in raw rows.
            if chart.wide_measures:
                return chart.wide_measures[0]
            return chart.y
        if isinstance(chart, _CartesianResolvedChartFields):
            # Heatmap is the only remaining family that can still carry a
            # list y (its own multi-measure render path, not the fold) —
            # use its first measure as the link field, same convention as
            # the wide bar/line/area branch above.
            y = chart.y
            return y[0] if isinstance(y, list) else y
        return None
    if channel == "color":
        if not isinstance(chart, _BaseResolvedChartFields):
            return None
        # A wide chart folds color into WIDE_LABEL_FIELD's humanized text --
        # a link needs the raw column via WIDE_KEY_FIELD, or downstream SQL
        # matches nothing. An authored dimension `color:` is an untouched
        # column on every row, so read it directly instead.
        if isinstance(chart, WIDE_MEASURE_FAMILIES) and chart.wide_measures:
            return chart.color if chart.color is not None else WIDE_KEY_FIELD
        color_ch = chart.resolved_channels.get("color")
        return color_ch.data_field if color_ch is not None else None
    if channel == "theta":
        if isinstance(chart, ResolvedPieChart):
            return chart.theta
        return None
    return None


def _vega_encode_field(safe_field: str) -> str:
    """Vega expression that percent-encodes a datum field value for URL query strings.

    Chains replace() with /pattern/g regex literals to cover the ASCII delimiter
    set that structurally corrupts a ?col=value query string. Non-ASCII characters
    pass through unescaped; browsers percent-encode them on navigation.

    ``safe_field`` must already have backslashes and single quotes escaped for
    embedding inside a Vega string literal (the ``safe`` variable in
    ``_build_href_calc_expr``).

    % must be encoded first so the %XX output of later substitutions is not
    re-encoded. /pattern/g (global regex) is required — a bare string needle
    replaces only the first occurrence per JS String.replace semantics.
    """
    expr = f"'' + datum['{safe_field}']"
    for pattern, pct in [
        ("/%/g", "%25"),
        ("/ /g", "%20"),
        ("/&/g", "%26"),
        ("/=/g", "%3D"),
        ("/\\+/g", "%2B"),
        ("/#/g", "%23"),
        ("/\\?/g", "%3F"),
        ("/\\//g", "%2F"),
    ]:
        expr = f"replace({expr}, {pattern}, '{pct}')"
    return expr


def _build_href_calc_expr(
    template: str,
    chart: ResolvedChart,
    encoding: dict[str, Any],
    data: list[dict[str, Any]] | None = None,
) -> str:
    """Build a Vega calculate expression for the href link template.

    Temporal fields use ``timeFormat(datum['field'], '%Y-%m-%d')`` so URL params
    are ISO date strings rather than ms timestamps. Non-temporal fields are
    percent-encoded via ``_vega_encode_field`` so values like 'A&B' or 'north
    east' produce structurally valid query-string parameters. Relative URLs are
    prefixed with the sentinel to prevent vl_convert mangling.

    Bar charts force ``ordinal`` on the x encoding even for authored-temporal axes
    (continuous temporal scale breaks bar layout). We fall back to data inference
    so fields carrying actual ISO date strings still use ``timeFormat`` in links.
    """
    from dbt_charts.core.render.chart.type_inference import infer_vega_type_from_data

    # Detect temporal fields from ChartSpec encoding; fall back to data inference
    # for ordinal fields that may have been downgraded from temporal by the emitter.
    temporal_fields: set[str] = set()
    for enc_val in encoding.values():
        if not isinstance(enc_val, dict):
            continue
        field_name = enc_val.get("field")
        if not field_name:
            continue
        if enc_val.get("type") == "temporal":
            temporal_fields.add(field_name)
        elif data and enc_val.get("type") == "ordinal":
            if infer_vega_type_from_data(data, field_name) == "temporal":
                temporal_fields.add(field_name)

    parts = _HREF_PLACEHOLDER.split(template)
    channels_found = _HREF_PLACEHOLDER.findall(template)

    expr_parts: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            if part:
                expr_parts.append(
                    "'" + part.replace("\\", "\\\\").replace("'", "\\'") + "'"
                )
        else:
            channel = channels_found[i // 2]
            field = _channel_field(chart, channel)
            if field is None:
                raise ValueError(
                    f"link template references channel '{{{{ {channel} }}}}' but chart "
                    f"has no '{channel}' encoding assigned"
                )
            safe = field.replace("\\", "\\\\").replace("'", "\\'")
            if field in temporal_fields:
                expr_parts.append(f"timeFormat(datum['{safe}'], '%Y-%m-%d')")
            else:
                expr_parts.append(_vega_encode_field(safe))

    expr = " + ".join(expr_parts) if expr_parts else "''"

    if template.startswith("?") or template.startswith("/"):
        return f"'{_HREF_SENTINEL}' + {expr}"
    return expr


@dataclass
class ClickInteractivityFeature:
    """Set spec.href_link from chart.link template when chart has a link defined."""

    def applies_to(self, chart: ResolvedChart) -> bool:
        return isinstance(chart, _BaseResolvedChartFields) and chart.link is not None

    def apply(
        self,
        spec: ChartSpec,
        chart: ResolvedChart,
        box: RenderBox,
        datasets: dict[str | None, list[dict[str, Any]]],
    ) -> ChartSpec:
        if not isinstance(chart, _BaseResolvedChartFields) or not chart.link:
            return spec
        data = chart_rows(chart, datasets).all_rows()
        spec.href_link = _build_href_calc_expr(chart.link, chart, spec.encoding, data)
        return spec
