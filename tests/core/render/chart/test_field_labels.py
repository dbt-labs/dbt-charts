"""`field_labels:` replaces the text derived from a field name on every surface
that paints one, verbatim; `x_label`/`y_label` and `layer.label` still win."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.models.chart.normalized import PieChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render
from dbt_charts.core.render.chart.emitters import get_emitter
from dbt_charts.core.render.chart.emitters._endpoint_rail import (
    resolve_endpoint_rail_span,
)
from dbt_charts.core.render.chart.features.structured_tooltip import (
    _cartesian_roles,
    _pie_roles,
)
from dbt_charts.core.render.chart.spec import RenderBox
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

Rows = list[dict[str, Any]]


def _board(
    chart: dict[str, Any], rows: Rows, extra_queries: dict[str, Rows] | None = None
) -> str:
    queries = {"q": rows, **(extra_queries or {})}
    return yaml.safe_dump(
        {
            "title": "T",
            "queries": {
                name: {"type": "values", "rows": qrows}
                for name, qrows in queries.items()
            },
            "charts": {"c": {"query": "q", **chart}},
            "rows": ["c"],
        },
        sort_keys=False,
    )


def _spec(chart: dict[str, Any], rows: Rows) -> dict[str, Any]:
    result = compile_board(_board(chart, rows))
    assert result.board is not None, result.errors
    return generate_vega_lite_spec(result.board.charts["c"], rows)


def _render(
    chart: dict[str, Any], rows: Rows, extra_queries: dict[str, Rows] | None = None
) -> Any:
    result = compile_board(_board(chart, rows, extra_queries))
    assert result.board is not None, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    return render(result.board, executor, format="svg")


def _values(node: Any, key: str) -> Iterator[Any]:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield v
            yield from _values(v, key)
    elif isinstance(node, list):
        for v in node:
            yield from _values(v, key)


def _tooltip_text(spec: dict[str, Any]) -> str:
    """Structured tooltip expressions (``description``) plus plain tooltip
    entry titles, so an assertion cannot pass on an axis or legend title."""
    exprs = [json.dumps(d) for d in _values(spec, "description") if d]
    entries = [
        t["title"]
        for tooltip in _values(spec, "tooltip")
        if isinstance(tooltip, list)
        for t in tooltip
    ]
    return "\n".join(exprs + entries)


def _encoding(spec: dict[str, Any], channel: str) -> dict[str, Any]:
    return next(
        enc[channel]
        for enc in _values(spec, "encoding")
        if isinstance(enc.get(channel), dict) and "field" in enc[channel]
    )


_WIDE_ROWS = [
    {"month": "Jan", "all": 5, "fix-linked": 2},
    {"month": "Feb", "all": 7, "fix-linked": 3},
]
_WIDE = {"type": "line", "x": "month", "y": ["all", "fix-linked"]}


def test_wide_measure_entries_and_joined_title_use_field_label() -> None:
    spec = _spec({**_WIDE, "field_labels": {"fix-linked": "fix-linked"}}, _WIDE_ROWS)
    assert "all, fix-linked" in list(_values(spec, "title"))
    assert "fix linked" not in json.dumps(spec)


@pytest.mark.parametrize("family", ["line", "bar", "area"])
def test_wide_measure_entry_paints_field_label_in_svg(family: str) -> None:
    labeled = {**_WIDE, "type": family, "field_labels": {"fix-linked": "fix-linked"}}
    svg = _render(labeled, _WIDE_ROWS).output
    assert ">fix-linked<" in svg
    assert "fix linked" not in svg
    assert "fix linked" in _render({**_WIDE, "type": family}, _WIDE_ROWS).output


def test_scatter_wide_measure_labels_use_field_label() -> None:
    labeled = {**_WIDE, "type": "scatter", "field_labels": {"fix-linked": "fix-linked"}}
    assert "fix linked" not in json.dumps(_spec(labeled, _WIDE_ROWS))
    assert "fix linked" in json.dumps(_spec({**_WIDE, "type": "scatter"}, _WIDE_ROWS))


_DATE_LAYER_ROWS = [
    {"d": "2026-01-01", "revenue": 5, "target": 6},
    {"d": "2026-02-01", "revenue": 7, "target": 6},
    {"d": "2026-03-01", "revenue": 8, "target": 7},
]
_DATE_LAYERED = {
    "type": "line",
    "x": "d",
    "y": "revenue",
    "layers": [{"type": "line", "y": "target"}],
}


def test_endpoint_rail_names_base_and_layer_by_field_label() -> None:
    svg = _render(
        {
            **_DATE_LAYERED,
            "field_labels": {"revenue": "Rev LBL", "target": "Goal LBL"},
            "style": {
                "endpoint_labels": {"visible": True},
                "legend": {"visible": False},
            },
        },
        _DATE_LAYER_ROWS,
    ).output
    assert ">Rev LBL<" in svg
    assert ">Goal LBL<" in svg
    assert ">revenue<" not in svg


@pytest.mark.parametrize(
    ("chart", "labels", "values"),
    [
        (
            _DATE_LAYERED,
            {"revenue": "Rev LBL", "target": "Goal LBL"},
            ["Rev LBL", "Goal LBL"],
        ),
        (_WIDE, {"fix-linked": "FL"}, ["all", "FL"]),
    ],
)
def test_legend_values_resolve_against_field_labels(
    chart: dict[str, Any], labels: dict[str, str], values: list[str]
) -> None:
    rows = _DATE_LAYER_ROWS if chart is _DATE_LAYERED else _WIDE_ROWS

    def codes(legend_values: list[str]) -> set[str]:
        legend = {"visible": True, "values": legend_values}
        result = _render(
            {**chart, "field_labels": labels, "style": {"legend": legend}}, rows
        )
        return {w.code for w in result.warnings}

    assert "WARN-LEGEND-VALUES-UNRESOLVED" not in codes(values)
    assert "WARN-LEGEND-VALUES-UNRESOLVED" in codes([*values[:-1], "ZZZ"])


@pytest.mark.parametrize("field", ["revenue", "target"])
def test_endpoint_rail_reserves_width_for_field_label(field: str) -> None:
    def span(labels: dict[str, str]) -> float:
        chart = _resolved(
            {
                **_DATE_LAYERED,
                "field_labels": labels,
                "style": {"endpoint_labels": {"visible": True}},
            },
            _DATE_LAYER_ROWS,
        )
        return resolve_endpoint_rail_span(chart, _DATE_LAYER_ROWS, 600.0)

    long_label = "Quarterly plan target set by the board"
    assert span({field: long_label}) > span({})


def test_hyphen_still_splits_without_a_field_label() -> None:
    assert "all, fix linked" in list(_values(_spec(_WIDE, _WIDE_ROWS), "title"))


def test_colliding_authored_wide_label_errors() -> None:
    result = _render({**_WIDE, "field_labels": {"fix-linked": "all"}}, _WIDE_ROWS)
    [error] = result.chart_errors
    assert error.code == "ERR-FIELD-LABELS-WIDE-COLLISION"
    assert "'all'" in error.message


def test_heatmap_derived_label_falls_back_beside_an_authored_one() -> None:
    spec = _spec(
        {
            "type": "heatmap",
            "x": "month",
            "y": ["all", "fix-linked"],
            "field_labels": {"all": "fix linked"},
        },
        _WIDE_ROWS,
    )
    datums = [c["datum"] for c in _values(spec, "color") if "datum" in c]
    assert sorted(datums) == ["fix linked", "fix-linked"]


def test_colliding_authored_heatmap_label_errors() -> None:
    result = _render(
        {
            "type": "heatmap",
            "x": "month",
            "y": ["all", "fix-linked"],
            "field_labels": {"all": "Same", "fix-linked": "Same"},
        },
        _WIDE_ROWS,
    )
    assert [e.code for e in result.chart_errors] == ["ERR-FIELD-LABELS-WIDE-COLLISION"]


def test_collision_error_keeps_other_charts_warnings() -> None:
    board = yaml.safe_dump(
        {
            "title": "T",
            "queries": {
                "q": {"type": "values", "rows": _WIDE_ROWS},
                "o": {
                    "type": "values",
                    "rows": [
                        {"month": "Jan", "orders_cnt": 1, "orders (Count)": 2},
                        {"month": "Feb", "orders_cnt": 3, "orders (Count)": 4},
                    ],
                },
            },
            "charts": {
                "a": {**_WIDE, "query": "q", "field_labels": {"fix-linked": "all"}},
                "b": {
                    "type": "line",
                    "query": "o",
                    "x": "month",
                    "y": ["orders_cnt", "orders (Count)"],
                },
            },
            "rows": ["a", "b"],
        }
    )
    result = compile_board(board)
    assert result.board is not None, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    output = render(result.board, executor, format="svg")
    assert [e.code for e in output.chart_errors] == ["ERR-FIELD-LABELS-WIDE-COLLISION"]
    assert "WARN-WIDE-MEASURE-LABEL-COLLISION" in {w.code for w in output.warnings}


_SEG_ROWS = [
    {"month": "Jan", "seg": "a", "MTok_cost": 1},
    {"month": "Feb", "seg": "b", "MTok_cost": 2},
]
_SEG_LABELS = {
    "seg": "customer SEG",
    "month": "billing MONTH",
    "MTok_cost": "cost/MTok",
}


def test_bar_legend_title_axis_titles_and_tooltip_use_field_labels() -> None:
    chart = {
        "type": "bar",
        "x": "month",
        "y": "MTok_cost",
        "color": "seg",
        "style": {"orientation": "vertical"},
    }
    spec = _spec({**chart, "field_labels": _SEG_LABELS}, _SEG_ROWS)
    assert _encoding(spec, "color")["title"] == "customer SEG"
    assert _encoding(spec, "x")["title"] == "billing MONTH"
    assert _encoding(spec, "y")["title"] == "cost/MTok"
    tooltip = _tooltip_text(spec)
    assert "cost/MTok: " in tooltip
    assert "MTok cost" not in tooltip
    assert "MTok cost: " in _tooltip_text(_spec(chart, _SEG_ROWS))


def test_line_color_legend_title_uses_field_label() -> None:
    spec = _spec(
        {
            "type": "line",
            "x": "month",
            "y": "MTok_cost",
            "color": "seg",
            "field_labels": _SEG_LABELS,
        },
        _SEG_ROWS,
    )
    assert _encoding(spec, "color")["title"] == "customer SEG"


def test_heatmap_legend_title_uses_field_label() -> None:
    spec = _spec(
        {
            "type": "heatmap",
            "x": "x",
            "y": "y",
            "color": "v",
            "field_labels": {"v": "V LBL"},
        },
        [{"x": "a", "y": "b", "v": 1}, {"x": "c", "y": "d", "v": 2}],
    )
    assert _encoding(spec, "color")["title"] == "V LBL"


def test_bar_layer_y_start_tooltip_row_uses_field_label() -> None:
    rows = [
        {"month": "Jan", "revenue": 5, "lo": 2, "hi": 6},
        {"month": "Feb", "revenue": 7, "lo": 3, "hi": 8},
    ]
    chart = {
        "type": "bar",
        "x": "month",
        "y": "revenue",
        "layers": [{"type": "bar", "y": "hi", "y_start": "lo"}],
    }
    assert "Low LBL" in _tooltip_text(
        _spec({**chart, "field_labels": {"lo": "Low LBL"}}, rows)
    )
    assert "Low LBL" not in _tooltip_text(_spec(chart, rows))


def _resolved(chart: dict[str, Any], rows: Rows) -> Any:
    result = compile_board(_board(chart, rows))
    assert result.board is not None, result.errors
    return resolve(
        result.board.charts["c"],
        rows,
        chart_style_context=result.board.chart_style_context,
    )


def test_unpainted_tooltip_header_and_series_titles_use_field_labels() -> None:
    rows = [
        {"month": m, "seg": s, "MTok_cost": v}
        for m, s, v in [("Jan", "a", 1), ("Jan", "b", 2), ("Feb", "a", 3)]
    ]
    chart = _resolved(
        {
            "type": "line",
            "x": "month",
            "y": "MTok_cost",
            "color": "seg",
            "field_labels": _SEG_LABELS,
        },
        rows,
    )
    base = get_emitter(chart).emit(
        chart, RenderBox(width=600, height=300), regroup((), rows)
    )
    header, series, _, _ = _cartesian_roles(chart, rows, base)
    assert header[0].title == "billing MONTH"
    assert series[0].title == "customer SEG"

    pie = _resolved(
        {
            "type": "pie",
            "theta": "MTok_cost",
            "color": "seg",
            "field_labels": _SEG_LABELS,
        },
        _SEG_ROWS,
    )
    pie_header, _, _ = _pie_roles(pie)
    assert pie_header[0].title == "customer SEG"


def test_axis_labels_win_over_field_labels() -> None:
    spec = _spec(
        {
            "type": "bar",
            "x": "month",
            "y": "MTok_cost",
            "x_label": "X wins",
            "style": {"orientation": "vertical"},
            "y_label": "Y wins",
            "field_labels": _SEG_LABELS,
        },
        _SEG_ROWS,
    )
    assert _encoding(spec, "x")["title"] == "X wins"
    assert _encoding(spec, "y")["title"] == "Y wins"
    assert "Y wins: " in _tooltip_text(spec)
    assert "cost/MTok" not in json.dumps(spec)


_LAYER_ROWS = [
    {"month": "Jan", "revenue": 5, "target": 6},
    {"month": "Feb", "revenue": 7, "target": 6},
]


@pytest.mark.parametrize(
    ("layer_label", "expected", "absent"),
    [(None, "Goal LINE", "target"), ("Authored", "Authored", "Goal LINE")],
)
def test_layer_label_wins_over_field_labels(
    layer_label: str | None, expected: str, absent: str
) -> None:
    layer: dict[str, Any] = {"type": "line", "y": "target"}
    if layer_label is not None:
        layer["label"] = layer_label
    spec = _spec(
        {
            "type": "line",
            "x": "month",
            "y": "revenue",
            "layers": [layer],
            "field_labels": {"target": "Goal LINE"},
        },
        _LAYER_ROWS,
    )
    tooltip = _tooltip_text(spec)
    assert f"{expected}: " in tooltip
    assert f"{absent}: " not in tooltip


_BASE_ROWS = [{"month": "Jan", "revenue": 5}, {"month": "Feb", "revenue": 7}]


def test_key_named_only_by_a_layer_on_its_own_query_is_accepted() -> None:
    result = _render(
        {
            "type": "line",
            "x": "month",
            "y": "revenue",
            "layers": [{"type": "line", "query": "goals", "y": "goal"}],
            "field_labels": {"goal": "Goal LINE"},
        },
        _BASE_ROWS,
        {"goals": [{"month": "Jan", "goal": 6}, {"month": "Feb", "goal": 6}]},
    )
    assert result.chart_errors == []
    assert "Goal LINE" in result.output


def test_bar_layer_y_start_key_is_accepted() -> None:
    result = _render(
        {
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "layers": [{"type": "bar", "query": "band", "y": "hi", "y_start": "lo"}],
            "field_labels": {"lo": "Low LBL"},
        },
        _BASE_ROWS,
        {
            "band": [
                {"month": "Jan", "lo": 2, "hi": 6},
                {"month": "Feb", "lo": 3, "hi": 8},
            ]
        },
    )
    assert result.chart_errors == []


def test_heatmap_multi_measure_entries_use_field_labels() -> None:
    spec = _spec(
        {
            "type": "heatmap",
            "x": "month",
            "y": ["all", "fix-linked"],
            "field_labels": {"fix-linked": "fix-linked"},
        },
        _WIDE_ROWS,
    )
    assert {"datum": "fix-linked"} in list(_values(spec, "color"))


def test_pie_legend_and_tooltip_rows_use_field_labels() -> None:
    spec = _spec(
        {
            "type": "pie",
            "theta": "MTok_cost",
            "color": "seg",
            "field_labels": _SEG_LABELS,
        },
        _SEG_ROWS,
    )
    assert _encoding(spec, "color")["title"] == "customer SEG"
    tooltip = _tooltip_text(spec)
    assert "cost/MTok: " in tooltip
    assert "MTok cost" not in tooltip


def test_donut_total_label_uses_field_label() -> None:
    result = compile_board(
        _board(
            {
                "type": "donut",
                "theta": "MTok_cost",
                "color": "seg",
                "field_labels": {"MTok_cost": "spend"},
            },
            _SEG_ROWS,
        )
    )
    assert result.board is not None, result.errors
    chart = result.board.charts["c"]
    assert isinstance(chart, PieChart)
    assert chart.total is not None
    assert chart.total.label == "Total spend"


def test_donut_total_label_is_not_prefixed_twice() -> None:
    result = compile_board(
        _board(
            {
                "type": "donut",
                "theta": "MTok_cost",
                "color": "seg",
                "field_labels": {"MTok_cost": "Total spend"},
            },
            _SEG_ROWS,
        )
    )
    assert result.board is not None, result.errors
    chart = result.board.charts["c"]
    assert isinstance(chart, PieChart)
    assert chart.total is not None
    assert chart.total.label == "Total spend"


def test_scatter_size_legend_title_uses_field_label() -> None:
    spec = _spec(
        {
            "type": "scatter",
            "x": "a",
            "y": "b",
            "size": "pop_cnt",
            "field_labels": {"pop_cnt": "residents"},
        },
        [{"a": 1, "b": 2, "pop_cnt": 3}, {"a": 4, "b": 5, "pop_cnt": 6}],
    )
    assert _encoding(spec, "size")["title"] == "residents"


def test_point_map_tooltip_fields_use_field_labels() -> None:
    spec = _spec(
        {
            "type": "point_map",
            "latitude": "lat",
            "longitude": "lon",
            "field_labels": {"pop_cnt": "residents", "lat": "LAT"},
        },
        [
            {"lat": 40.0, "lon": -100.0, "pop_cnt": 3},
            {"lat": 35.0, "lon": -90.0, "pop_cnt": 6},
        ],
    )
    tooltip = _tooltip_text(spec).split("\n")
    assert "residents" in tooltip
    assert "LAT" in tooltip
    assert "Longitude" in tooltip


def test_geoshape_legend_and_tooltip_use_field_labels() -> None:
    spec = _spec(
        {
            "type": "geoshape",
            "geo_source": "us-states",
            "lookup": "state_id",
            "value": "revenue",
            "field_labels": {"revenue": "Rev LBL", "state_id": "State LBL"},
        },
        [{"state_id": "06", "revenue": 5}, {"state_id": "36", "revenue": 7}],
    )
    tooltip = _tooltip_text(spec).split("\n")
    assert "Rev LBL" in tooltip
    assert "State LBL" in tooltip
    assert _encoding(spec, "color")["title"] == "Rev LBL"


def test_unknown_field_label_key_errors() -> None:
    result = _render(
        {
            "type": "bar",
            "x": "month",
            "y": "MTok_cost",
            "field_labels": {"revenue": "Revenue"},
        },
        _SEG_ROWS,
    )
    [error] = result.chart_errors
    assert error.code == "ERR-FIELD-LABELS-FIELD-NOT-FOUND"
    assert "'revenue'" in error.message


def test_field_labels_on_table_points_at_column_labels() -> None:
    result = compile_board(
        _board({"type": "table", "field_labels": {"MTok_cost": "cost/MTok"}}, _SEG_ROWS)
    )
    assert result.board is None
    [error] = result.errors
    assert "field_labels" in error.message
    assert error.hint is not None
    assert "style.columns.<col>.label" in error.hint


def test_table_hint_keys_on_the_family_not_the_chart_name() -> None:
    board = yaml.safe_dump(
        {
            "title": "T",
            "queries": {"q": {"type": "values", "rows": _SEG_ROWS}},
            "charts": {
                "table": {
                    "query": "q",
                    "type": "kpi",
                    "value": "MTok_cost",
                    "field_labels": {"MTok_cost": "cost/MTok"},
                }
            },
            "rows": ["table"],
        }
    )
    result = compile_board(board)
    assert result.board is None
    [error] = result.errors
    assert "style.columns" not in (error.hint or "")
