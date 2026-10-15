"""Format alias resolution — gap site regression tests.

Covers sites where format aliases bypassed resolve_format() and were emitted
as raw alias strings into Vega-Lite specs.

Gap sites fixed in PR #2872:
1. support_table source row  — _vl_format_calc (support_table_attachment.py)
2. support_table aggregate row — inline calculate (support_table_attachment.py)
3. support_table per_series row — inline calculate (support_table_attachment.py)
4. pie chart total.format   — compile/resolve/chart/pie.py
5. axis.format               — compile/resolve/chart/_axes.py

Gap site fixed in follow-up (this file):
6. tooltip.format — compile/resolve/ (per-family resolve_format
   calls) and emitters/_tooltip.py (tooltip field list)
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.primitives import FormatTable
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

from ._engine_render import render_pie_svg
from .conftest import chart_pane

_BOARD_STYLE = resolve_style(get_theme_style())
_BOARD_CONTEXT = resolve_chart_style_context(get_theme_style())

# All these aliases live in the default theme (stark.yaml).
# Tests use them to verify resolution: the VL output must contain the d3 spec,
# not the alias name.
_ALIAS_CURRENCY = "currency_full"  # → "$,.2f"
_RESOLVED_CURRENCY = "$,.2f"

_ALIAS_INTEGER = "integer"  # → ",.0f"
_RESOLVED_INTEGER = ",.0f"

_ALIAS_NUMBER = "number_full"  # → ",.2f"
_RESOLVED_NUMBER = ",.2f"

_ALIAS_COMPACT = "number"  # → ".3~s" (trim flag set; round_aware_spec is a no-op)
_RESOLVED_COMPACT = ".3~s"

_ALIAS_PERCENT = "percent"  # → ".1%"
_RESOLVED_PERCENT = ".1%"

_BAR_DATA = [
    {"month": "Jan", "revenue": 100.0},
    {"month": "Feb", "revenue": 200.0},
    {"month": "Mar", "revenue": 150.0},
]

_STACKED_DATA = [
    {"month": "Jan", "category": "Alpha", "revenue": 100.0},
    {"month": "Jan", "category": "Beta", "revenue": 200.0},
    {"month": "Feb", "category": "Alpha", "revenue": 110.0},
    {"month": "Feb", "category": "Beta", "revenue": 210.0},
]

_PIE_DATA = [
    {"category": "A", "value": 300.0},
    {"category": "B", "value": 700.0},
]


@pytest.fixture(autouse=True)
def _reset_config():
    reset_config()
    yield
    reset_config()


def _all_calculate_exprs(spec: dict[str, Any]) -> list[str]:
    """Collect every calculate-transform expression from every layer.

    Endpoint labels may have wrapped the chart in hconcat/vconcat — unwrap to
    the real chart pane first (chart_pane() is a no-op otherwise).
    """
    exprs: list[str] = []
    for layer in chart_pane(spec).get("layer", []):
        for t in layer.get("transform", []) or []:
            if "calculate" in t:
                exprs.append(t["calculate"])
    return exprs


# ── Gap site 1: support_table source row ────────────────────────────────────────


def test_support_table_source_format_alias_resolves():
    """Source row: 'currency' must resolve to '$,.2f' in the VL calculate expression."""
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "style": {"orientation": "vertical"},
            "support_table": {
                "entries": [{"source": "revenue", "format": _ALIAS_CURRENCY}]
            },
        }
    )
    _rc = resolve(chart, _BAR_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _BAR_DATA, width=400, height=200)
    calcs = _all_calculate_exprs(spec)

    # The anchored currency composes ahead of the digits.
    assert any(",.2f" in c and '"$"' in c for c in calcs), (
        f"Expected '{_RESOLVED_CURRENCY}' in a calculate expression; got: {calcs}"
    )
    assert not any(f"'{_ALIAS_CURRENCY}'" in c for c in calcs), (
        f"Alias '{_ALIAS_CURRENCY}' must be resolved before emit; got: {calcs}"
    )


# ── Gap site 2: support_table aggregate row ─────────────────────────────────────


def test_support_table_aggregate_format_alias_resolves():
    """Aggregate row: 'integer' must resolve to ',.0f' in the VL calculate expression."""
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "style": {"orientation": "vertical"},
            "support_table": {
                "entries": [
                    {"aggregate": "sum", "source": "revenue", "format": _ALIAS_INTEGER}
                ]
            },
        }
    )
    _rc = resolve(chart, _BAR_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _BAR_DATA, width=400, height=200)
    calcs = _all_calculate_exprs(spec)

    assert any(_RESOLVED_INTEGER in c for c in calcs), (
        f"Expected '{_RESOLVED_INTEGER}' in a calculate expression; got: {calcs}"
    )
    assert not any(f"'{_ALIAS_INTEGER}'" in c for c in calcs), (
        f"Alias '{_ALIAS_INTEGER}' must be resolved before emit; got: {calcs}"
    )


# ── Gap site 3: support_table per_series row ────────────────────────────────────


def test_support_table_per_series_format_alias_resolves():
    """Per-series row: 'number' must resolve to ',.2f' in the VL calculate expression."""
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "color": "category",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "stack": "zero",
            "style": {"orientation": "vertical"},
            "support_table": {
                "entries": [{"per_series": "revenue", "format": _ALIAS_NUMBER}]
            },
        }
    )
    _rc = resolve(chart, _STACKED_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _STACKED_DATA, width=400, height=200)
    calcs = _all_calculate_exprs(spec)

    assert any(_RESOLVED_NUMBER in c for c in calcs), (
        f"Expected '{_RESOLVED_NUMBER}' in a calculate expression; got: {calcs}"
    )
    assert not any(f"'{_ALIAS_NUMBER}'" in c for c in calcs), (
        f"Alias '{_ALIAS_NUMBER}' must be resolved before emit; got: {calcs}"
    )


# ── Gap site 4: pie chart total.format ───────────────────────────────────────


def test_pie_total_format_alias_resolves():
    """style.total.value.format: 'number' paints the total through the alias."""
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "pie",
            "theta": "value",
            "color": "category",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "total": {},
            "style": {
                "inner_radius": 0.6,
                "total": {"value": {"format": _ALIAS_COMPACT}},
            },
        }
    )
    svg = render_pie_svg(chart, _PIE_DATA, width=400, height=400)
    assert re.search(r'layer_1_marks"[^>]*><text [^>]*>1k</text>', svg), svg


# ── Gap site 5: axis.format via _build_encoding_axis ─────────────────────────


def test_axis_x_format_alias_resolves():
    """axis_x.labels.format: 'currency' alias must resolve to '$,.2f' in encoding.x.axis.format.

    axis_x.labels.format is NOT inferred to the chart-level format field (unlike
    axis_y.labels.format), so it goes through _build_encoding_axis → axis_to_vl
    without the user_format resolution path.  This is the gap site.

    A scatter chart with a quantitative x-axis triggers this path cleanly.
    """
    from dbt_charts.core.compile.models.style.authored import (
        AxisXStylePatch,
        DimensionLabelStylePatch,
        ScatterChartStylePatch,
    )

    _SCATTER_DATA = [
        {"width": 100.0, "height": 200.0},
        {"width": 150.0, "height": 250.0},
        {"width": 200.0, "height": 300.0},
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "scatter",
            "x": "width",
            "y": "height",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "style": ScatterChartStylePatch(
                axis_x=AxisXStylePatch(
                    labels=DimensionLabelStylePatch(format=_ALIAS_CURRENCY)
                )
            ),
        }
    )
    _rc = resolve(chart, _SCATTER_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _SCATTER_DATA, width=400, height=300)

    x_enc = spec.get("encoding", {}).get("x", {})
    x_axis = x_enc.get("axis", {})
    axis_fmt = x_axis.get("format")

    assert axis_fmt == _RESOLVED_CURRENCY, (
        f"Expected axis_x.format to be '{_RESOLVED_CURRENCY}' after resolution; "
        f"got: {axis_fmt!r}  (full axis: {x_axis})"
    )


# ── Gap site 6: tooltip.format ───────────────────────────────────────────────


def test_tooltip_format_alias_resolves_in_measure_encoding():
    """tooltip.format alias must resolve before being set on the measure encoding.

    _build_standard_encoding sets menc.setdefault("format", tooltip.format) on
    the measure channel for bar/scatter/etc.  If tooltip.format is an alias like
    "currency", it must be resolved to "$,.2f" before emission.

    The test injects an alias directly into _BOARD_CONTEXT.tooltip.format
    and _BOARD_CONTEXT.formats so the alias flows through build_chart_style_context
    into the VL spec. A bar chart with categorical x auto-detects horizontal
    orientation, placing the measure (revenue) on VL "x". The assertion targets
    spec["encoding"]["x"]["format"].
    """
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
        }
    )

    import dataclasses

    # Patch the board style: inject the alias into tooltip.format and
    # add the alias to the formats vocabulary so the resolver can expand it.
    # Merged onto the board's real formats dict (not replaced) -- a real
    # compile() only ever merges into formats key-wise (merge_onto_base's
    # dict rule), so it always carries the full theme alias vocabulary,
    # including axis_quantitative's own "number". Replacing it here
    # manufactures a state ordinary authoring can't reach (short of an
    # explicit `style.formats: null`, a different, deliberate scenario this
    # test isn't exercising) and would otherwise fail on the unrelated
    # axis_y.labels.format validation this test doesn't care about.
    patched_tooltip = _BOARD_CONTEXT.tooltip.model_copy(
        update={"format": _ALIAS_CURRENCY}
    )
    patched_context = dataclasses.replace(
        _BOARD_CONTEXT,
        tooltip=patched_tooltip,
        formats=FormatTable(
            aliases={
                **(_BOARD_CONTEXT.formats.aliases if _BOARD_CONTEXT.formats else {}),
                _ALIAS_CURRENCY: _RESOLVED_CURRENCY,
            },
            currency=None,
        ),
    )

    spec = generate_vega_lite_spec(
        chart,
        _BAR_DATA,
        board_style=_BOARD_STYLE,
        chart_style_context=patched_context,
        width=400,
        height=200,
    )

    # Categorical x → horizontal bar → measure on VL "x" (quantitative).
    x_enc = spec.get("encoding", {}).get("x", {})
    fmt = x_enc.get("format")

    assert fmt == _RESOLVED_CURRENCY, (
        f"Expected tooltip.format alias '{_ALIAS_CURRENCY}' to resolve to "
        f"'{_RESOLVED_CURRENCY}' on the measure encoding; got: {fmt!r}"
    )
    assert fmt != _ALIAS_CURRENCY, (
        f"Alias '{_ALIAS_CURRENCY}' must be resolved before emit; got raw alias"
    )


# ── Contract: enum member vs inline d3, same spec, different output ──────────


def test_predefined_format_member_gets_house_rules_inline_d3_gets_native() -> None:
    """The whole three-way contract in one test.

    "number" is an engine-owned predefined name that resolves with house rules:
    round-aware trim + analytic notation register (1.5B for a billion).
    Writing the resolved spec ".3~s" directly as an inline d3 string is a
    different authoring choice and must produce different output: the predefined
    name gets notation registers (analytic "1.5B"), the inline d3 string gets
    native d3 output ("1.5G" — d3's SI letter for a billion).
    """
    from dbt_charts.core.compile.format_value import format_value

    # Predefined member: house rules apply (analytic notation register).
    enum_output = format_value(1_500_000_000.0, "number")
    # Inline d3 with same trimmed spec: native d3, no house notation.
    # The billion letter distinguishes house analytic ("1.5B") from native d3 ("1.5G").
    inline_output = format_value(1_500_000_000.0, _RESOLVED_COMPACT)

    assert enum_output == "1.5B", (
        f"predefined member 'number' must apply analytic notation; got {enum_output!r}"
    )
    assert inline_output == "1.5G", (
        f"inline {_RESOLVED_COMPACT!r} must return native d3 (G for a billion); "
        f"got {inline_output!r}"
    )
    assert enum_output != inline_output, (
        "enum member and inline d3 with the same spec must produce different output"
    )
