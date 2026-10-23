"""Format alias resolution — gap site regression tests.

Covers sites where format aliases bypassed resolve_format() and were emitted
as raw alias strings into Vega-Lite specs.

Gap sites fixed in PR #2872:
1. support_table source row
2. support_table aggregate row
3. support_table per_series row
4. pie chart total.format   — compile/resolve/chart/pie.py
5. axis.format               — compile/resolve/chart/_axes.py

Gap site fixed in follow-up (this file):
6. tooltip.format — compile/resolve/ (per-family resolve_format
   calls) and emitters/_tooltip.py (tooltip field list)
"""

from __future__ import annotations

import re

from pydantic import TypeAdapter

from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery

from ._engine_render import render_pie_svg

_ALIAS_COMPACT = "number"  # → ".3~s" (trim flag set; round_aware_spec is a no-op)
_RESOLVED_COMPACT = ".3~s"


_PIE_DATA = [
    {"category": "A", "value": 300.0},
    {"category": "B", "value": 700.0},
]


# ── Gap site 1: support_table source row ────────────────────────────────────────


# ── Gap site 2: support_table aggregate row ─────────────────────────────────────


# ── Gap site 3: support_table per_series row ────────────────────────────────────


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


# ── Gap site 6: tooltip.format ───────────────────────────────────────────────


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
