"""Authored keys survive the dump-and-revalidate sites `compile_file` runs.

The meta pass, `extends`, inline rows and tabs all dump a parsed board and
validate it again, so a key the grammar writes under one name must read back
under the same name.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile, compile_file
from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.compile.models.board.normalized import Board

_TABLE = """
      type: table
      query: q
      conditional_formatting:
        n:
          when:
            - in: [1, 2]
              background: '#fee2e2'
"""

_QUERIES = """
queries:
  q:
    type: values
    rows:
      - {n: 1}
"""

_BOARDS = {
    "top-level": f"""
title: T
{_QUERIES}
charts:
  t:{_TABLE}
rows:
  - t
""",
    "inline-row": f"""
title: T
{_QUERIES}
rows:
  - {_TABLE.strip().replace(chr(10) + "      ", chr(10) + "    ")}
""",
    "tab": f"""
title: T
{_QUERIES}
tabs:
  items:
    - title: One
      rows:
        - {_TABLE.strip().replace(chr(10) + "      ", chr(10) + "          ")}
""",
}


def _compile(tmp_path: Path, board_yaml: str) -> Board:
    reset_config()
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  db:\n    type: duckdb\n    path: warehouse.duckdb\n"
    )
    boards = tmp_path / "charts"
    boards.mkdir(exist_ok=True)
    (boards / "board.yml").write_text(dedent(board_yaml))
    project = FilesystemProject(tmp_path)
    result = compile_file(project.path("charts/board.yml").read_board())
    assert result.success, f"Compilation failed: {result.errors}"
    return result.board


@pytest.mark.parametrize("placement", _BOARDS)
def test_in_rule_compiles(tmp_path: Path, placement: str) -> None:
    _compile(tmp_path, _BOARDS[placement])


def test_schema_query_compiles(tmp_path: Path) -> None:
    board = _compile(
        tmp_path,
        """
        title: T
        queries:
          s:
            type: schema
            source: db
            schema: main
            fields: [a]
        charts:
          c:
            query: s
            type: table
        rows:
          - c
        """,
    )
    assert board.queries["s"].schema_name == "main"


def test_inline_chart_visible_false_reaches_the_layout_item(tmp_path: Path) -> None:
    board = _compile(
        tmp_path,
        f"""
        title: T
        {_QUERIES.replace(chr(10), chr(10) + "        ")}
        rows:
          - type: table
            query: q
            visible: false
        """,
    )
    assert [item.visible for item in board.layout.items] == [False]


def test_cross_file_variable_rejects_compiler_owned_key(tmp_path: Path) -> None:
    (tmp_path / "shared.yaml").write_text(
        "title: Shared\nvariables:\n  v:\n    input: select\n"
        "    input_auto_detected: true\n"
    )
    result = compile(
        "title: T\nvariables:\n  v: shared.yaml.variables.v\nrows: []\n",
        base_dir=FilesystemProject(tmp_path).directory(),
        file="board.yaml",
    )
    assert not result.success
    assert "input_auto_detected" in str(result.errors)
