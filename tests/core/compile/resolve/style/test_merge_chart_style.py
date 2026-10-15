"""``merge_chart_style`` folds a chart's local style into the cascade overrides."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)
from dbt_charts.core.compile.resolve.style.chart_style import merge_chart_style

_LEGENDED = ("bar", "line", "area", "scatter", "pie")
_FAMILIES = (*_LEGENDED, "kpi", "table", "heatmap")


@pytest.fixture
def board_ctx() -> ChartStyleContext:
    return resolve_chart_style_context(get_theme_style())


_REQUIRED: dict[str, dict[str, object]] = {
    "pie": {"theta": "n"},
    "kpi": {"value": "n"},
}


def _chart(family: str, style: dict[str, object]) -> Chart:
    return TypeAdapter(Chart).validate_python(
        {"id": "c", "type": family, "style": style, **_REQUIRED.get(family, {})}
    )


@pytest.mark.parametrize("family", _FAMILIES)
def test_background_is_folded_into_family_and_context(
    board_ctx: ChartStyleContext, family: str
) -> None:
    merged = merge_chart_style(board_ctx, _chart(family, {"background": "#123456"}))
    assert merged[family].background == "#123456"
    assert merged["background"] == "#123456"
    assert "ink_canvas" in merged


@pytest.mark.parametrize("family", _LEGENDED)
def test_local_legend_wins_over_theme_legend(
    board_ctx: ChartStyleContext, family: str
) -> None:
    merged = merge_chart_style(
        board_ctx, _chart(family, {"legend": {"symbol_limit": 7}})
    )
    assert merged["legend"].symbol_limit == 7
    assert merged[family].legend.symbol_limit == 7


@pytest.mark.parametrize("family", _LEGENDED)
def test_local_legend_visibility_is_left_to_the_chart_resolver(
    board_ctx: ChartStyleContext, family: str
) -> None:
    merged = merge_chart_style(
        board_ctx, _chart(family, {"legend": {"visible": "auto"}})
    )
    assert "legend" not in merged


@pytest.mark.parametrize("family", ["bar", "line", "area", "scatter"])
def test_local_title_font_is_folded_into_context_title(
    board_ctx: ChartStyleContext, family: str
) -> None:
    merged = merge_chart_style(
        board_ctx, _chart(family, {"title": {"font": {"family": "Zilla Slab"}}})
    )
    assert merged["title"].font.family == "Zilla Slab"


def test_unstyled_chart_merges_nothing(board_ctx: ChartStyleContext) -> None:
    chart = _chart("kpi", {})
    assert merge_chart_style(board_ctx, chart) == {}
    assert build_chart_style_context(board_ctx, chart) is board_ctx
