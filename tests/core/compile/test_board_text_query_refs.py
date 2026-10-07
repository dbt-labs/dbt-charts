"""Board text query references: compile-time scope and errors."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import CompileResult, compile, compile_file
from dbt_charts.core.execute.collect import collect_all_query_names

_QUERIES = """
queries:
  summary:
    columns: [x]
    values:
      - [1]
  other:
    columns: [y]
    values:
      - [2]
"""


def _codes(result: CompileResult) -> list[str]:
    return [e.code for e in result.errors]


def test_text_referenced_query_joins_board_queries() -> None:
    result = compile("text: '{{ queries.summary.one.x }}'\n" + _QUERIES)
    assert result.success, result.errors
    assert result.board is not None
    assert "summary" in result.board.queries


def test_text_referenced_query_is_collected_for_pre_execution() -> None:
    result = compile("text: '{{ queries.summary.one.x }}'\n" + _QUERIES)
    assert result.board is not None
    assert collect_all_query_names(result.board) == {"summary"}


def test_nested_board_text_references_are_collected() -> None:
    result = compile(
        _QUERIES
        + """
rows:
  - text: '{{ queries.other.one.y }}'
    title: nested
    cols: []
"""
    )
    assert result.success, result.errors
    assert result.board is not None
    assert "other" in collect_all_query_names(result.board)


def test_unknown_query_names_the_defined_ones() -> None:
    result = compile("text: '{{ queries.nope.one.x }}'\n" + _QUERIES)
    assert _codes(result) == ["ERR-TEXT-UNKNOWN-QUERY"]
    message = result.errors[0].message
    assert "'nope'" in message
    assert "Defined queries: other, summary" in message


@pytest.mark.parametrize(
    "expr", ["queries[name]", "queries | list", "queries"], ids=str
)
def test_non_literal_use_is_rejected(expr: str) -> None:
    result = compile("text: '{{ " + expr + " }}'\n" + _QUERIES)
    assert _codes(result) == ["ERR-TEXT-QUERIES-NONLITERAL"]


def test_query_defined_only_in_extends_parent_resolves(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    charts = tmp_path / "charts"
    charts.mkdir()
    (tmp_path / "dbt_charts.yml").write_text("name: t\n", encoding="utf-8")
    (tmp_path / "base.yaml").write_text(_QUERIES, encoding="utf-8")
    (charts / "board.yaml").write_text(
        "extends: base\ntext: '{{ queries.summary.one.x }}'\n", encoding="utf-8"
    )
    result = compile_file(
        local_project(tmp_path).path("charts/board.yaml").read_board()
    )
    assert result.success, result.errors
    assert result.board is not None
    assert "summary" in result.board.queries
