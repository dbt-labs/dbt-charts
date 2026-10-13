"""Rules keyed on the ``support.value`` column style the support row, not the headline."""

from __future__ import annotations

import re

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import KpiChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

from ._engine_render import render_kpi_svg

_GREEN = "#00884d"
_RED = "#94001e"
_RULES = {
    "change": {
        "when": [
            {"gt": 0, "glyph": "▲", "tone": "positive", "font": {"color": _GREEN}},
            {"lt": 0, "glyph": "▼", "tone": "negative", "font": {"color": _RED}},
        ]
    }
}


def _render(variant: str = "stacked", change: float = 12, **overrides) -> str:
    chart = KpiChart(
        id="k",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="kpi",
        value="total",
        support={"value": "change", "label": "vs prior"},
        style={"variant": variant, "support": {"format": "delta"}},
        conditional_formatting=_RULES,
        **overrides,
    )
    data = [{"total": 100, "change": change}]
    ctx = resolve_chart_style_context(get_theme_style())
    resolved = resolve(chart, data, chart_style_context=ctx)
    return render_kpi_svg(
        resolved,
        data,
        width=400,
        height=200,
        board_style=resolve_style(get_theme_style()),
    )


def _tspan_fills(svg: str) -> dict[str, str]:
    """Map each tspan's text to its fill."""
    return {
        text.strip(): fill
        for fill, text in re.findall(
            r'<tspan[^>]*fill="([^"]+)"[^>]*>([^<]*)</tspan>', svg
        )
    }


@pytest.mark.parametrize("variant", ["stacked", "inline", "compact"])
def test_support_column_rule_colors_support_row_and_draws_glyph(variant: str) -> None:
    svg = _render(variant)
    fills = _tspan_fills(svg)
    assert fills["▲"] == _GREEN
    assert fills["+12"] == _GREEN
    assert fills["100"] != _GREEN


def test_support_column_rule_negative_branch() -> None:
    svg = _render(change=-3)
    fills = _tspan_fills(svg)
    assert fills["▼"] == _RED
    assert fills["−3"] == _RED


def test_support_column_rule_no_match_keeps_muted_support() -> None:
    svg = _render(change=0)
    assert "▲" not in svg and "▼" not in svg
    assert _GREEN not in svg and _RED not in svg


def test_headline_rule_on_value_column_still_colors_headline() -> None:
    chart = KpiChart(
        id="k",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="kpi",
        value="total",
        support={"value": "change"},
        style={"support": {"format": "delta"}},
        conditional_formatting={
            "total": {"when": [{"gt": 0, "font": {"color": _GREEN}}]},
            "change": {"when": [{"gt": 0, "font": {"color": _RED}}]},
        },
    )
    data = [{"total": 100, "change": 12}]
    ctx = resolve_chart_style_context(get_theme_style())
    resolved = resolve(chart, data, chart_style_context=ctx)
    fills = _tspan_fills(
        render_kpi_svg(
            resolved,
            data,
            width=400,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )
    )
    assert fills["100"] == _GREEN
    assert fills["+12"] == _RED


@pytest.mark.parametrize(
    "rule",
    [
        {"gt": 0, "background": _GREEN},
        {"gt": 0, "font": {"color": _GREEN, "size": 30}},
    ],
)
def test_support_column_rule_with_unsupported_output_raises(rule: dict) -> None:
    chart = KpiChart(
        id="k",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="kpi",
        value="total",
        support={"value": "change"},
        conditional_formatting={"change": {"when": [rule]}},
    )
    ctx = resolve_chart_style_context(get_theme_style())
    with pytest.raises(ValueError, match="support"):
        resolve(chart, [{"total": 1, "change": 1}], chart_style_context=ctx)
