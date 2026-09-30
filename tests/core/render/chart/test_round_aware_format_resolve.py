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
from dbt_charts.core.compile.models.style.theme.marks import BarLabelsStyle
from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
from dbt_charts.core.compile.resolve.chart._marks import _label_format_fallback
from dbt_charts.core.compile.resolve.style.axis_cascade import (
    AxisOverrides,
    build_resolved_axis,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl
from dbt_charts.core.render.format_utils import format_value

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
    chart-authored ``format:`` — ``chart.format`` is the highest-priority
    layer in the axis cascade (``_bake_cartesian_axes``/``style_cascade.py``
    merges it after the theme's ``axis_quantitative`` default), so it
    survives to the resolved axis unlike a bare theme-level override."""
    chart_style_context = resolve_chart_style_context(get_theme_style("stark"))
    chart = BarChart(id="fixture", type="bar", format=format_spec)
    (
        _ax_merged,
        ay_merged,
        _ax_band,
        ay_band,
        ay_format_raw,
    ) = _bake_cartesian_axes(
        chart_style_context, chart, "bar", "ordinal", "quantitative", AxisOverrides()
    )
    return build_resolved_axis(
        ay_merged,
        band_position=ay_band,
        chart_id="t",
        format_raw=ay_format_raw,
    )


def test_inline_si_axis_passes_through_to_vega_without_trim() -> None:
    """Inline d3 spec ``".3s"`` is passed verbatim to Vega — no trim injected.

    vl_field_maps.py hands the resolved format directly to Vega (``d["format"] = v``).
    Under the three-way contract, only predefined format names get trim baked;
    inline specs pass through as authored. Vega renders ``"1.0M"`` (trailing
    zero preserved) — the same output Python's format_d3 produces for the same
    raw spec."""
    resolved_ay = _resolved_axis_y_for_format(".3s")
    assert resolved_ay.ruler is None, (
        "test requires the non-compacting (no ruler) axis path"
    )

    vl_format = axis_to_vl(resolved_ay)["format"]
    assert vl_format == ".3s", (
        f"inline d3 spec must reach Vega unchanged — got {vl_format!r}"
    )

    rendered = _render_text_via_vega(1_000_000.0, vl_format)
    assert rendered == "1.00M", (
        "inline spec without ~ keeps trailing zeros on both Python and Vega sides"
        f" — got {rendered!r}"
    )


def test_mark_value_label_inline_si_passes_through() -> None:
    """An explicitly-authored mark value-label inline SI spec bypasses trim
    injection — ``_label_format_fallback`` returns it unchanged, and both
    Python-side format_d3 and Vega render the same digit count."""
    labels = BarLabelsStyle(format=".3s")
    resolved_labels, _ = _label_format_fallback(
        labels, axis_format=None, axis_house_default=False, formats={}
    )
    assert resolved_labels.format == ".3s"

    rendered = _render_text_via_vega(1_000_000.0, resolved_labels.format)
    assert rendered == "1.00M"


def test_axis_and_kpi_agree_on_digits_for_the_same_authored_spec() -> None:
    """The parity claim: an axis tick (real Vega) and a KPI/table cell
    (format_value, Python) must show the same digit count for one authored
    spec.

    Under the three-way contract, inline ``"$,.2s"`` passes through without
    trim on both sides — Vega and Python both render ``"$1.0M"``.

    The space-vs-no-space register gap (Vega's bare d3 ``"M"`` vs dbt charts'
    analytic ``" M"``) is pre-existing and out of scope here — stripped out on
    both sides to isolate the digit-count assertion.
    """
    resolved_ay = _resolved_axis_y_for_format("$,.2s")
    vl_format = axis_to_vl(resolved_ay)["format"]

    axis_digits = _render_text_via_vega(1_000_000.0, vl_format).replace(" ", "")
    kpi_digits = format_value(1_000_000.0, "$,.2s").replace(" ", "")
    assert axis_digits == kpi_digits, (
        f"axis (Vega) rendered {axis_digits!r}, KPI/table (format_value) rendered "
        f"{kpi_digits!r} — same authored spec, different digits"
    )
