"""Invariant: CLI render verb must call agent_api for ALL formats, never core directly."""

from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.parametrize(
    ("format", "mock_data"),
    [
        ("json", {"items": []}),
        ("text", "stub"),
        ("yaml", "stub"),
        ("svg", "<svg/>"),
        ("html", "<html/>"),
        ("png", b"\x89PNG\r\n"),
        ("pdf", b"%PDF-"),
        ("terminal", "stub"),
    ],
)
def test_render_command_routes_through_agent_api(
    tmp_path: Path, format: str, mock_data: dict[str, Any] | str | bytes
) -> None:
    """CLI must call project.render_board for all formats — never core directly."""
    from dbt_charts.agent_api.boards import BoardRenderResult
    from dbt_charts.cli.commands.render import render_command
    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.render_format import RenderFormat

    # render_command resolves the board via resolve_board_or_error before handing
    # it to render_board — the mock needs a real Project + on-disk board for
    # that resolve step to succeed; only render_board itself is mocked.
    (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
    boards_dir = tmp_path / "charts"
    boards_dir.mkdir()
    (boards_dir / "test.yml").write_text("title: T\nrows: []\n")

    mock_result = BoardRenderResult(status="ok", data=mock_data)
    fake_project = MagicMock(name="ProjectSession")
    fake_project.project = FilesystemProject(tmp_path)
    fake_project.render_board.return_value = mock_result

    @contextmanager
    def fake_from_project(project, *, cache, **_kwargs):
        yield fake_project

    with patch(
        "dbt_charts.agent_api.ProjectSession.from_project",
        side_effect=fake_from_project,
    ):
        render_command(
            board_path=Path("charts/test.yml"),
            output="-",
            format=cast(RenderFormat, format),
            project_dir=tmp_path,
        )
        fake_project.render_board.assert_called_once()


@pytest.mark.parametrize(
    ("format", "mock_data"),
    [
        ("json", {"items": []}),
        ("svg", "<svg/>"),
        ("html", "<html/>"),
        ("png", b"\x89PNG\r\n"),
        ("terminal", "stub"),
    ],
)
def test_render_command_from_yaml_routes_through_agent_api(
    tmp_path: Path, format: str, mock_data: dict[str, Any] | str | bytes
) -> None:
    """Stdin path must also go through project.render_board, never core directly."""
    from dbt_charts.agent_api.boards import BoardRenderResult
    from dbt_charts.cli.commands.render import render_command_from_yaml
    from dbt_charts.core.render_format import RenderFormat

    (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
    mock_result = BoardRenderResult(status="ok", data=mock_data)
    fake_project = MagicMock(name="ProjectSession")
    fake_project.render_board.return_value = mock_result

    @contextmanager
    def fake_from_project(project, *, cache, **_kwargs):
        yield fake_project

    with patch(
        "dbt_charts.agent_api.ProjectSession.from_project",
        side_effect=fake_from_project,
    ):
        render_command_from_yaml(
            yaml_content="title: stub\nrows: []\n",
            output="-",
            format=cast(RenderFormat, format),
            project_dir=tmp_path,
        )
        fake_project.render_board.assert_called_once()
