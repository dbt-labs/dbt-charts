"""`auto` written on a chart or a layer reads as the renderer default in every resolved style."""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)

_BOARD = resolve_style_and_context(get_theme_style(get_default_theme_name()))[1]
_QUERY = SqlQuery(sql="SELECT 1", source="src")


def _resolved(chart_dict: dict[str, Any], rows: list[dict[str, Any]]) -> Any:
    chart = TypeAdapter(Chart).validate_python(
        {"id": "t", "query": _QUERY, "query_name": "q", **chart_dict}
    )
    return resolve(chart, rows, chart_style_context=_BOARD, width=600.0)


def test_pie_slice_opacity_and_stroke_auto_are_unset() -> None:
    resolved = _resolved(
        {
            "type": "pie",
            "theta": "share",
            "style": {
                "marks": {
                    "slice": {
                        "opacity": "auto",
                        "stroke": {"join": "auto", "color": "auto"},
                    }
                }
            },
        },
        [{"share": 3}],
    )
    slice_mark = resolved.style.slice_mark
    assert slice_mark.opacity is None
    assert slice_mark.stroke.join is None
    assert slice_mark.stroke.color is None


def test_heatmap_rect_opacity_auto_is_unset() -> None:
    resolved = _resolved(
        {
            "type": "heatmap",
            "x": "x",
            "y": "y",
            "color": "v",
            "style": {"marks": {"rect": {"opacity": "auto"}}},
        },
        [{"x": "a", "y": "b", "v": 1.0}],
    )
    assert resolved.style.rect_mark.opacity is None


def test_geoshape_stroke_auto_is_unset() -> None:
    resolved = _resolved(
        {
            "type": "geoshape",
            "geo_source": "us-states",
            "lookup": "state_id",
            "value": "population",
            "style": {"marks": {"geoshape": {"stroke": {"join": "auto"}}}},
        },
        [],
    )
    assert resolved.style.geoshape.marks.geoshape.stroke.join is None


def test_layer_stroke_auto_is_unset() -> None:
    resolved = _resolved(
        {
            "type": "bar",
            "x": "x",
            "y": "y",
            "layers": [
                {
                    "type": "line",
                    "y": "y2",
                    "style": {"marks": {"line": {"stroke": {"cap": "auto"}}}},
                }
            ],
        },
        [{"x": "a", "y": 1.0, "y2": 2.0}, {"x": "b", "y": 2.0, "y2": 3.0}],
    )
    assert resolved.layers[0].line_mark.stroke.cap is None


def _pinned_board() -> Any:
    patch = StylePatch.model_validate(
        {
            "charts": {
                "axis": {"labels": {"angle": 30}},
                "marks": {"bar": {"opacity": 0.5}},
            }
        }
    )
    return resolve_style_and_context(get_theme_style(get_default_theme_name()), patch)[
        1
    ]


def test_chart_auto_resets_a_leaf_the_board_pinned() -> None:
    board = _pinned_board()
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "query": _QUERY,
            "query_name": "q",
            "type": "bar",
            "x": "x",
            "y": "y",
            "style": {"axis": {"labels": {"angle": "auto"}}},
        }
    )
    rows = [{"x": "a", "y": 1.0}, {"x": "b", "y": 2.0}]
    assert (
        resolve(
            chart, rows, chart_style_context=board, width=600.0
        ).style.axis_x.labels.angle
        is None
    )
    unreset = chart.model_copy(update={"style": None})
    assert (
        resolve(
            unreset, rows, chart_style_context=board, width=600.0
        ).style.axis_x.labels.angle
        == 30
    )


def test_chart_auto_reaches_the_family_context_unset() -> None:
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "query": _QUERY,
            "query_name": "q",
            "type": "bar",
            "x": "x",
            "y": "y",
            "style": {"marks": {"bar": {"opacity": "auto"}}},
        }
    )
    context = build_chart_style_context(_pinned_board(), chart)
    assert context.bar.marks.bar.opacity is None


def test_chart_legend_direction_auto_resets_the_boards_pinned_direction() -> None:
    board = resolve_style_and_context(
        get_theme_style(get_default_theme_name()),
        StylePatch.model_validate({"charts": {"legend": {"direction": "vertical"}}}),
    )[1]
    rows = [{"x": "a", "y": 1.0, "c": "p"}, {"x": "b", "y": 2.0, "c": "q"}]

    def direction(legend: dict[str, Any]) -> str | None:
        chart = TypeAdapter(Chart).validate_python(
            {
                "id": "t",
                "query": _QUERY,
                "query_name": "q",
                "type": "bar",
                "x": "x",
                "y": "y",
                "color": "c",
                "style": {"legend": {"position": {"edge": "bottom"}, **legend}},
            }
        )
        resolved = resolve(chart, rows, chart_style_context=board, width=600.0)
        return resolved.legend.direction

    assert direction({"direction": "auto"}) == "horizontal"
    assert direction({}) == "vertical"
