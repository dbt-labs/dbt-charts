"""A board's ``style.charts.<family>`` tier sits between the theme and the chart.

Pie, geoshape and point_map read their chart-envelope values (palette, alias
provenance, background, single-series fill) from the merged family, so the
board tier beats the global (theme-level) value and the chart's own ``style``
beats the board tier.
"""

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

_GLOBAL = ["#aa0000", "#aa1111"]
_BOARD = ["#00bb00", "#11bb11"]
_CHART = ["#0000cc", "#1111cc"]

_PIE: dict[str, Any] = {"type": "pie", "theta": "n", "color": "s"}
_PIE_ROWS = [{"n": 1, "s": "a"}, {"n": 2, "s": "b"}]
_GEOSHAPE: dict[str, Any] = {"type": "geoshape", "geo": "g"}
_GEOSHAPE_ROWS = [{"g": "CA"}]
_POINT_MAP: dict[str, Any] = {
    "type": "point_map",
    "latitude": "la",
    "longitude": "lo",
}
_POINT_MAP_ROWS = [{"la": 1.0, "lo": 1.0}]


def _ctx(charts: dict[str, Any]) -> ChartStyleContext:
    patch = StylePatch.model_validate({"charts": charts})
    return resolve_chart_style_context(get_theme_style(), patch)


def _resolved(
    chart: dict[str, Any],
    rows: list[dict[str, Any]],
    ctx: ChartStyleContext,
    chart_style: dict[str, Any] | None = None,
) -> Any:
    spec = {"id": "t", "query": _QUERY, "query_name": "q", **chart}
    if chart_style is not None:
        spec["style"] = chart_style
    normalized = TypeAdapter(Chart).validate_python(spec)
    return resolve(normalized, rows, chart_style_context=ctx, width=600.0)


def _categorical(palette: list[str] | str) -> dict[str, Any]:
    return {"color": {"categorical": {"palette": palette}}}


def test_pie_board_palette_beats_theme_palette() -> None:
    ctx = _ctx({**_categorical(_GLOBAL), "pie": _categorical(_BOARD)})
    assert _resolved(_PIE, _PIE_ROWS, ctx).palette[:2] == tuple(_BOARD)


def test_pie_chart_palette_beats_board_palette() -> None:
    ctx = _ctx({"pie": _categorical(_BOARD)})
    resolved = _resolved(_PIE, _PIE_ROWS, ctx, _categorical(_CHART))
    assert resolved.palette[:2] == tuple(_CHART)


def test_pie_board_alias_beats_theme_and_reports_requested_name() -> None:
    ctx = _ctx({**_categorical(_GLOBAL), "pie": _categorical("RdYlGn")})
    resolved = _resolved(_PIE, _PIE_ROWS, ctx)
    assert resolved.palette[:2] != tuple(_GLOBAL)
    assert resolved.requested_alias_palette == "RdYlGn"


def test_pie_board_palette_clears_theme_alias_provenance() -> None:
    ctx = _ctx({**_categorical("RdYlGn"), "pie": _categorical(_BOARD)})
    resolved = _resolved(_PIE, _PIE_ROWS, ctx)
    assert resolved.palette[:2] == tuple(_BOARD)
    assert resolved.requested_alias_palette is None


def test_pie_chart_palette_beats_board_alias() -> None:
    ctx = _ctx({"pie": _categorical("RdYlGn")})
    resolved = _resolved(_PIE, _PIE_ROWS, ctx, _categorical(_CHART))
    assert resolved.palette[:2] == tuple(_CHART)
    assert resolved.requested_alias_palette is None


_BACKGROUND_CASES = [
    ("pie", _PIE, _PIE_ROWS),
    ("pie", {**_PIE, "type": "donut"}, _PIE_ROWS),
    ("geoshape", _GEOSHAPE, _GEOSHAPE_ROWS),
    ("geoshape", {**_GEOSHAPE, "type": "map"}, _GEOSHAPE_ROWS),
    ("point_map", _POINT_MAP, _POINT_MAP_ROWS),
    ("point_map", {**_POINT_MAP, "type": "bubble_map"}, _POINT_MAP_ROWS),
]


@pytest.mark.parametrize(("family", "chart", "rows"), _BACKGROUND_CASES)
def test_board_family_background_beats_theme(
    family: str, chart: dict[str, Any], rows: list[dict[str, Any]]
) -> None:
    ctx = _ctx({"background": "#101010", family: {"background": "#202020"}})
    resolved = _resolved(chart, rows, ctx)
    assert resolved.background == "#202020"
    assert resolved.canvas == "#202020"


@pytest.mark.parametrize(("family", "chart", "rows"), _BACKGROUND_CASES)
def test_chart_background_beats_board_family(
    family: str, chart: dict[str, Any], rows: list[dict[str, Any]]
) -> None:
    ctx = _ctx({family: {"background": "#202020"}})
    resolved = _resolved(chart, rows, ctx, {"background": "#303030"})
    assert resolved.background == "#303030"
    assert resolved.canvas == "#303030"


@pytest.mark.parametrize(
    ("chart", "rows"),
    [(c, r) for _, c, r in _BACKGROUND_CASES],
)
def test_unauthored_family_keeps_theme_background(
    chart: dict[str, Any], rows: list[dict[str, Any]]
) -> None:
    ctx = _ctx({"background": "#101010"})
    assert _resolved(chart, rows, ctx).background == "#101010"


def test_point_map_board_static_beats_theme_single_series_palette() -> None:
    ctx = _ctx(
        {
            "color": {"categorical": {"single_series_palette": ["#aa0000"]}},
            "point_map": {"color": {"static": "#00bb00"}},
        }
    )
    resolved = _resolved(_POINT_MAP, _POINT_MAP_ROWS, ctx)
    assert resolved.style.single_series_fill == "#00bb00"


def test_point_map_chart_static_beats_board_static() -> None:
    ctx = _ctx({"point_map": {"color": {"static": "#00bb00"}}})
    resolved = _resolved(
        _POINT_MAP, _POINT_MAP_ROWS, ctx, {"color": {"static": "#0000cc"}}
    )
    assert resolved.style.single_series_fill == "#0000cc"
