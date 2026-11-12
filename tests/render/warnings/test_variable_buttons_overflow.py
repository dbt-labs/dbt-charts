"""WARN_VARIABLE_BUTTONS_OVERFLOW reaches a rendered board.

A button row wider than its slot draws as its menu form and says so, once per
button row, naming where the variable is written.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.board import BoardRenderResult

_OPTIONS = "[Alpha segment, Bravo segment, Charlie segment, Delta segment]"


def _board(overflow: str) -> str:
    return f"""
width: 320
style:
  variables:
    overflow: {overflow}
variables:
  first:
    input: radio
    options:
      static: {_OPTIONS}
  second:
    input: radio
    options:
      static: {_OPTIONS}
text: Buttons.
"""


def _render(
    board_yaml: str, tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> BoardRenderResult:
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.execute.env import build_exec_env
    from dbt_charts.core.project import InMemoryBoard

    project = local_project(tmp_path)
    board = InMemoryBoard(board_yaml, path=project.path("charts/_t.yml"))
    return render_dashboard(
        board=board,
        env=build_exec_env(project, read_only=False),
        format="svg",
        project=project,
        result_cache=None,
    )


def test_each_overflowing_buttons_warns_once_with_its_path(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    from dbt_charts.core.diagnostics import WARN_VARIABLE_BUTTONS_OVERFLOW

    result = _render(_board("menu"), tmp_path, local_project)

    matches = [
        w for w in result.warnings if w.code == WARN_VARIABLE_BUTTONS_OVERFLOW.code
    ]
    assert len(matches) == 2
    assert {w.path for w in matches} == {"variables.first", "variables.second"}
    assert all("style.variables.overflow" in (w.fix or "") for w in matches)


def test_wrap_opts_out_of_the_warning(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    from dbt_charts.core.diagnostics import WARN_VARIABLE_BUTTONS_OVERFLOW

    result = _render(_board("wrap"), tmp_path, local_project)

    assert not [
        w for w in result.warnings if w.code == WARN_VARIABLE_BUTTONS_OVERFLOW.code
    ]
