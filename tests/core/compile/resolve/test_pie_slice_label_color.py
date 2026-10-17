"""Pie slice label ink: the theme spells `auto`; resolve leaves it to the wedge."""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.chart.resolved import ResolvedPieChart
from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.normalize.charts import normalize_chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

_ROWS = [{"share": 3}]
_QUERY_REGISTRY = {"q": ValuesQuery(rows=_ROWS)}


def _resolved(style: dict[str, Any] | None = None) -> tuple[ResolvedPieChart, Any]:
    _, board_ctx = resolve_style_and_context(get_theme_style(get_default_theme_name()))
    chart_def: dict[str, Any] = {"type": "pie", "query": "q", "theta": "share"}
    if style is not None:
        chart_def["style"] = style
    flat = normalize_chart("chart1", chart_def, _QUERY_REGISTRY, sources={})
    resolved = resolve(flat, _ROWS, chart_style_context=board_ctx)
    assert isinstance(resolved, ResolvedPieChart)
    return resolved, board_ctx


def test_single_series_label_uses_auto_unless_authored() -> None:
    resolved, board_ctx = _resolved()
    assert board_ctx.pie.marks.slice.labels.font.color is None
    assert resolved.style.slice_mark.labels.font.color is None

    authored, _ = _resolved(
        {"marks": {"slice": {"labels": {"font": {"color": "#112233"}}}}}
    )
    assert authored.style.slice_mark.labels.font.color == "#112233"


def test_authored_auto_label_ink_is_each_wedges_own() -> None:
    resolved, _ = _resolved(
        {"marks": {"slice": {"labels": {"font": {"color": "auto"}}}}}
    )
    assert resolved.style.slice_mark.labels.font.color is None
