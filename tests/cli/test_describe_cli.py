"""Tests for the dct describe CLI command."""

from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from dbt_charts.cli.main import app
from dbt_charts.core.diagnostics import Diagnostic

from .._paths import DBT_CHARTS_DIR

runner = CliRunner()

_BOARD_YAML = """
title: Sales Dashboard
queries:
  revenue:
    sql: SELECT month, SUM(amount) AS total FROM orders GROUP BY 1
    source: analytics
charts:
  trend:
    query: revenue
    type: line
    x: month
    y: total
rows:
  - trend
"""


class TestDftDescribeIsRegistered:
    """dct describe must exist; dct explain must not."""

    def test_describe_is_registered(self) -> None:
        import click
        from typer.main import get_command

        click_app = get_command(app)
        assert isinstance(click_app, click.Group)
        commands = click_app.commands
        assert "describe" in commands, "dct describe must be registered"

    def test_explain_is_absent(self) -> None:
        import click
        from typer.main import get_command

        click_app = get_command(app)
        assert isinstance(click_app, click.Group)
        commands = click_app.commands
        assert "explain" not in commands, "dct explain must be removed"


class TestDftDescribeJsonOutput:
    """dct describe <board> --json returns a structured result."""

    def test_json_output_for_yaml_path(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        board = tmp_path / "sales.yml"
        board.write_text(_BOARD_YAML)

        result = runner.invoke(
            app, ["describe", str(board), "--json", "--project-dir", str(tmp_path)]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert "queries" in data
        assert "charts" in data

    def test_json_has_structured_errors_on_failure(self, tmp_path: Path) -> None:
        """On compile failure, --json must return errors as Diagnostic dicts."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        bad = tmp_path / "bad.yml"
        bad.write_text("title: broken\nqueries:\n  q: [invalid")

        result = runner.invoke(
            app, ["describe", str(bad), "--json", "--project-dir", str(tmp_path)]
        )
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["success"] is False
        assert len(data["errors"]) > 0
        # Each error must be a dict (serialized Diagnostic), not a plain string
        for err in data["errors"]:
            assert isinstance(err, dict), f"Expected dict, got {type(err)}: {err}"


class TestDftDescribeRichOutput:
    """dct describe <board> produces Rich human-readable output."""

    def test_rich_output_shows_title(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        board = tmp_path / "sales.yml"
        board.write_text(_BOARD_YAML)

        result = runner.invoke(
            app, ["describe", str(board), "--project-dir", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "Sales Dashboard" in result.output

    def test_missing_file_exits_1(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        missing = tmp_path / "missing.yml"
        result = runner.invoke(
            app, ["describe", str(missing), "--project-dir", str(tmp_path)]
        )
        assert result.exit_code == 1
        combined = result.output + (result.stderr or "")
        assert "missing.yml" in combined
        assert "Error" in combined or "not found" in combined.lower()


class TestDftDescribeMetavar:
    """dct describe uses [PATH]... metavar, not ARG."""

    def test_help_shows_path_metavar(self) -> None:
        result = runner.invoke(app, ["describe", "--help"])
        assert result.exit_code == 0
        # Metavar must be PATH..., not ARG
        assert "PATH" in result.output
        assert " ARG" not in result.output


class TestDftDescribeProjectDir:
    """dct describe --project-dir resolves the board path relative to the project root."""

    def test_describe_with_project_dir_flag(self, tmp_path: Path) -> None:
        fixture = DBT_CHARTS_DIR / "tests" / "fixtures" / "single-query-board"
        shutil.copytree(fixture, tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")

        result = runner.invoke(
            app,
            [
                "describe",
                "charts/board.yml",
                "--project-dir",
                str(tmp_path / "project"),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True


class TestDftDescribeMissingBoard:
    """dct describe on a nonexistent file exits 1 with a structured error."""

    def test_missing_board_json_envelope(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        result = runner.invoke(
            app,
            [
                "describe",
                str(tmp_path / "nonexistent.yml"),
                "--json",
                "--project-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False
        assert len(data["errors"]) > 0
        for err in data["errors"]:
            parsed = Diagnostic.model_validate(err)
            assert parsed.code
            assert parsed.message


_BAD_BOARD = """
queries:
  revenue:
    sql: SELECT month, SUM(revenue) FROM orders GROUP BY 1
    source: analytics
charts:
  revenue_trend:
    query: nonexistent_query
    type: bar
    x: month
    y: revenue
rows:
  - revenue_trend
"""


class TestDftDescribeMultiPathArgv:
    """dct describe accepts multiple positional paths (shell-expanded globs)."""

    def test_multi_path_argv_json_returns_array(self, tmp_path: Path) -> None:
        from dbt_charts.agent_api.describe import DescribeBoardResult

        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        a = tmp_path / "a.yml"
        a.write_text(_BOARD_YAML)
        b = tmp_path / "b.yml"
        b.write_text(_BOARD_YAML)

        result = runner.invoke(
            app,
            ["describe", str(a), str(b), "--json", "--project-dir", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2
        for entry in data:
            parsed = DescribeBoardResult.model_validate(entry)
            assert parsed.success is True

    def test_single_path_argv_json_still_returns_object(self, tmp_path: Path) -> None:
        """One argv path → JSON object, not a 1-element array."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        board = tmp_path / "board.yml"
        board.write_text(_BOARD_YAML)
        result = runner.invoke(
            app, ["describe", str(board), "--json", "--project-dir", str(tmp_path)]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)
        assert data["success"] is True

    def test_multi_path_argv_exits_1_when_any_file_has_errors(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        good = tmp_path / "good.yml"
        good.write_text(_BOARD_YAML)
        bad = tmp_path / "bad.yml"
        bad.write_text(_BAD_BOARD)

        result = runner.invoke(
            app,
            ["describe", str(bad), str(good), "--project-dir", str(tmp_path)],
        )
        assert result.exit_code == 1
        # bad is first — if _emit short-circuits on error, good never renders.
        # "Sales Dashboard" appearing proves _emit continues past the failure.
        assert "Sales Dashboard" in result.output

    def test_multi_path_json_exits_1_and_includes_all_entries(
        self, tmp_path: Path
    ) -> None:
        """--json mode emits all results (2 entries) and exits 1 when any fail."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        good = tmp_path / "good.yml"
        good.write_text(_BOARD_YAML)
        bad = tmp_path / "bad.yml"
        bad.write_text(_BAD_BOARD)

        result = runner.invoke(
            app,
            [
                "describe",
                str(good),
                str(bad),
                "--json",
                "--project-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2
        successes = [e["success"] for e in data]
        assert True in successes
        assert False in successes

    def test_mixed_file_and_directory_argv(self, tmp_path: Path) -> None:
        """argv mixing a file and a directory concatenates results."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        loose = tmp_path / "loose.yml"
        loose.write_text(_BOARD_YAML)
        (tmp_path / "more").mkdir()
        (tmp_path / "more" / "one.yml").write_text(_BOARD_YAML)
        (tmp_path / "more" / "two.yml").write_text(_BOARD_YAML)

        result = runner.invoke(
            app,
            [
                "describe",
                str(loose),
                str(tmp_path / "more"),
                "--json",
                "--project-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 3

    def test_human_output_has_per_board_section_headers(self, tmp_path: Path) -> None:
        """Non-JSON multi-path stdout uses console.rule() between boards."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        a = tmp_path / "alpha.yml"
        a.write_text(_BOARD_YAML)
        b = tmp_path / "beta.yml"
        b.write_text(_BOARD_YAML)

        result = runner.invoke(
            app, ["describe", str(a), str(b), "--project-dir", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        # Each board gets a console.rule() line starting with ─.
        # Count those lines to confirm two distinct section headers were emitted
        # (a single rule would produce only one such line).
        rule_lines = [
            line for line in result.output.splitlines() if line.startswith("─")
        ]
        # Two boards → two distinct console.rule() lines. One rule would give len == 1.
        assert len(rule_lines) == 2


class TestDftDescribeDiagnosticErrors:
    """dct describe rich-mode (no --json) error output must surface the ERR-* code."""

    def test_rich_output_shows_structured_error_code(self, tmp_path: Path) -> None:
        """dct describe <bad-board> (no --json): exits 1, stderr has ERR- code."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        bad = tmp_path / "bad.yml"
        bad.write_text("title: broken\nqueries:\n  q: [invalid")

        result = runner.invoke(
            app, ["describe", str(bad), "--project-dir", str(tmp_path)]
        )
        assert result.exit_code == 1
        combined = (result.stderr or "") + result.output
        assert "ERR-" in combined


class TestDftDescribeJsonMalformed:
    """dct describe --json on malformed YAML emits a parseable error envelope."""

    def test_malformed_yaml_json_envelope_is_structured_error(
        self, tmp_path: Path
    ) -> None:
        fixture = DBT_CHARTS_DIR / "tests" / "fixtures" / "malformed-board"
        shutil.copytree(fixture, tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")

        result = runner.invoke(
            app,
            [
                "describe",
                "charts/board.yml",
                "--project-dir",
                str(tmp_path / "project"),
                "--json",
            ],
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False
        assert len(data["errors"]) > 0
        for err in data["errors"]:
            parsed = Diagnostic.model_validate(err)
            assert parsed.code
            assert parsed.message


def test_cli_describe_command_uses_project_session_with_block(tmp_path: Path) -> None:
    """dct describe constructs ProjectSession.from_project and routes through
    project_session.describe_paths — never through agent_api.describe directly."""
    (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
    board = tmp_path / "board.yml"
    board.write_text(
        "title: T\ncharts:\n  c: {type: text, content: hi}\nrows:\n  - c\n"
    )

    fake_project = MagicMock(name="ProjectSession")
    fake_project.describe_paths.return_value = [
        MagicMock(
            path=board,
            errors=[],
            success=True,
            title="T",
            queries=[],
            charts=[],
            variables=[],
            layout=None,
            notes=None,
        )
    ]

    @contextmanager
    def fake_from_project(project, *, cache=None, **_kwargs):  # type: ignore[no-untyped-def]
        yield fake_project

    with patch(
        "dbt_charts.agent_api.ProjectSession.from_project",
        side_effect=fake_from_project,
    ) as from_project_mock:
        result = runner.invoke(
            app, ["describe", str(board), "--project-dir", str(tmp_path)]
        )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert from_project_mock.call_count == 1, (
        "describe_command must construct exactly one ProjectSession.from_project(...)"
    )
    fake_project.describe_paths.assert_called_once()
