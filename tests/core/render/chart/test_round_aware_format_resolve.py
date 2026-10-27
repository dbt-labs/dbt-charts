"""Round-aware trim (``~``) is baked at resolve time — only for predefined formats.

Three-way format contract:

| source           | resolve_format behavior                      | trim injected? |
|------------------|-----------------------------------------------|---------------|
| predefined name  | house rules: engine spec + trim + notation    | yes           |
| style.formats    | native d3: literal spec, no post-processing  | no            |
| inline d3 string | native d3: literal spec, no post-processing  | no            |

Inline SI specs (e.g. ``".3s"``) reach Vega verbatim — Python-side render
leaves (``format_d3``, ``format_kpi_parts``) use the same raw spec, so the
digit count is consistent between axis ticks (real Vega) and KPI/table cells
(Python). Authors who want trailing-zero trimming on an inline SI spec must
write the ``~`` themselves (e.g. ``".3~s"``), or use a predefined format name
whose house spec already includes it.

These tests render the resolved spec through real ``vl_convert`` rather than
asserting on the string alone, so they catch any call site further down the
pipe that silently re-processes the spec.
"""

from __future__ import annotations

import html
import re

import vl_convert as vlc

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.primitives import ResolvedFormat
from dbt_charts.core.compile.models.style.authored import (
    AxisLabelStylePatch,
    AxisYStylePatch,
)
from dbt_charts.core.compile.models.style.resolved import ResolvedBarLabels
from dbt_charts.core.compile.models.style.theme.marks import BarLabelsStyle
from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
from dbt_charts.core.compile.resolve.chart._marks import _label_format_fallback
from dbt_charts.core.compile.resolve.style.axis_cascade import (
    AxisOverrides,
    build_resolved_axis,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_TEXT_RE = re.compile(r"<text[^>]*>(.*?)</text>", re.S)


def _render_text_via_vega(value: float, format_spec: str) -> str:
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": [{"v": value}]},
        "mark": "text",
        "encoding": {
            "text": {"field": "v", "type": "quantitative", "format": format_spec}
        },
    }
    svg = vlc.vegalite_to_svg(spec)
    match = _TEXT_RE.search(svg)
    assert match, f"no <text> element in rendered SVG: {svg}"
    return html.unescape(match.group(1))


def _resolved_axis_y_for_format(format_spec: str) -> object:
    """Bake a real axis_y through the actual compile-resolve cascade with a
    chart-authored ``axis_y.labels.format`` — a chart-local axis layer, so it
    survives to the resolved axis unlike a bare theme-level override."""
    chart_style_context = resolve_chart_style_context(get_theme_style("stark"))
    chart = BarChart(id="fixture", type="bar")
    overrides = AxisOverrides(
        y=AxisYStylePatch(labels=AxisLabelStylePatch(format=format_spec))
    )
    baked = _bake_cartesian_axes(
        chart_style_context, chart, "bar", "ordinal", "quantitative", overrides
    )
    return build_resolved_axis(
        baked.y.style,
        format=baked.y.format,
        band_position=baked.y.band_position,
        chart_id="t",
        formats=None,
    )


def test_mark_value_label_inline_si_passes_through() -> None:
    """An explicitly-authored mark value-label inline SI spec bypasses trim
    injection — ``_label_format_fallback`` returns it unchanged, and both
    Python-side format_d3 and Vega render the same digit count."""
    labels = BarLabelsStyle(format=".3s")
    resolved_labels, _ = _label_format_fallback(
        labels,
        ResolvedBarLabels,
        axis_format=None,
        axis_house_default=False,
        formats={},
    )
    assert resolved_labels.format == ResolvedFormat(spec=".3s", raw=".3s")

    rendered = _render_text_via_vega(1_000_000.0, resolved_labels.format.spec)
    assert rendered == "1.00M"
