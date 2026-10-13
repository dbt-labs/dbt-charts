"""A dbt column's ``meta.format`` prints through board SQL, end to end."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal
from unittest.mock import MagicMock

import duckdb

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.board.normalized import Board, LayoutItem
from dbt_charts.core.compile.models.chart.normalized.table import TableChart
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.column_formats import (
    known_format_names,
    with_dbt_column_formats,
)
from dbt_charts.core.render import render
from dbt_charts.core.render.render_result import RenderResult

_BOARD = """
title: T
style:
  formats:
    currency: '$,.0f'
    integer: ',.0f'
queries:
  q:
    source: s
    sql: |
      select month, sum(amount) as revenue
      from {{ ref('orders') }}
      group by 1
      order by 1
%(formats)s
charts:
  c:
    query: q
%(chart)s
rows:
  - c
"""
_BAR = "    type: bar\n    x: month\n    y: revenue\n"
_TABLE = "    type: table\n"


def _manifest(amount_meta: dict[str, object]) -> str:
    return json.dumps(
        {
            "metadata": {
                "dbt_schema_version": (
                    "https://schemas.getdbt.com/dbt/manifest/v12.json"
                )
            },
            "nodes": {
                "model.p.orders": {
                    "resource_type": "model",
                    "name": "orders",
                    "schema": "main",
                    "relation_name": "orders",
                    "columns": {"amount": {"name": "amount", "meta": amount_meta}},
                }
            },
            "sources": {},
        }
    )


def _prepare(tmp_path: Path, meta: dict[str, object]) -> ProjectSourcesConfig:
    (tmp_path / "target").mkdir(exist_ok=True)
    (tmp_path / "target" / "manifest.json").write_text(_manifest(meta))
    db = tmp_path / "w.duckdb"
    if not db.exists():
        con = duckdb.connect(str(db))
        con.execute(
            "create table orders as select * from (values "
            "(date '2026-01-01', 1234567), (date '2026-02-01', 2345678)) "
            "t(month, amount)"
        )
        con.close()
    return ProjectSourcesConfig(sources={"s": {"type": "duckdb", "path": str(db)}})


def _render(
    tmp_path: Path,
    chart: str,
    *,
    meta: dict[str, object],
    template: str = _BOARD,
    formats: str = "",
    output_format: Literal["svg", "json", "yaml", "text"] = "svg",
) -> RenderResult:
    sources = _prepare(tmp_path, meta)
    result = compile_board(
        template % {"chart": chart, "formats": formats},
        project_sources=sources,
    )
    assert result.board is not None, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(tmp_path)),
        query_registry=result.query_registry,
    )
    return render(result.board, executor, format=output_format)


def _svg(tmp_path: Path, chart: str, **kwargs: Any) -> str:
    result = _render(tmp_path, chart, **kwargs)
    assert result.output is not None, result.chart_errors
    return result.output


def test_a_summed_dbt_column_prints_in_its_format_on_an_axis_and_a_table(
    tmp_path: Path,
) -> None:
    meta = {"format": "currency"}
    assert "$1,000,000" in _svg(tmp_path, _BAR, meta=meta)
    table = _svg(tmp_path, _TABLE, meta=meta)
    assert ">$</tspan>" in table and "1,234,567" in table


def test_without_meta_format_the_engine_number_prints(tmp_path: Path) -> None:
    assert "$1,000,000" not in _svg(tmp_path, _BAR, meta={})


def test_the_querys_own_formats_beat_the_dbt_column(tmp_path: Path) -> None:
    svg = _svg(
        tmp_path,
        _TABLE,
        meta={"format": "currency"},
        formats="    formats:\n      revenue: integer\n",
    )
    assert ">$</tspan>" not in svg and "1,234,567" in svg


def test_a_meta_format_the_board_lacks_fails_the_chart_that_reads_it(
    tmp_path: Path,
) -> None:
    result = _render(tmp_path, _BAR, meta={"format": "curency"})
    [error] = result.chart_errors
    assert error.code == "ERR-DBT-COLUMN-FORMAT-UNKNOWN"
    assert "orders" in error.message and "amount" in error.message
    assert "curency" in error.message


def test_a_sibling_chart_still_renders_beside_the_failed_one(tmp_path: Path) -> None:
    template = (
        _BOARD.replace(
            "queries:\n  q:",
            "queries:\n  v:\n    type: values\n    rows:\n      - {n: 1}\n  q:",
        )
        .replace(
            "charts:\n  c:",
            "charts:\n  d:\n    query: v\n    type: kpi\n    value: n\n"
            "    label: SIBLINGTITLE\n  c:",
        )
        .replace("rows:\n  - c\n", "rows:\n  - c\n  - d\n")
    )
    result = _render(tmp_path, _BAR, meta={"format": "curency"}, template=template)
    assert result.output is not None, result.board_error
    assert "SIBLINGTITLE" in result.output
    assert [e.code for e in result.chart_errors] == ["ERR-DBT-COLUMN-FORMAT-UNKNOWN"]


def test_a_non_string_meta_format_fails_only_the_chart_that_reads_it(
    tmp_path: Path,
) -> None:
    broken = _render(tmp_path, _BAR, meta={"format": None})
    assert [e.code for e in broken.chart_errors] == ["ERR-DBT-COLUMN-FORMAT-UNKNOWN"]
    unrelated = _BOARD.replace("sum(amount) as revenue", "count(*) as revenue")
    result = _render(tmp_path, _TABLE, meta={"format": None}, template=unrelated)
    assert result.output is not None and result.chart_errors == []


def test_a_bad_meta_format_under_a_column_the_query_overrides_never_raises(
    tmp_path: Path,
) -> None:
    svg = _svg(
        tmp_path,
        _TABLE,
        meta={"format": "curency"},
        formats="    formats:\n      revenue: integer\n",
    )
    assert "1,234,567" in svg


def test_the_text_format_resolves_the_dbt_format_too(tmp_path: Path) -> None:
    result = _render(tmp_path, _BAR, meta={"format": "currency"}, output_format="text")
    assert result.output is not None, result.board_error
    assert "$" in result.output


def test_a_derived_column_takes_the_casing_the_result_returns(
    tmp_path: Path,
) -> None:
    result = compile_board(
        _BOARD.replace("sum(amount) as revenue", "sum(amount) as Revenue")
        % {"chart": _TABLE, "formats": ""},
        project_sources=ProjectSourcesConfig(
            sources={"s": {"type": "duckdb", "path": str(tmp_path / "x.duckdb")}}
        ),
    )
    assert result.board is not None, result.errors
    executor = MagicMock()
    executor.board = result.board
    executor.adapter_registry.project.sources.sources = {}
    executor.adapter_registry.dbt_refs.column_formats.return_value = {
        "orders": {"amount": "currency"}
    }
    executor.execute_query.return_value = [{"month": "2026-01-01", "revenue": 1}]

    widened = with_dbt_column_formats(result.board, executor, {})

    chart = widened.layout.items[0].chart
    assert isinstance(chart, TableChart)
    assert chart.query is not None and chart.query.formats == {"revenue": "currency"}


_ALIAS_STYLE = "style:\n  formats:\n    arr_money: '$,.1f'\n"
_BASE_STYLE = "style:\n  formats:\n    currency: '$,.0f'\n    integer: ',.0f'\n"


def _two_boards(
    tmp_path: Path, meta: dict[str, object], nested_style: str
) -> tuple[Board, Executor]:
    sources = _prepare(tmp_path, meta)
    nested = compile_board(
        _BOARD.replace(_BASE_STYLE, nested_style) % {"chart": _TABLE, "formats": ""},
        project_sources=sources,
    )
    root = compile_board(
        _BOARD % {"chart": _TABLE, "formats": ""}, project_sources=sources
    )
    assert nested.board is not None and root.board is not None
    tree = root.board.model_copy(
        update={
            "layout": root.board.layout.model_copy(
                update={
                    "items": [
                        *root.board.layout.items,
                        LayoutItem(
                            type="board", board=nested.board, width=600, height=300
                        ),
                    ]
                }
            )
        }
    )
    executor = Executor(
        tree,
        adapter_registry=build_adapter_registry(FilesystemProject(tmp_path)),
        query_registry=root.query_registry,
    )
    return tree, executor


def test_a_name_only_a_nested_board_defines_fails_the_chart_in_every_placement(
    tmp_path: Path,
) -> None:
    tree, executor = _two_boards(tmp_path, {"format": "arr_money"}, _ALIAS_STYLE)

    result = render(tree, executor, format="svg")

    assert result.chart_errors
    assert {e.code for e in result.chart_errors} == {"ERR-DBT-COLUMN-FORMAT-UNKNOWN"}


_GRAIN_BOARD = """
title: T
{style}
queries:
  q:
    columns: [a]
    values: [[1]]
charts:
  c:
    query: q
    type: table
rows:
  - c
"""


def test_the_engine_kind_names_are_known_and_a_grain_name_only_once_redefined() -> None:
    plain = compile_board(_GRAIN_BOARD.format(style="")).board
    assert plain is not None
    names = known_format_names(plain.chart_style_context)
    assert {"id", "duration", "bytes", "latitude", "longitude", "rating"} <= names
    assert "date_month" not in names

    redefined = compile_board(
        _GRAIN_BOARD.format(style='style:\n  formats:\n    date_month: "%b %Y"')
    ).board
    assert redefined is not None
    assert "date_month" in known_format_names(redefined.chart_style_context)
    assert "date_week" not in known_format_names(redefined.chart_style_context)
