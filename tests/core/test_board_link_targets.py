"""check_link_targets is wired into render_dashboard, the agent's edit loop."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import BoardRenderResult, render_dashboard
from dbt_charts.core.execute.env import ExecEnv
from dbt_charts.core.project import InMemoryBoard
from dbt_charts.core.render.render_result import RenderResult

_CODE = "WARN-LINK-TARGET-NOT-FOUND"
_BOARD = """\
queries:
  people:
    columns: [email]
    values:
      - [a@example.com]
charts:
  people_table:
    type: table
    query: people
    style:
      columns:
        email:
          link: "{link}"
rows:
  - people_table
"""


def _project(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> FilesystemProject:
    project = local_project(tmp_path)
    project.write_text("charts/user-360.yml", "rows: []\n")
    return project


def _compile_only(project: FilesystemProject, link: str) -> BoardRenderResult:
    return render_dashboard(
        board=InMemoryBoard(
            _BOARD.format(link=link), path=project.path("charts/people.yml")
        ),
        project=project,
        as_link=True,
        result_cache=None,
    )


def _full_render(project: FilesystemProject, link: str) -> BoardRenderResult:
    render_result = RenderResult(output='{"id": "test", "title": "Board", "items": []}')
    with (
        patch("dbt_charts.core.board.render", return_value=render_result),
        patch("dbt_charts.core.board.make_context"),
    ):
        return render_dashboard(
            board=InMemoryBoard(
                _BOARD.format(link=link), path=project.path("charts/people.yml")
            ),
            env=MagicMock(spec=ExecEnv),
            format="json",
            project=project,
            result_cache=None,
        )


def _codes(result: BoardRenderResult) -> list[str]:
    return [w.code for w in result.warnings]


def test_missing_link_target_warns_on_the_compile_only_arm(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = _project(tmp_path, local_project)
    assert _CODE in _codes(_compile_only(project, "/dataface/d/user-360"))


def test_existing_link_target_is_clean_on_the_compile_only_arm(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = _project(tmp_path, local_project)
    assert _CODE not in _codes(_compile_only(project, "/user-360?id={{ email }}"))


def test_missing_link_target_warns_in_the_full_render_arm(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = _project(tmp_path, local_project)
    assert _CODE in _codes(_full_render(project, "/dataface/d/user-360"))


def test_existing_link_target_is_clean_in_the_full_render_arm(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = _project(tmp_path, local_project)
    assert _CODE not in _codes(_full_render(project, "/user-360?id={{ email }}"))
