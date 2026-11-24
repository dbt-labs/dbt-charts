"""A tab whose `visible:` is false is left out of the tab bar."""

from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.diagnostics.codes_render import ERR_TAB_UNKNOWN
from dbt_charts.core.diagnostics.codes_unknown import ERR_INTERNAL
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render.render_result import RenderResult
from dbt_charts.core.render.renderer import render

from ..._run import Run

LocalProject = Callable[..., FilesystemProject]

_BOARD = """
variables:
  show_admin:
    input: checkbox
    default: false
tabs:
  Admin:
    visible: show_admin
    text: admin body
  Overview:
    text: overview body
  Legacy:
    visible: false
    text: legacy body
"""


def _render(
    local_project: LocalProject,
    variables: dict[str, object] | None = None,
    *,
    tab: str | None = None,
) -> RenderResult:
    """`_BOARD` rendered with `variables`, and its tab variable set to `tab`."""
    board = compile(_BOARD).board
    assert board is not None and board.layout.tab_variable is not None
    given = dict(variables or {})
    if tab is not None:
        given[board.layout.tab_variable] = tab
    run = Run(board, env=build_exec_env(local_project(Path.cwd())))
    return render(board, run.ctx, run.ledger, variables=given)


def _svg(result: RenderResult) -> str:
    assert isinstance(result.output, str), result.board_error
    return result.output


def test_hidden_tab_is_left_out_of_the_bar(local_project: LocalProject) -> None:
    svg = _svg(_render(local_project))
    assert "Overview" in svg
    assert "Legacy" not in svg
    assert "Admin" not in svg


def test_variable_shows_a_conditional_tab(local_project: LocalProject) -> None:
    svg = _svg(_render(local_project, variables={"show_admin": True}))
    assert "Admin" in svg
    assert "Legacy" not in svg


def test_a_hidden_first_tab_opens_the_first_drawn_tab(
    local_project: LocalProject,
) -> None:
    svg = _svg(_render(local_project))
    assert "overview body" in svg
    assert "admin body" not in svg


def test_selecting_a_hidden_tab_is_an_unknown_tab(
    local_project: LocalProject,
) -> None:
    error = _render(local_project, tab="legacy").board_error
    assert error is not None
    assert error.code == ERR_TAB_UNKNOWN.code
    assert error.fields["slugs"] == ["overview"]


def test_every_tab_hidden_renders_no_bar(local_project: LocalProject) -> None:
    board = compile(
        _BOARD.replace("  Overview:\n", "  Overview:\n    visible: false\n")
    ).board
    assert board is not None
    run = Run(board, env=build_exec_env(local_project(Path.cwd())))
    svg = _svg(render(board, run.ctx, run.ledger))
    assert 'role="tab"' not in svg


_PROBE_BOARD = """
queries:
  flags:
    columns: [show]
    values:
      - [false]
tabs:
  Overview:
    text: overview body
  Admin:
    {pane_queries}
    visible:
      query: flags
      column: show
    text: admin body
"""


def _probe_svg(local_project: LocalProject, board_yaml: str) -> str:
    board = compile(board_yaml).board
    assert board is not None
    run = Run(board, env=build_exec_env(local_project(Path.cwd())))
    return _svg(render(board, run.ctx, run.ledger))


def test_a_query_probe_hides_a_tab(local_project: LocalProject) -> None:
    svg = _probe_svg(local_project, _PROBE_BOARD.replace("{pane_queries}", ""))
    assert "Overview" in svg
    assert "Admin" not in svg


def test_a_probe_reads_a_query_declared_in_its_closed_pane(
    local_project: LocalProject,
) -> None:
    board = _PROBE_BOARD.replace(
        "queries:\n  flags:\n    columns: [show]\n    values:\n      - [false]\n",
        "",
    ).replace(
        "{pane_queries}",
        "queries:\n        flags:\n          columns: [show]\n"
        "          values:\n            - [true]",
    )
    svg = _probe_svg(local_project, board)
    assert "Admin" in svg
    assert "admin body" not in svg


def test_a_condition_that_is_no_bool_is_a_board_error(
    local_project: LocalProject,
) -> None:
    result = _render(local_project, variables={"show_admin": "maybe"})
    assert result.board_error is not None
    assert result.board_error.code == ERR_INTERNAL.code


_KPI_BOARD = """
variables:
  show_admin:
    input: checkbox
    default: false
queries:
  overview_q:
    columns: [n]
    values:
      - [41]
  admin_q:
    columns: [n]
    values:
      - [97]
tabs:
  Admin:
    visible: show_admin
    rows:
      - admin_kpi:
          type: kpi
          query: admin_q
          value: n
  Overview:
    rows:
      - overview_kpi:
          type: kpi
          query: overview_q
          value: n
"""


def test_a_hidden_first_tab_draws_the_first_drawn_tabs_kpi(
    local_project: LocalProject,
) -> None:
    board = compile(_KPI_BOARD).board
    assert board is not None
    run = Run(board, env=build_exec_env(local_project(Path.cwd())))
    svg = _svg(render(board, run.ctx, run.ledger))
    assert ">41<" in svg
    assert ">97<" not in svg
