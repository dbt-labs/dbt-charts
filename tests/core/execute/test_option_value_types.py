"""A caller's value for a query-backed control binds as its options column's type."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.agent_api import ProjectSession
from dbt_charts.agent_api.query import query_board
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute.lookup import lookup_names
from dbt_charts.core.execute.run import Failed, run_misses
from dbt_charts.core.project import InMemoryBoard

from ..._run import Run as BoardRun

LocalProject = Callable[..., FilesystemProject]

_BOARD = """
title: T
variables:
  flag:
    input: INPUT
    DATA_TYPE
    options:
      query: flag_options
queries:
  flag_options:
    source: db
    sql: SELECT * FROM (VALUES OPTIONS) AS t(value)
  q:
    source: db
    sql: SELECT typeof(BOUND) AS t
charts:
  c:
    query: q
    type: table
rows:
  - c
"""


def _project(
    tmp_path: Path,
    local_project: LocalProject,
    input_type: str = "select",
    data_type: str = "",
    options: str = "(true), (false)",
) -> FilesystemProject:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  db:\n    type: duckdb\n    path: ':memory:'\n"
    )
    value = "{{ flag }}" if input_type == "select" else "{{ flag }}[1]"
    board = (
        _BOARD.replace("INPUT", input_type)
        .replace("DATA_TYPE", data_type)
        .replace("BOUND", value)
        .replace("OPTIONS", options)
    )
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "b.yml").write_text(board)
    return local_project(tmp_path)


def _bound_type(project: FilesystemProject, variables: dict[str, str]) -> str:
    with ProjectSession.from_project(project) as session:
        result = query_board(
            "q", Path("charts/b.yml"), project, vars=variables, env=session.env
        )
    assert result.success, result
    return result.data[0]["t"]


@pytest.mark.parametrize(
    ("options", "value", "bound"),
    [
        ("(true), (false)", "true", "BOOLEAN"),
        ("(1), (2)", "2", "INTEGER"),
        ("(DATE '2024-01-01')", "2024-01-01", "DATE"),
    ],
)
def test_url_value_binds_as_the_options_column_type(
    tmp_path: Path, local_project: LocalProject, options: str, value: str, bound: str
) -> None:
    project = _project(tmp_path, local_project, options=options)
    assert _bound_type(project, {"flag": value}) == bound
    with ProjectSession.from_project(project) as session:
        result = session.render_board(
            board=InMemoryBoard(
                project.read_text("charts/b.yml"), path=project.path("charts/b.yml")
            ),
            variables={"flag": value},
        )
    assert result.status == "ok", result.board_error


def test_multiselect_members_bind_and_pass_the_option_check(
    tmp_path: Path, local_project: LocalProject
) -> None:
    project = _project(tmp_path, local_project, input_type="multiselect")
    with ProjectSession.from_project(project) as session:
        result = session.render_board(
            board=InMemoryBoard(
                project.read_text("charts/b.yml"), path=project.path("charts/b.yml")
            ),
            variables={"flag": '["true", "false"]'},
        )
    assert result.status == "ok", result.board_error
    assert _bound_type(project, {"flag": '["true"]'}) == "BOOLEAN"


def test_declared_data_type_wins_over_the_options_column(
    tmp_path: Path, local_project: LocalProject
) -> None:
    project = _project(tmp_path, local_project, data_type="data_type: string")
    assert _bound_type(project, {"flag": "true"}) == "VARCHAR"


def test_a_failed_options_query_is_not_stored_as_the_consumers_failure(
    tmp_path: Path, local_project: LocalProject
) -> None:
    project = _project(
        tmp_path, local_project, options="(true)) AS x(value) JOIN missing"
    )
    with ProjectSession.from_project(project) as session:
        loaded = compile_board(project.read_text("charts/b.yml")).board
        assert loaded is not None
        run = BoardRun(loaded, env=session.env, variables={"flag": "true"})
        found = lookup_names(run.ctx, run.ledger, ["q"], resume_incremental=False)
        outcome = run_misses(run.ctx, run.ledger, found, None)["q"]
    assert isinstance(outcome, Failed)
    assert outcome.record is None
