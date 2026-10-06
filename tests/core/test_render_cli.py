"""Tests for dct render CLI command."""

import textwrap
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.main import app
from dbt_charts.core.diagnostics import ERR_INTERNAL, Diagnostic

from .._paths import DBT_CHARTS_DIR

runner = CliRunner()
_PLAYGROUND_DIR = DBT_CHARTS_DIR / "examples" / "playground"

MINIMAL_YAML = textwrap.dedent(
    """\
    charts:
      c:
        query:
          type: sql
          source: examples_db
          sql: SELECT * FROM ecommerce_orders
        type: table
    rows:
      - c
"""
)


class TestRenderFile:
    """Tests for rendering from a YAML file."""

    def test_render_file_terminal_format(self) -> None:
        """dct render <file> --format terminal should produce terminal output."""

        board = (
            _PLAYGROUND_DIR / "charts" / "general" / "simple-example.yml"
        ).resolve()
        result = runner.invoke(
            app,
            [
                "render",
                str(board),
                "--format",
                "terminal",
                "--project-dir",
                str(_PLAYGROUND_DIR),
            ],
        )
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert len(result.output.strip()) > 0

    def test_render_no_project_dir_and_no_marker_in_cwd_raises_clean_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """dct render <abs-board> without --project-dir no longer anchors on the
        board file's parent directory — project discovery is a cwd walk only.

        With no --project-dir and no project marker walking up from cwd, the
        command exits 1 with a clean message instead of a traceback, even
        though the board path itself points at a real file inside a project.
        """
        board = (
            _PLAYGROUND_DIR / "charts" / "general" / "simple-example.yml"
        ).resolve()
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app,
            ["render", str(board), "--format", "terminal"],
        )
        assert result.exit_code == 1
        combined = result.output + (result.stderr or "")
        assert "Traceback" not in combined
        assert "No dbt charts project found" in combined

    def test_render_file_not_found(self) -> None:
        """dct render <nonexistent> should fail with clear error."""
        result = runner.invoke(app, ["render", "nonexistent.yml"])
        assert result.exit_code != 0


class TestRenderStdin:
    """Tests for rendering YAML from stdin via `dct render -`."""

    def test_render_stdin_terminal_format(self) -> None:
        """dct render - --format terminal should read YAML from stdin."""
        result = runner.invoke(
            app,
            [
                "render",
                "-",
                "--format",
                "terminal",
                "--project-dir",
                str(_PLAYGROUND_DIR),
            ],
            input=MINIMAL_YAML,
        )
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert len(result.output.strip()) > 0

    def test_render_stdin_svg_to_stdout(self) -> None:
        """dct render - --output - should write SVG to stdout."""
        result = runner.invoke(
            app,
            ["render", "-", "--output", "-", "--project-dir", str(_PLAYGROUND_DIR)],
            input=MINIMAL_YAML,
        )
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "<svg" in result.output

    def test_render_stdin_empty_input(self) -> None:
        """dct render - with empty stdin should fail with clear error."""
        result = runner.invoke(
            app,
            ["render", "-", "--format", "terminal"],
            input="",
        )
        assert result.exit_code != 0

    def test_render_stdin_render_error_is_concise(self) -> None:
        """Render failures produce a clean structured error, not a Typer variable dump."""
        from dbt_charts.agent_api.boards import BoardRenderResult

        err = Diagnostic.from_code(ERR_INTERNAL, message="render stage failed")

        fake_project = MagicMock(name="ProjectSession")
        fake_project.render_board.return_value = BoardRenderResult(
            status="failed", validation_errors=[err]
        )

        @contextmanager
        def fake_from_project(project, *, cache, **_kwargs):  # type: ignore[no-untyped-def]
            yield fake_project

        with patch(
            "dbt_charts.agent_api.ProjectSession.from_project",
            side_effect=fake_from_project,
        ):
            result = runner.invoke(
                app,
                [
                    "render",
                    "-",
                    "--output",
                    "-",
                    "--project-dir",
                    str(_PLAYGROUND_DIR),
                ],
                input=MINIMAL_YAML,
            )
        assert result.exit_code == 1
        combined = f"{result.stdout}\n{result.stderr}"
        assert "render stage failed" in combined
        assert "locals" not in combined.lower()


class TestRenderChartFlag:
    """Tests for --chart flag (single-chart rendering)."""

    def _fake_project_session(self, data: str = "<svg/>") -> tuple[MagicMock, object]:
        from dbt_charts.agent_api.boards import BoardRenderResult

        fake_project = MagicMock(name="ProjectSession")
        fake_project.render_board.return_value = BoardRenderResult(
            status="ok", data=data
        )

        @contextmanager
        def fake_from_project(
            project: object, *, cache: object, **_kwargs: object
        ) -> Generator[MagicMock, None, None]:
            yield fake_project

        return fake_project, fake_from_project

    def test_chart_flag_passes_chart_to_render_board(self, tmp_path: Path) -> None:
        """--chart with a single board *file* passes chart= to render_board."""
        fake_project, fake_from_project = self._fake_project_session()
        (tmp_path / "dbt_charts.yml").write_text("")
        board = tmp_path / "board.yml"
        board.write_text(MINIMAL_YAML)

        with patch(
            "dbt_charts.agent_api.ProjectSession.from_project",
            side_effect=fake_from_project,
        ):
            result = runner.invoke(
                app,
                [
                    "render",
                    str(board),
                    "--output",
                    str(tmp_path / "out.svg"),
                    "--project-dir",
                    str(tmp_path),
                    "--chart",
                    "my_chart",
                ],
            )
        assert result.exit_code == 0, result.output
        _kwargs = fake_project.render_board.call_args.kwargs
        assert _kwargs.get("chart") == "my_chart"

    def test_chart_flag_multiple_boards_is_bad_parameter(self, tmp_path: Path) -> None:
        """--chart with multiple board arguments is a BadParameter (exit 2)."""
        board1 = tmp_path / "a.yml"
        board2 = tmp_path / "b.yml"
        board1.write_text(MINIMAL_YAML)
        board2.write_text(MINIMAL_YAML)
        result = runner.invoke(
            app,
            [
                "render",
                str(board1),
                str(board2),
                "--chart",
                "my_chart",
                "--project-dir",
                str(_PLAYGROUND_DIR),
            ],
        )
        assert result.exit_code == 2
        assert "--chart" in result.output

    def test_chart_flag_stdin_passes_chart_to_render_board(self) -> None:
        """--chart with the stdin arm passes chart= to render_board."""
        fake_project, fake_from_project = self._fake_project_session()

        with patch(
            "dbt_charts.agent_api.ProjectSession.from_project",
            side_effect=fake_from_project,
        ):
            result = runner.invoke(
                app,
                [
                    "render",
                    "-",
                    "--output",
                    "-",
                    "--project-dir",
                    str(_PLAYGROUND_DIR),
                    "--chart",
                    "rev",
                ],
                input=MINIMAL_YAML,
            )
        assert result.exit_code == 0, result.output
        _kwargs = fake_project.render_board.call_args.kwargs
        assert _kwargs.get("chart") == "rev"


class TestRenderChartErrors:
    """Tests for --allow-chart-errors behavior."""

    def _make_chart_error(self) -> Diagnostic:
        return Diagnostic.from_code(
            ERR_INTERNAL, message="column 'missing_col' not found"
        )

    def test_chart_errors_default_exits_1(self) -> None:
        """Default behavior: any per-chart error causes exit 1 (CI-safe)."""
        from dbt_charts.agent_api.boards import BoardRenderResult

        fake_project = MagicMock(name="ProjectSession")
        fake_project.render_board.return_value = BoardRenderResult(
            status="partial",
            data="<svg/>",
            chart_errors=[self._make_chart_error()],
        )

        @contextmanager
        def fake_from_project(project, *, cache, **_kwargs):  # type: ignore[no-untyped-def]
            yield fake_project

        with patch(
            "dbt_charts.agent_api.ProjectSession.from_project",
            side_effect=fake_from_project,
        ):
            result = runner.invoke(
                app,
                [
                    "render",
                    "-",
                    "--output",
                    "-",
                    "--project-dir",
                    str(_PLAYGROUND_DIR),
                ],
                input=MINIMAL_YAML,
            )
        assert result.exit_code == 1
        assert "missing_col" in result.output

    def test_chart_errors_allow_chart_errors_exits_0(self) -> None:
        """--allow-chart-errors: per-chart errors do NOT cause exit 1; partial render written."""
        from dbt_charts.agent_api.boards import BoardRenderResult

        fake_project = MagicMock(name="ProjectSession")
        fake_project.render_board.return_value = BoardRenderResult(
            status="partial",
            data="<svg/>",
            chart_errors=[self._make_chart_error()],
        )

        @contextmanager
        def fake_from_project(project, *, cache, **_kwargs):  # type: ignore[no-untyped-def]
            yield fake_project

        with patch(
            "dbt_charts.agent_api.ProjectSession.from_project",
            side_effect=fake_from_project,
        ):
            result = runner.invoke(
                app,
                [
                    "render",
                    "-",
                    "--output",
                    "-",
                    "--project-dir",
                    str(_PLAYGROUND_DIR),
                    "--allow-chart-errors",
                ],
                input=MINIMAL_YAML,
            )
        assert result.exit_code == 0
        assert "<svg" in result.output

    def test_no_chart_errors_default_exits_0(self) -> None:
        """Default: no chart errors → exit 0 (unchanged)."""
        from dbt_charts.agent_api.boards import BoardRenderResult

        fake_project = MagicMock(name="ProjectSession")
        fake_project.render_board.return_value = BoardRenderResult(
            status="ok",
            data="<svg/>",
            chart_errors=[],
        )

        @contextmanager
        def fake_from_project(project, *, cache, **_kwargs):  # type: ignore[no-untyped-def]
            yield fake_project

        with patch(
            "dbt_charts.agent_api.ProjectSession.from_project",
            side_effect=fake_from_project,
        ):
            result = runner.invoke(
                app,
                [
                    "render",
                    "-",
                    "--output",
                    "-",
                    "--project-dir",
                    str(_PLAYGROUND_DIR),
                ],
                input=MINIMAL_YAML,
            )
        assert result.exit_code == 0
