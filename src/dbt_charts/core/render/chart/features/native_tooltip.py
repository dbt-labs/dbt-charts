"""Compose a format's affix on the hover card of a geo map.

A map's card is Vega-Lite's own mark description, built from each
channel's plain d3 ``format``, which has no place for a prefix or suffix (a
money name's currency included). When a measure's format carries one, this
feature writes the description itself: one ``title: value`` row per channel, in
the encoding's order, the measure composed like every other affix.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.chart.resolved.geoshape import (
    ResolvedGeoshapeChart,
)
from dbt_charts.core.compile.models.chart.resolved.point_map import (
    ResolvedPointMapChart,
)
from dbt_charts.core.compile.models.primitives import ResolvedFormat
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.emitters._tooltip import (
    COUNT_TITLE,
    format_quantitative_expr,
)
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox

# Channels Vega-Lite's own description lists; the rest carry no row.
_DESCRIBED = ("x", "y", "theta", "color", "size", "shape", "opacity")
# Vega-Lite's name for the count aggregate.
_COUNT_FIELD = "__count"
# Vega-Lite's date text for a temporal channel with no format.
_DEFAULT_DATE = "%b %-d, %Y"


def _measure_formats(chart: ResolvedChart) -> dict[str, ResolvedFormat]:
    """The measure field's format: a point map's size, a choropleth's value."""
    if isinstance(chart, ResolvedPointMapChart) and chart.size:
        return {chart.size: chart.style.tooltip_format}
    if isinstance(chart, ResolvedGeoshapeChart) and chart.value_field:
        return {chart.value_field: chart.style.tooltip_format}
    return {}


def _quantitative(
    ref: str, field: str, spec: str, formats: dict[str, ResolvedFormat]
) -> str:
    return format_quantitative_expr(
        ref, formats.get(field) or ResolvedFormat(spec=spec)
    )


def _spec(defn: VLDict) -> str:
    """The channel's format; Vega-Lite's own rule prints its axis's without one."""
    if "format" in defn:
        return str(defn["format"])
    axis = defn.get("axis")
    if isinstance(axis, dict) and "format" in axis:
        return str(axis["format"])
    return ""


def _value(defn: VLDict, formats: dict[str, ResolvedFormat]) -> str:
    spec = _spec(defn)
    if defn.get("aggregate") == "count":
        ref = f"datum[{json.dumps(_COUNT_FIELD)}]"
        return _quantitative(ref, _COUNT_FIELD, spec, formats)
    field = defn["field"]
    ref = f"datum[{json.dumps(field)}]"
    if defn.get("type") == "quantitative":
        return _quantitative(ref, field, spec, formats)
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
        return COUNT_TITLE
    return str(defn["field"])


def _description(encoding: VLDict, formats: dict[str, ResolvedFormat]) -> str | None:
    listed = encoding.get("tooltip")
    if isinstance(listed, list):
        defns = [d for d in listed if isinstance(d, dict)]
    else:
        defns = [
            encoding[channel]
            for channel in encoding
            if channel in _DESCRIBED
            and isinstance(encoding[channel], dict)
            and ("field" in encoding[channel] or "aggregate" in encoding[channel])
        ]
    if not defns:
        return None
    parts = [
        f"{json.dumps(_title(defn) + ': ')} + ({_value(defn, formats)})"
        for defn in defns
    ]
    return " + '; ' + ".join(parts)


def _stamp(
    spec: ChartSpec, inherited: VLDict, formats: dict[str, ResolvedFormat]
) -> None:
    encoding = {**inherited, **spec.encoding}
    if spec.encoding and not spec.tooltip_description:
        description = _description(encoding, formats)
        if description is not None:
            spec.tooltip_description = description
    for layer in spec.layers:
        _stamp(layer, encoding, formats)


@dataclass
class NativeTooltipAffixFeature:
    """Writes a map's hover card when one of its measure formats carries an affix."""

    def applies_to(self, chart: ResolvedChart) -> bool:
        return any(f.has_affix for f in _measure_formats(chart).values())

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
