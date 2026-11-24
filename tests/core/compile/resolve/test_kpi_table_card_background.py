"""KPI and table cards take the chart's resolved card fill when they author no background."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_QUERY = SqlQuery(sql="SELECT 1", source="src")
_KPI: dict[str, Any] = {"type": "kpi", "value": "n"}
_TABLE: dict[str, Any] = {"type": "table"}
_ROWS = [{"n": 1}]


def _ctx(charts: dict[str, Any]) -> ChartStyleContext:
    patch = StylePatch.model_validate({"charts": charts})
    return resolve_chart_style_context(get_theme_style(), patch)


def _background(
    chart: dict[str, Any], ctx: ChartStyleContext, style: Any = None
) -> Any:
    spec = {"id": "t", "query": _QUERY, "query_name": "q", **chart}
    if style is not None:
        spec["style"] = style
    resolved = resolve(
        TypeAdapter(Chart).validate_python(spec),
        _ROWS,
        chart_style_context=ctx,
        width=600.0,
    )
    return getattr(resolved.style, chart["type"]).background


@pytest.mark.parametrize("chart", [_KPI, _TABLE], ids=["kpi", "table"])
def test_family_background_takes_the_card_fill(chart: dict[str, Any]) -> None:
    ctx = _ctx({"background": "#123456"})
    assert _background(chart, ctx) == "#123456"


@pytest.mark.parametrize("chart", [_KPI, _TABLE], ids=["kpi", "table"])
def test_authored_family_background_beats_the_card_fill(
    chart: dict[str, Any],
) -> None:
    family = chart["type"]
    ctx = _ctx({"background": "#123456", family: {"background": "#abcdef"}})
    assert _background(chart, ctx) == "#abcdef"
    local = _ctx({"background": "#123456"})
    assert _background(chart, local, {"background": "#fedcba"}) == "#fedcba"


@pytest.mark.parametrize("chart", [_KPI, _TABLE], ids=["kpi", "table"])
def test_card_fill_the_board_already_paints_adds_no_fill(
    chart: dict[str, Any],
) -> None:
    default = _ctx({})
    assert _background(chart, default) is None
    canvas = _ctx({"background": default.board_canvas})
    assert _background(chart, canvas) is None
    assert _background(chart, _ctx({"background": "transparent"})) is None


def test_kpi_gradient_beats_the_card_fill() -> None:
    ctx = _ctx({"background": "#123456"})
    gradient = {"column": "n", "scale": {"palette": "blues"}}
    assert _background(_KPI, ctx, {"background": gradient}).column == "n"
