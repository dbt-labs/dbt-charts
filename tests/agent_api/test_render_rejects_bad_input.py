"""A chart naming a missing column and a bad variable override fail loudly.

Both used to render ``status: ok`` with empty output: a typo'd column plotted
nothing under a misleading all-NULL warning, and a typo'd variable name or an
out-of-range select value ran the queries against nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.agent_api import ProjectSession
from dbt_charts.agent_api.query import query_board
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import BoardRenderResult
from dbt_charts.core.project import InMemoryBoard

_BOARD = """
title: T
variables:
  region:
    input: select
    default: US
    options:
      static: [US, EU]
queries:
  q:
    type: values
    rows:
      - {rep: Jim, bookings: 10, region: US}
      - {rep: Phyllis, bookings: 20, region: EU}
charts:
  c:
    query: q
    type: bar
    x: rep
    y: Y_COLUMN
rows:
  - c
"""

LocalProject = Callable[..., FilesystemProject]


def _render(
    tmp_path: Path,
    local_project: LocalProject,
    *,
    y: str = "bookings",
    variables: dict[str, str] | None = None,
    strict_variables: bool = True,
) -> BoardRenderResult:
    project = local_project(tmp_path)
    board = InMemoryBoard(
        _BOARD.replace("Y_COLUMN", y), path=project.path("charts/b.yml")
    )
    with ProjectSession.from_project(project) as session:
        return session.render_board(
            board=board,
            variables=variables,
            format="json",
            strict_variables=strict_variables,
        )


def _codes(result: BoardRenderResult) -> set[str]:
    board_error = {result.board_error.code} if result.board_error else set()
    return (
        {d.code for d in result.chart_errors}
        | board_error
        | {d.code for d in result.validation_errors}
    )


def test_healthy_board_renders_ok(tmp_path: Path, local_project: LocalProject) -> None:
    assert _render(tmp_path, local_project).status == "ok"


def test_missing_chart_column_is_a_chart_error(
    tmp_path: Path, local_project: LocalProject
) -> None:
    result = _render(tmp_path, local_project, y="booking")
    assert "ERR-CHART-COLUMN-NOT-IN-RESULT" in _codes(result), result
    assert result.status != "ok"
    message = next(d.message for d in result.chart_errors if "booking" in d.message)
    assert "'booking'" in message
    assert "bookings" in message and "rep" in message  # available columns
    assert "WARN-Y-ENCODING-MOSTLY-NULL" not in {w.code for w in result.warnings}


def test_missing_column_in_list_y_is_a_chart_error(
    tmp_path: Path, local_project: LocalProject
) -> None:
    result = _render(tmp_path, local_project, y="[bookings, bookin]")
    assert "ERR-CHART-COLUMN-NOT-IN-RESULT" in _codes(result)


def test_missing_pivot_field_keeps_the_pivot_diagnostic(
    tmp_path: Path, local_project: LocalProject
) -> None:
    project = local_project(tmp_path)
    board = InMemoryBoard(
        """
title: T
queries:
  q:
    type: values
    rows:
      - {rep: Jim, quarter: Q1, bookings: 10}
charts:
  c:
    query: q
    type: table
    rows: [rep]
    columns: [ghost]
    values: [bookings]
rows:
  - c
""",
        path=project.path("charts/b.yml"),
    )
    with ProjectSession.from_project(project) as session:
        result = session.render_board(board=board, format="json")

    codes = _codes(result)
    assert "ERR-TABLE-PIVOT-FIELD-NOT-FOUND" in codes, result
    assert "ERR-CHART-COLUMN-NOT-IN-RESULT" not in codes


def test_missing_kpi_value_keeps_the_kpi_diagnostic(
    tmp_path: Path, local_project: LocalProject
) -> None:
    project = local_project(tmp_path)
    board = InMemoryBoard(
        """
title: T
queries:
  q:
    type: values
    rows:
      - {revenue: 10}
charts:
  c:
    query: q
    type: kpi
    value: nope
rows:
  - c
""",
        path=project.path("charts/b.yml"),
    )
    with ProjectSession.from_project(project) as session:
        result = session.render_board(board=board, format="json")

    codes = _codes(result)
    assert "ERR-KPI-VALUE-COLUMN-NOT-FOUND" in codes, result
    assert "ERR-CHART-COLUMN-NOT-IN-RESULT" not in codes


def test_undeclared_variable_override_fails(
    tmp_path: Path, local_project: LocalProject
) -> None:
    result = _render(tmp_path, local_project, variables={"regoin": "US"})
    assert result.status == "failed"
    assert "ERR-UNKNOWN-VARIABLE-OVERRIDE" in _codes(result)
    assert result.board_error is not None
    assert "regoin" in result.board_error.message
    assert "region" in result.board_error.message  # the declared names


def test_select_value_outside_static_options_fails(
    tmp_path: Path, local_project: LocalProject
) -> None:
    result = _render(tmp_path, local_project, variables={"region": "APAC"})
    assert result.status == "failed"
    assert "ERR-VARIABLE-VALUE-NOT-IN-OPTIONS" in _codes(result)
    assert result.board_error is not None
    assert "US" in result.board_error.message and "EU" in result.board_error.message


def test_valid_override_renders(tmp_path: Path, local_project: LocalProject) -> None:
    assert _render(tmp_path, local_project, variables={"region": "EU"}).status == "ok"


_QUERY_BOARD = """
title: T
variables:
  region:
    input: select
    default: US
    options:
      static: [US, EU]
queries:
  q:
    type: values
    rows:
      - {region: US}
charts:
  c:
    query: q
    type: table
rows:
  - c
"""


@pytest.mark.parametrize(
    ("variables", "code"),
    [
        ({"regoin": "US"}, "ERR-UNKNOWN-VARIABLE-OVERRIDE"),
        ({"region": "APAC"}, "ERR-VARIABLE-VALUE-NOT-IN-OPTIONS"),
    ],
)
def test_query_board_rejects_the_same_overrides(
    tmp_path: Path,
    local_project: LocalProject,
    variables: dict[str, str],
    code: str,
) -> None:
    project = local_project(tmp_path)
    project.write_text("charts/b.yml", _QUERY_BOARD)
    with ProjectSession.from_project(project) as session:
        result = query_board(
            "q",
            Path("charts/b.yml"),
            project,
            vars=variables,
            adapter_registry=session.adapter_registry,
        )
    assert result.success is False
    assert result.error_code == code


_QUERY_OPTIONS_BOARD = """
title: T
variables:
  title:
    input: select
    options:
      query: title_options
queries:
  title_options:
    type: values
    rows:
      - {value: Batter Up}
      - {value: Home Run}
  q:
    type: values
    rows:
      - {rep: Jim, bookings: 10}
charts:
  c:
    query: q
    type: bar
    x: rep
    y: bookings
rows:
  - c
"""


def test_select_value_outside_query_options_fails_on_render(
    tmp_path: Path, local_project: LocalProject
) -> None:
    project = local_project(tmp_path)
    board = InMemoryBoard(_QUERY_OPTIONS_BOARD, path=project.path("charts/b.yml"))
    with ProjectSession.from_project(project) as session:
        ok = session.render_board(board=board, variables={"title": "Home Run"})
        bad = session.render_board(board=board, variables={"title": "Batter Upp"})
    assert ok.status == "ok"
    assert bad.status == "failed"
    assert bad.board_error is not None
    assert bad.board_error.code == "ERR-VARIABLE-VALUE-NOT-IN-OPTIONS"
    assert "Batter Up" in bad.board_error.message


@pytest.mark.parametrize(
    "variables", [{"regoin": "US"}, {"region": "APAC"}], ids=["unknown", "option"]
)
def test_non_strict_variables_tolerate_a_browser_query_string(
    tmp_path: Path, local_project: LocalProject, variables: dict[str, str]
) -> None:
    result = _render(
        tmp_path, local_project, variables=variables, strict_variables=False
    )
    assert result.status == "ok", result.board_error
