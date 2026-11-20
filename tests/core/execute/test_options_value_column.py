"""`options.column` names the value column of a variable's options query."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dbt_charts.agent_api import BoardRenderResult, ProjectSession
from dbt_charts.agent_api.query import query_board
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.project import InMemoryBoard

LocalProject = Callable[..., FilesystemProject]

_BOARD = """
title: T
variables:
  status:
    input: select
    options:
      query: status_options
      column: COLUMN
queries:
  status_options:
    source: db
    sql: SELECT * FROM (VALUES ('open', 1), ('closed', 2)) AS t(status_name, status_id)
  q:
    source: db
    sql: SELECT typeof({{ status }}) AS t
charts:
  c:
    query: q
    type: table
rows:
  - c
"""


def _project(
    tmp_path: Path, local_project: LocalProject, column: str
) -> FilesystemProject:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  db:\n    type: duckdb\n    path: ':memory:'\n"
    )
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "b.yml").write_text(_BOARD.replace("COLUMN", column))
    return local_project(tmp_path)


def _render(project: FilesystemProject, value: str) -> BoardRenderResult:
    with ProjectSession.from_project(project) as session:
        return session.render_board(
            board=InMemoryBoard(
                project.read_text("charts/b.yml"), path=project.path("charts/b.yml")
            ),
            variables={"status": value},
        )


def test_options_column_picks_the_value_column(
    tmp_path: Path, local_project: LocalProject
) -> None:
    project = _project(tmp_path, local_project, "status_id")
    result = _render(project, "2")
    assert result.status == "ok", result.board_error
    with ProjectSession.from_project(project) as session:
        bound = query_board(
            "q",
            Path("charts/b.yml"),
            project,
            vars={"status": "2"},
            env=session.env,
        )
    assert bound.success, bound
    assert bound.data[0]["t"] == "INTEGER"


def test_options_column_missing_from_the_query_raises(
    tmp_path: Path, local_project: LocalProject
) -> None:
    project = _project(tmp_path, local_project, "status_code")
    result = _render(project, "")
    assert result.status != "ok"
    assert "ERR-VARIABLE-OPTIONS-COLUMN-MISSING" in str(result.board_error)


def test_a_board_with_options_label_column_still_compiles() -> None:
    compiled = compile_board(
        _BOARD.replace("COLUMN", "status_id").replace(
            "      column: status_id\n",
            "      column: status_id\n      label_column: status_name\n",
        )
    )
    assert compiled.board is not None, compiled
    options = compiled.board.variables["status"].options
    assert options is not None
    assert options.column == "status_id"
