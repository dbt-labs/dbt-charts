"""`field_labels:` replaces the text derived from a field name on every surface
that paints one, verbatim; `x_label`/`y_label` and `layer.label` still win."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

Rows = list[dict[str, Any]]


from ..._engine_render import render_pie_svg


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


_WIDE_ROWS = [
    {"month": "Jan", "all": 5, "fix-linked": 2},
    {"month": "Feb", "all": 7, "fix-linked": 3},
]
_WIDE = {"type": "line", "x": "month", "y": ["all", "fix-linked"]}
# A measure whose derived label is its own raw name (derived text is
# sentence-cased, so only a capitalized name is) can collide with an authored one.
_CAPITAL_ROWS = [
    {"month": "Jan", "All": 5, "fix-linked": 2},
    {"month": "Feb", "All": 7, "fix-linked": 3},
]
_CAPITAL = {**_WIDE, "y": ["All", "fix-linked"]}


@pytest.mark.parametrize("family", ["line", "bar", "area"])
def test_wide_measure_entry_paints_field_label_in_svg(family: str) -> None:
    labeled = {**_WIDE, "type": family, "field_labels": {"fix-linked": "fix-linked"}}
    svg = _render(labeled, _WIDE_ROWS).output
    assert ">fix-linked<" in svg
    assert "Fix linked" not in svg
    assert "Fix linked" in _render({**_WIDE, "type": family}, _WIDE_ROWS).output


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


def test_colliding_authored_wide_label_errors() -> None:
    result = _render({**_CAPITAL, "field_labels": {"fix-linked": "All"}}, _CAPITAL_ROWS)
    [error] = result.chart_errors
    assert error.code == "ERR-FIELD-LABELS-WIDE-COLLISION"
    assert "'All'" in error.message


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
                "q": {"type": "values", "rows": _CAPITAL_ROWS},
                "o": {
                    "type": "values",
                    "rows": [
                        {"month": "Jan", "orders_cnt": 1, "orders (Count)": 2},
                        {"month": "Feb", "orders_cnt": 3, "orders (Count)": 4},
                    ],
                },
            },
            "charts": {
                "a": {**_CAPITAL, "query": "q", "field_labels": {"fix-linked": "All"}},
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


def _resolved(chart: dict[str, Any], rows: Rows) -> Any:
    result = compile_board(_board(chart, rows))
    assert result.board is not None, result.errors
    return resolve(
        result.board.charts["c"],
        rows,
        chart_style_context=result.board.chart_style_context,
    )


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


def test_pie_legend_and_tooltip_rows_use_field_labels() -> None:
    chart = _resolved(
        {
            "type": "pie",
            "theta": "MTok_cost",
            "color": "seg",
            "field_labels": _SEG_LABELS,
            "style": {"legend": {"visible": True}},
        },
        _SEG_ROWS,
    )
    svg = render_pie_svg(chart, _SEG_ROWS, width=chart.resolution_width)
    assert ">customer SEG</text>" in svg
    tooltips = re.findall(
        r'aria-label="([^"]+)"[^>]*aria-roledescription="arc mark"', svg
    )
    assert tooltips
    assert all("cost/MTok: " in t and "MTok cost" not in t for t in tooltips), tooltips


def test_donut_total_label_uses_field_label() -> None:
    chart = _resolved(
        {
            "type": "donut",
            "theta": "MTok_cost",
            "color": "seg",
            "field_labels": {"MTok_cost": "spend"},
        },
        _SEG_ROWS,
    )
    assert chart.total is not None
    assert chart.total.label == "Total spend"


def test_donut_total_label_is_not_prefixed_twice() -> None:
    chart = _resolved(
        {
            "type": "donut",
            "theta": "MTok_cost",
            "color": "seg",
            "field_labels": {"MTok_cost": "Total spend"},
        },
        _SEG_ROWS,
    )
    assert chart.total is not None
    assert chart.total.label == "Total spend"


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
