"""A board's ``style.charts.<family>`` tier sits between the theme and the chart.

Pie, geoshape and point_map read their chart-envelope values (palette, alias
provenance, background, single-series fill) from the merged family, so the
board tier beats the global (theme-level) value and the chart's own ``style``
beats the board tier.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)

_QUERY = SqlQuery(sql="SELECT 1", source="src")

_GLOBAL = ["#aa0000", "#aa1111"]
_BOARD = ["#00bb00", "#11bb11"]
_CHART = ["#0000cc", "#1111cc"]

_PIE: dict[str, Any] = {"type": "pie", "theta": "n", "color": "s"}
_PIE_ROWS = [{"n": 1, "s": "a"}, {"n": 2, "s": "b"}]
_GEOSHAPE: dict[str, Any] = {"type": "geoshape", "geo": "g", "projection": "mercator"}
_GEOSHAPE_ROWS = [{"g": "CA"}]
_POINT_MAP: dict[str, Any] = {
    "type": "point_map",
    "latitude": "la",
    "longitude": "lo",
    "projection": "albersUsa",
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
    assert resolved.warned_palette == "RdYlGn"


def test_pie_board_palette_clears_theme_alias_provenance() -> None:
    ctx = _ctx({**_categorical("RdYlGn"), "pie": _categorical(_BOARD)})
    resolved = _resolved(_PIE, _PIE_ROWS, ctx)
    assert resolved.palette[:2] == tuple(_BOARD)
    assert resolved.warned_palette is None


def test_pie_chart_palette_beats_board_alias() -> None:
    ctx = _ctx({"pie": _categorical("RdYlGn")})
    resolved = _resolved(_PIE, _PIE_ROWS, ctx, _categorical(_CHART))
    assert resolved.palette[:2] == tuple(_CHART)
    assert resolved.warned_palette is None


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


def test_point_map_board_mark_color_beats_theme_single_series_palette() -> None:
    ctx = _ctx(
        {
            "color": {"categorical": {"single_series_palette": ["#aa0000"]}},
            "point_map": {"marks": {"point": {"color": "#00bb00"}}},
        }
    )
    resolved = _resolved(_POINT_MAP, _POINT_MAP_ROWS, ctx)
    assert resolved.style.single_series_fill == "#00bb00"


def test_point_map_chart_mark_color_beats_board_mark_color() -> None:
    ctx = _ctx({"point_map": {"marks": {"point": {"color": "#00bb00"}}}})
    resolved = _resolved(
        _POINT_MAP, _POINT_MAP_ROWS, ctx, {"marks": {"point": {"color": "#0000cc"}}}
    )
    assert resolved.style.single_series_fill == "#0000cc"


_SERIES_ROWS = [
    {"m": "a", "s": "x", "v": 1, "w": 3},
    {"m": "a", "s": "y", "v": 2, "w": 4},
    {"m": "b", "s": "x", "v": 3, "w": 5},
    {"m": "b", "s": "y", "v": 4, "w": 6},
]
_CARTESIAN = ["line", "bar", "area", "scatter", "heatmap", "histogram", "funnel"]


def _by_color(family: str) -> dict[str, Any]:
    if family == "heatmap":
        return {"type": "heatmap", "x": "m", "y": "s", "color": "s"}
    if family == "histogram":
        return {"type": "histogram", "x": "v", "color": "s"}
    return {"type": family, "x": "m", "y": "v", "color": "s"}


def _normalized(chart: dict[str, Any]) -> Chart:
    spec = {"id": "t", "query": _QUERY, "query_name": "q", **chart}
    return TypeAdapter(Chart).validate_python(spec)


@pytest.mark.parametrize("family", _CARTESIAN)
def test_cartesian_board_palette_beats_theme_palette(family: str) -> None:
    slot = "bar" if family == "funnel" else family
    ctx = _ctx({**_categorical(_GLOBAL), slot: _categorical(_BOARD)})
    resolved = _resolved(_by_color(family), _SERIES_ROWS, ctx)
    assert resolved.palette[:2] == tuple(_BOARD)
    # The chart's own style context, which annotations paint from, agrees.
    local = build_chart_style_context(ctx, _normalized(_by_color(family)))
    assert local.palette[:2] == _BOARD


@pytest.mark.parametrize("family", _CARTESIAN)
def test_cartesian_chart_palette_beats_board_palette(family: str) -> None:
    ctx = _ctx({("bar" if family == "funnel" else family): _categorical(_BOARD)})
    resolved = _resolved(_by_color(family), _SERIES_ROWS, ctx, _categorical(_CHART))
    assert resolved.palette[:2] == tuple(_CHART)


@pytest.mark.parametrize("family", ["line", "bar", "area"])
def test_y_list_takes_the_board_family_palette(family: str) -> None:
    ctx = _ctx({**_categorical(_GLOBAL), family: _categorical(_BOARD)})
    chart = {"type": family, "x": "m", "y": ["v", "w"]}
    assert _resolved(chart, _SERIES_ROWS, ctx).palette[:2] == tuple(_BOARD)


def test_board_family_palette_leaves_other_families_on_the_theme() -> None:
    ctx = _ctx({**_categorical(_GLOBAL), "line": _categorical(_BOARD)})
    assert _resolved(_by_color("bar"), _SERIES_ROWS, ctx).palette[:2] == tuple(_GLOBAL)


@pytest.mark.parametrize("family", ["line", "bar", "area", "scatter"])
def test_board_family_single_series_palette_beats_theme(family: str) -> None:
    ctx = _ctx(
        {
            "color": {"categorical": {"single_series_palette": ["#aa0000"]}},
            family: {"color": {"categorical": {"single_series_palette": ["#00bb00"]}}},
        }
    )
    chart = {"type": family, "x": "m", "y": "v"}
    assert _resolved(chart, _SERIES_ROWS, ctx).style.single_series_fill == "#00bb00"


def test_a_series_annotation_paints_in_the_board_family_palette() -> None:
    ctx = _ctx({**_categorical(_GLOBAL), "line": _categorical(_BOARD)})
    chart = {
        **_by_color("line"),
        "annotations": [{"type": "rule", "y": "avg()", "series": "each"}],
    }
    normalized = _normalized(chart)
    rendered_at = datetime(2026, 1, 1, tzinfo=UTC)
    resolved = resolve(normalized, _SERIES_ROWS, ctx, rendered_at=rendered_at)
    rules = resolved.annotations
    assert [r.style.rule.stroke.color for r in rules] == _BOARD


def test_a_colorless_heatmap_fills_from_the_board_family_palette() -> None:
    ctx = _ctx({**_categorical(_GLOBAL), "heatmap": _categorical(_BOARD)})
    chart = {"type": "heatmap", "x": "m", "y": "s"}
    assert _resolved(chart, _SERIES_ROWS, ctx).style.single_series_fill == _BOARD[0]


def test_a_board_histogram_axis_x_type_is_a_compile_error() -> None:
    """A histogram bins a numeric x, so a type addressed to the histogram
    family is refused, not dropped."""
    ctx = _ctx({"histogram": {"axis_x": {"type": "nominal"}}})
    with pytest.raises(CompilationError) as exc_info:
        _resolved({"type": "histogram", "x": "v"}, _SERIES_ROWS, ctx)
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-HISTOGRAM-AXIS-X-TYPE"
    assert "style.charts.histogram.axis_x.type" in str(exc_info.value)


def test_a_board_wide_axis_x_type_leaves_a_histogram_resolving() -> None:
    """`charts.axis_x.type` styles every cartesian family on the board; a
    histogram beside the bar it was set for keeps binning its numeric x."""
    ctx = _ctx({"axis_x": {"type": "nominal"}})
    _resolved({"type": "histogram", "x": "v"}, _SERIES_ROWS, ctx)
