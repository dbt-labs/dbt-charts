"""KPI slot ink: each slot's color is a resolved link, never a None sentinel."""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.chart.resolved import ResolvedKpiChart
from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.normalize.charts import normalize_chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

_ROWS = [{"revenue": 128_000, "delta": 4}]
_QUERY_REGISTRY = {"q": ValuesQuery(rows=_ROWS)}


def _kpi(style: dict[str, Any] | None = None, support: bool = True) -> Any:
    _, board_ctx = resolve_style_and_context(get_theme_style(get_default_theme_name()))
    chart_def: dict[str, Any] = {
        "type": "kpi",
        "query": "q",
        "value": "revenue",
        "label": "Revenue",
    }
    if support:
        chart_def["support"] = {"value": "delta"}
    if style is not None:
        chart_def["style"] = style
    flat = normalize_chart("chart1", chart_def, _QUERY_REGISTRY, sources={})
    resolved = resolve(flat, _ROWS, chart_style_context=board_ctx)
    assert isinstance(resolved, ResolvedKpiChart)
    return resolved.style.kpi, board_ctx


def test_value_color_reaches_affix_and_glyph() -> None:
    kpi, _ = _kpi({"value": {"font": {"color": "#112233"}}})
    assert kpi.value.font.color == "#112233"
    assert kpi.value.affix.font.color == "#112233"
    assert kpi.value.glyph.font.color == "#112233"
    assert kpi.label.font.color == kpi.font.color


def test_unauthored_slots_carry_the_card_ink() -> None:
    kpi, _ = _kpi()
    ink = kpi.font.color
    assert ink is not None
    for slot in (kpi.value, kpi.value.affix, kpi.value.glyph, kpi.label):
        assert slot.font.color == ink


def test_support_tone_paints_row_and_glyph() -> None:
    kpi, ctx = _kpi({"support": {"tone": "positive"}})
    assert kpi.support.font.color == ctx.tones.positive
    assert kpi.support.glyph.font.color == ctx.tones.positive


def test_support_without_tone_is_muted() -> None:
    kpi, ctx = _kpi()
    assert kpi.support.font.color == ctx.cascaded.muted
    assert kpi.support.glyph.font.color == ctx.cascaded.muted


def test_authored_support_color_keeps_tone_on_glyph() -> None:
    kpi, ctx = _kpi({"support": {"tone": "negative", "font": {"color": "#112233"}}})
    assert kpi.support.font.color == "#112233"
    assert kpi.support.glyph.font.color == ctx.tones.negative


def test_authored_support_color_without_tone_reaches_glyph() -> None:
    kpi, _ = _kpi({"support": {"font": {"color": "#112233"}}})
    assert kpi.support.glyph.font.color == "#112233"
