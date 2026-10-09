"""Compose a format's affix on the hover card of a chart with no structured tooltip.

Such a chart's card is Vega-Lite's own mark description, built from each
channel's plain d3 ``format``, which has no place for a prefix or suffix (a
money name's currency included). When a measure's format carries one, this
feature writes the description itself: one ``title: value`` row per channel, in
the encoding's order, the measure composed like every other affix.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.models.chart.resolved.geoshape import (
    ResolvedGeoshapeChart,
)
from dbt_charts.core.compile.models.chart.resolved.heatmap import ResolvedHeatmapChart
from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
from dbt_charts.core.compile.models.chart.resolved.point_map import (
    ResolvedPointMapChart,
)
from dbt_charts.core.compile.models.chart.resolved.scatter import ResolvedScatterChart
from dbt_charts.core.compile.models.primitives import ResolvedFormat
from dbt_charts.core.compile.resolve.chart._wide_fields import WIDE_VALUE_FIELD
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.emitters._tooltip import format_quantitative_expr
from dbt_charts.core.render.chart.features.structured_tooltip import (
    StructuredTooltipFeature,
)
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox
from dbt_charts.core.text.format_d3 import is_d3_si_spec
from dbt_charts.core.text.numeral_scale import sub_unit_digit_format

# Channels Vega-Lite's own description lists; the rest carry no row.
_DESCRIBED = ("x", "y", "theta", "color", "size", "shape", "opacity")
# Vega-Lite's names for a binned field's edges and the count aggregate.
_BIN_PREFIX = "bin_maxbins_10_"
_COUNT_FIELD = "__count"
# Vega-Lite's date text for a temporal channel with no format.
_DEFAULT_DATE = "%b %-d, %Y"


@dataclass(frozen=True)
class _Formats:
    """Measure field -> format, and a scatter's x field -> its own format (None:
    plain). The two may name one field (a layer plotting the x column)."""

    measure: dict[str, ResolvedFormat]
    x: dict[str, ResolvedFormat | None]

    def lookup(self, channel: str, field: str) -> ResolvedFormat | None:
        if channel == "x" and field in self.x:
            return self.x[field]
        return self.measure.get(field)


_LAYERED = (
    ResolvedBarChart,
    ResolvedLineChart,
    ResolvedAreaChart,
    ResolvedScatterChart,
)
_MEASURED = (
    *_LAYERED,
    ResolvedHeatmapChart,
    ResolvedGeoshapeChart,
    ResolvedPointMapChart,
)


def _measure_formats(chart: ResolvedChart) -> _Formats:
    """The measure fields' formats (a layer's own, else the chart's; a
    histogram's measure is its count) and a scatter's x."""
    if not isinstance(chart, _MEASURED):
        return _Formats({}, {})
    fmt = chart.style.tooltip_format
    fields: list[tuple[str, ResolvedFormat]] = []
    if isinstance(chart, ResolvedHeatmapChart):
        channel = chart.resolved_channels.get("color")
        if channel is not None and channel.data_field:
            fields.append((channel.data_field, fmt))
    elif isinstance(chart, ResolvedPointMapChart):
        if chart.size:
            fields.append((chart.size, fmt))
    elif isinstance(chart, ResolvedGeoshapeChart):
        if chart.value_field:
            fields.append((chart.value_field, fmt))
    elif isinstance(chart, ResolvedBarChart) and chart.chart_type == "histogram":
        fields.append((_COUNT_FIELD, fmt))
    else:
        y = chart.y
        fields.append((y if isinstance(y, str) else WIDE_VALUE_FIELD, fmt))
        for layer in chart.layers:
            labels = layer.axis_y.labels
            layer_format = layer.axis_y.tooltip_format or (
                labels.format if labels is not None else None
            )
            if layer.y is not None:
                fields.append((layer.y, layer_format or fmt))
    x: dict[str, ResolvedFormat | None] = {}
    if isinstance(chart, ResolvedScatterChart) and isinstance(chart.x, str):
        # As the structured card: x's own format, else the measure's digits
        # without its affix.
        x[chart.x] = chart.x_tooltip_format or ResolvedFormat(spec=fmt.spec)
    return _Formats(dict(fields), x)


def _quantitative(
    ref: str, channel: str, field: str, spec: str, formats: _Formats
) -> str:
    fmt = formats.lookup(channel, field)
    if fmt is None:
        fmt = ResolvedFormat(spec=spec)
    return format_quantitative_expr(ref, fmt)


def _bin_edge(ref: str, spec: str) -> str:
    """A bin edge in the binned column's spec; an SI spec prints plain digits
    below 1, as the axis does, so 0.6 never reads as milli."""
    plain = f"format({ref}, {json.dumps(spec)})"
    if not is_d3_si_spec(spec):
        return plain
    digits = f"format({ref}, {json.dumps(sub_unit_digit_format(spec))})"
    return f"(abs({ref}) < 1 ? {digits} : {plain})"


def _spec(defn: VLDict) -> str:
    """The channel's format; Vega-Lite's own rule prints its axis's without one."""
    if "format" in defn:
        return str(defn["format"])
    axis = defn.get("axis")
    if isinstance(axis, dict) and "format" in axis:
        return str(axis["format"])
    return ""


def _value(channel: str, defn: VLDict, formats: _Formats) -> str:
    spec = _spec(defn)
    if defn.get("aggregate") == "count":
        ref = f"datum[{json.dumps(_COUNT_FIELD)}]"
        return _quantitative(ref, channel, _COUNT_FIELD, spec, formats)
    field = defn["field"]
    if defn.get("bin") is True:
        start = f"datum[{json.dumps(_BIN_PREFIX + field)}]"
        end = f"datum[{json.dumps(_BIN_PREFIX + field + '_end')}]"
        # A bin's edges are the binned column's own values, not the measure.
        return f"{_bin_edge(start, spec)} + ' – ' + {_bin_edge(end, spec)}"
    ref = f"datum[{json.dumps(field)}]"
    if defn.get("type") == "quantitative":
        return _quantitative(ref, channel, field, spec, formats)
    if defn.get("type") == "temporal":
        date_spec = spec if spec else _DEFAULT_DATE
        return f"utcFormat(toDate({ref}), {json.dumps(date_spec)})"
    return ref


def _title(defn: VLDict) -> str:
    """The channel's title; Vega-Lite titles an untitled channel by its field."""
    title = defn.get("title")
    if isinstance(title, str):
        return title
    if defn.get("aggregate") == "count":
        return "Count"
    return str(defn["field"])


def _rows(encoding: VLDict) -> list[tuple[str, VLDict]]:
    """Card rows; a tooltip-list row naming the x field reads as ``x``."""
    listed = encoding.get("tooltip")
    if isinstance(listed, list):
        x_def = encoding.get("x")
        x = x_def.get("field") if isinstance(x_def, dict) else None
        return [
            ("x" if d.get("field") == x else "tooltip", d)
            for d in listed
            if isinstance(d, dict)
        ]
    return [
        (channel, encoding[channel])
        for channel in encoding
        if channel in _DESCRIBED
        and isinstance(encoding[channel], dict)
        and ("field" in encoding[channel] or "aggregate" in encoding[channel])
    ]


def _description(encoding: VLDict, formats: _Formats) -> str | None:
    rows = _rows(encoding)
    if not rows:
        return None
    parts = [
        f"{json.dumps(_title(defn) + ': ')} + ({_value(channel, defn, formats)})"
        for channel, defn in rows
    ]
    return " + '; ' + ".join(parts)


def _stamp(spec: ChartSpec, inherited: VLDict, formats: _Formats) -> None:
    encoding = {**inherited, **spec.encoding}
    if spec.encoding and not spec.tooltip_description:
        description = _description(encoding, formats)
        if description is not None:
            spec.tooltip_description = description
    for layer in spec.layers:
        _stamp(layer, encoding, formats)


@dataclass
class NativeTooltipAffixFeature:
    """Writes the hover card of a chart without a structured tooltip when one of
    its measure formats carries an affix."""

    def applies_to(self, chart: ResolvedChart) -> bool:
        if StructuredTooltipFeature().applies_to(chart):
            return False
        formats = _measure_formats(chart)
        return any(
            f is not None and f.has_affix
            for f in (*formats.measure.values(), *formats.x.values())
        )

    def apply(
        self,
        spec: ChartSpec,
        chart: ResolvedChart,
        box: RenderBox,
        datasets: dict[
            str | None, list[VLDict]
        ],  # type-state: explicit_any — the ChartFeature protocol's datasets rows
    ) -> ChartSpec:
        _stamp(spec, {}, _measure_formats(chart))
        return spec
