"""Tests for the dct validate CLI command.

Validates that dct validate exists (with the same behavior as the former
dct check), and that dct check no longer exists.
"""

from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.main import app
from dbt_charts.core.diagnostics import Diagnostic

from .._paths import DBT_CHARTS_DIR

runner = CliRunner()

_VALID_BOARD = """
queries:
  revenue:
    sql: SELECT month, SUM(revenue) FROM orders GROUP BY 1
    source: analytics
charts:
  revenue_trend:
    query: revenue
    type: bar
    x: month
    y: revenue
rows:
  - revenue_trend
"""

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

_ORPHAN_BOARD = """
queries:
  revenue:
    sql: SELECT month, SUM(revenue) FROM orders GROUP BY 1
    source: analytics
charts:
  revenue_trend:
    query: revenue
    type: bar
    x: month
    y: revenue
  orphan:
    query: revenue
    type: line
    x: month
    y: revenue
rows:
  - revenue_trend
"""


class TestDftCheckCommandRemoved:
    """dct check must no longer exist as a registered command."""

    def test_check_is_not_a_registered_command(self) -> None:
        result = runner.invoke(app, ["check", "--help"])
        assert result.exit_code != 0, "dct check should not exist (use dct validate)"


class TestDftValidateJsonRoundTrip:
    """dct validate --json produces valid JSON matching ValidateResult schema."""

    def test_dct_validate_json_round_trip(self, tmp_path: Path) -> None:
        from dbt_charts.agent_api.validate import ValidateResult

        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        board = tmp_path / "board.yml"
        board.write_text(_VALID_BOARD)

        result = runner.invoke(
            app, ["validate", str(board), "--json", "--project-dir", str(tmp_path)]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        parsed = ValidateResult.model_validate(data)
        assert parsed.success is True
        assert parsed.errors == []
        assert isinstance(data["path"], str), (
            f"path field in --json output must be a JSON string, got {type(data['path'])}"
        )

    def test_dct_validate_directory_json_serializes_path(self, tmp_path: Path) -> None:
        """Multi-file --json must produce JSON-serializable output."""
        from dbt_charts.agent_api.validate import ValidateResult

        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        boards_dir = tmp_path / "charts"
        boards_dir.mkdir()
        (boards_dir / "one.yml").write_text(_VALID_BOARD)
        (boards_dir / "two.yml").write_text(_VALID_BOARD)

        result = runner.invoke(
            app, ["validate", str(boards_dir), "--json", "--project-dir", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) >= 2
        for entry in data:
            parsed = ValidateResult.model_validate(entry)
            assert parsed.success is True


class TestDftValidateExits1OnErrors:
    """dct validate exits 1 when the board has validation errors."""

    def test_dct_validate_exits_1_on_errors(self, tmp_path: Path) -> None:
        board = tmp_path / "board.yml"
        board.write_text(_BAD_BOARD)

        result = runner.invoke(
            app, ["validate", str(board), "--project-dir", str(tmp_path)]
        )
        assert result.exit_code == 1


class TestDftValidateStrictExits1OnWarnings:
    """dct validate --strict exits 1 when the board has warnings."""

    def test_dct_validate_strict_exits_1_on_warnings(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        board = tmp_path / "board.yml"
        board.write_text(_ORPHAN_BOARD)
        project_dir_args = ["--project-dir", str(tmp_path)]

        result_normal = runner.invoke(app, ["validate", str(board), *project_dir_args])
        assert result_normal.exit_code == 0

        result_strict = runner.invoke(
            app, ["validate", str(board), "--strict", *project_dir_args]
        )
        assert result_strict.exit_code == 1

    def test_strict_fails_a_board_migrated_in_memory(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        board = tmp_path / "board.yml"
        board.write_text(
            "title: t\n"
            "theme: solid\n"
            "queries:\n"
            "  q:\n"
            "    type: values\n"
            "    columns: [n]\n"
            "    values:\n"
            "      - [1]\n"
            "charts:\n"
            "  k:\n"
            "    type: kpi\n"
            "    query: q\n"
            "    value: n\n"
            "rows:\n"
            "  - k\n"
        )
        project_dir_args = ["--project-dir", str(tmp_path)]

        result_normal = runner.invoke(app, ["validate", str(board), *project_dir_args])
        assert result_normal.exit_code == 0, result_normal.stderr

        result_strict = runner.invoke(
            app, ["validate", str(board), "--strict", *project_dir_args]
        )
        assert result_strict.exit_code == 1
        assert "WARN-SCHEMA-MIGRATED" in result_strict.stderr


class TestDftValidatePrettyOutputHasNoTraceback:
    """dct validate pretty output shows message text, not a Python traceback."""

    def test_dct_validate_pretty_output_has_no_traceback(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        board = tmp_path / "board.yml"
        board.write_text(_BAD_BOARD)

        result = runner.invoke(
            app, ["validate", str(board), "--project-dir", str(tmp_path)]
        )
        combined = (result.output or "") + (result.stderr or "")
        assert "Traceback" not in combined
        assert "nonexistent_query" in combined


class TestDftValidateDirectoryDefaultBoards:
    """dct validate with no args defaults to charts/ under the project root."""

    def test_validate_no_args_checks_boards_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "good.yml").write_text(_VALID_BOARD)
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["validate"])
        assert result.exit_code == 0, result.output

    def test_validate_directory_arg_checks_all_yaml_files(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "good.yml").write_text(_VALID_BOARD)
        (tmp_path / "charts" / "also_good.yaml").write_text(_VALID_BOARD)

        result = runner.invoke(
            app,
            ["validate", str(tmp_path / "charts"), "--project-dir", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output

    def test_validate_directory_exits_1_when_any_file_has_errors(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "good.yml").write_text(_VALID_BOARD)
        (tmp_path / "charts" / "bad.yml").write_text(_BAD_BOARD)

        result = runner.invoke(
            app,
            ["validate", str(tmp_path / "charts"), "--project-dir", str(tmp_path)],
        )
        assert result.exit_code == 1

    def test_validate_directory_skips_underscore_files(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "_partial.yml").write_text(_BAD_BOARD)
        (tmp_path / "charts" / "good.yml").write_text(_VALID_BOARD)

        result = runner.invoke(
            app,
            ["validate", str(tmp_path / "charts"), "--project-dir", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output

    def test_validate_directory_skips_ejected_inspect_templates(
        self, tmp_path: Path
    ) -> None:
        """Directories with .inspect-template-manifest.json are template
        territory, not standalone boards — skip them in the default walk."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "good.yml").write_text(_VALID_BOARD)
        inspect_dir = tmp_path / "charts" / "inspect"
        inspect_dir.mkdir()
        # A file shaped like a real ejected template: contains Jinja-flow that
        # the YAML pre-parser cannot tolerate without the URL parameter context.
        (inspect_dir / "model.yml").write_text(
            "theme: {{ 'neon' if theme == 'neon' else 'stark' }}\n"
            'title: "Model: {{ model }}"\n'
        )
        (inspect_dir / ".inspect-template-manifest.json").write_text(
            '{"schema_version": 1, "templates": {"model": '
            '{"filename": "model.yml", "ejected_at": "2026-01-01T00:00:00+00:00",'
            ' "source_version": "0.0.0", "source_sha256": "deadbeef"}}}\n'
        )

        result = runner.invoke(
            app,
            ["validate", str(tmp_path / "charts"), "--project-dir", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "model.yml" not in result.output

    def test_validate_explicit_inspect_template_path_still_errors(
        self, tmp_path: Path
    ) -> None:
        """Explicit path argument bypasses the directory walk filter — if the
        user asks for an inspect template by name, deliver the (parse) error."""
        inspect_dir = tmp_path / "charts" / "inspect"
        inspect_dir.mkdir(parents=True)
        target = inspect_dir / "model.yml"
        target.write_text(
            "theme: {{ 'neon' if theme == 'neon' else 'stark' }}\n"
            'title: "Model: {{ model }}"\n'
        )
        (inspect_dir / ".inspect-template-manifest.json").write_text(
            '{"schema_version": 1, "templates": {"model": '
            '{"filename": "model.yml", "ejected_at": "2026-01-01T00:00:00+00:00",'
            ' "source_version": "0.0.0", "source_sha256": "deadbeef"}}}\n'
        )

        result = runner.invoke(
            app, ["validate", str(target), "--project-dir", str(tmp_path)]
        )
        # The user asked for this exact file — it parses as raw YAML containing
        # unresolved Jinja, so validate fails. That's the right outcome here.
        assert result.exit_code == 1

    def test_validate_empty_directory_exits_1(self, tmp_path: Path) -> None:
        (tmp_path / "empty").mkdir()

        result = runner.invoke(
            app,
            ["validate", str(tmp_path / "empty"), "--project-dir", str(tmp_path)],
        )
        assert result.exit_code == 1


class TestDftValidateDirectoryStrict:
    """dct validate <dir> --strict exits 1 when any file has warnings."""

    def test_validate_directory_strict_exits_1_on_warnings(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "warn.yml").write_text(_ORPHAN_BOARD)
        project_dir_args = ["--project-dir", str(tmp_path)]

        result_normal = runner.invoke(
            app, ["validate", str(tmp_path / "charts"), *project_dir_args]
        )
        assert result_normal.exit_code == 0

        result_strict = runner.invoke(
            app,
            ["validate", str(tmp_path / "charts"), "--strict", *project_dir_args],
        )
        assert result_strict.exit_code == 1


class TestDftValidateProjectDir:
    """dct validate --project-dir resolves the board path relative to the project root."""

    def test_validate_with_project_dir_flag(self, tmp_path: Path) -> None:
        fixture = DBT_CHARTS_DIR / "tests" / "fixtures" / "single-query-board"
        shutil.copytree(fixture, tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")

        result = runner.invoke(
            app,
            [
                "validate",
                "charts/board.yml",
                "--project-dir",
                str(tmp_path / "project"),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True

    def test_validate_directory_with_project_dir_flag(self, tmp_path: Path) -> None:

        # Use two boards so the output is a list (single-board directories return an object)
        (tmp_path / "project" / "charts").mkdir(parents=True)
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")
        fixture_board = DBT_CHARTS_DIR / "tests" / "fixtures" / "single-query-board"
        shutil.copy(
            fixture_board / "charts" / "board.yml",
            tmp_path / "project" / "charts" / "a.yml",
        )
        shutil.copy(
            fixture_board / "charts" / "board.yml",
            tmp_path / "project" / "charts" / "b.yml",
        )
        shutil.copy(fixture_board / "data.csv", tmp_path / "project" / "data.csv")

        result = runner.invoke(
            app,
            [
                "validate",
                "charts",
                "--project-dir",
                str(tmp_path / "project"),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) >= 2
        assert all(entry["success"] for entry in data)


class TestDftValidateJsonMalformedEnvelope:
    """dct validate --json on a malformed YAML file emits a parseable error envelope."""

    def test_malformed_yaml_json_envelope_is_structured_error(
        self, tmp_path: Path
    ) -> None:
        fixture = DBT_CHARTS_DIR / "tests" / "fixtures" / "malformed-board"
        shutil.copytree(fixture, tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")

        result = runner.invoke(
            app,
            [
                "validate",
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
        # Each error must be parseable as a Diagnostic
        for err in data["errors"]:
            parsed = Diagnostic.model_validate(err)
            assert parsed.code
            assert parsed.message


class TestDftValidateMultiPathArgv:
    """dct validate accepts multiple positional paths (shell-expanded globs)."""

    def test_multi_path_argv_json_returns_array(self, tmp_path: Path) -> None:
        from dbt_charts.agent_api.validate import ValidateResult

        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        a = tmp_path / "a.yml"
        a.write_text(_VALID_BOARD)
        b = tmp_path / "b.yml"
        b.write_text(_VALID_BOARD)

        result = runner.invoke(
            app,
            ["validate", str(a), str(b), "--json", "--project-dir", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2
        for entry in data:
            parsed = ValidateResult.model_validate(entry)
            assert parsed.success is True

    def test_multi_path_argv_exits_1_when_any_file_has_errors(
        self, tmp_path: Path
    ) -> None:
        good = tmp_path / "good.yml"
        good.write_text(_VALID_BOARD)
        bad = tmp_path / "bad.yml"
        bad.write_text(_BAD_BOARD)

        result = runner.invoke(
            app,
            ["validate", str(good), str(bad), "--project-dir", str(tmp_path)],
        )
        assert result.exit_code == 1

    def test_single_path_argv_json_still_returns_object(self, tmp_path: Path) -> None:
        """One argv path → JSON object, not a 1-element array."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        board = tmp_path / "board.yml"
        board.write_text(_VALID_BOARD)
        result = runner.invoke(
            app, ["validate", str(board), "--json", "--project-dir", str(tmp_path)]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)
        assert data["success"] is True

    def test_mixed_file_and_directory_argv(self, tmp_path: Path) -> None:
        """argv mixing a file and a directory concatenates results."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        loose = tmp_path / "loose.yml"
        loose.write_text(_VALID_BOARD)
        (tmp_path / "more").mkdir()
        (tmp_path / "more" / "one.yml").write_text(_VALID_BOARD)
        (tmp_path / "more" / "two.yml").write_text(_VALID_BOARD)

        result = runner.invoke(
            app,
            [
                "validate",
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


class TestDftValidateWarningsAtLineIsPosix:
    """The warning-display 'At:' fallback line must stay forward-slash for a
    nested board, on any host.

    Regression: cli/commands/validate.py used to re-wrap the already-str
    ValidateResult.path in Path(...) before handing it to print_diagnostics —
    undoing the str-identity fix at the last mile. print_diagnostics's `path`
    kwarg is f-string-interpolated directly (never routed through the
    POSIX-normalizing `_display_path`), so on a Windows host `str(WindowsPath
    (...))` rejoins with backslash. Monkeypatch the `Path` name bound in
    cli/commands/validate.py to `PureWindowsPath` — the only runtime
    construction site the fix removed — so the bug reproduces on any host,
    without needing a real Windows machine: pre-fix, the monkeypatched `Path`
    is still called and backslash leaks; post-fix, nothing in this module
    calls `Path(...)` anymore, so the patch is inert and the output stays
    POSIX.
    """

    def test_nested_warning_path_has_no_backslash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import dbt_charts.cli.commands.validate as validate_cmd

        monkeypatch.setattr(validate_cmd, "Path", PureWindowsPath)

        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "warn.yml").write_text(_ORPHAN_BOARD)

        result = runner.invoke(
            app,
            ["validate", "charts/warn.yml", "--project-dir", str(tmp_path)],
        )

        assert result.exit_code == 0, result.output
        assert "\\" not in result.output, f"backslash leaked: {result.output!r}"


class TestDftValidateStrictHelpText:
    """dct validate --help must explain the relationship between errors and --strict."""

    def test_strict_help_mentions_errors_and_warnings(self) -> None:
        result = runner.invoke(app, ["validate", "--help"])
        assert result.exit_code == 0
        combined = result.output + result.stderr
        assert "--strict" in combined
        assert "warnings" in combined.lower()
        assert "errors" in combined.lower()


def test_cli_validate_command_calls_project_session_validate(tmp_path: Path) -> None:
    """dct validate constructs ProjectSession.from_project and routes through
    project_session.validate_paths — never through agent_api.validate.validate_paths
    directly."""
    (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
    board = tmp_path / "board.yml"
    board.write_text(
        "title: T\ncharts:\n  c: {type: text, content: hi}\nrows:\n  - c\n"
    )

    fake_project = MagicMock(name="ProjectSession")
    fake_project.validate_paths.return_value = [
        MagicMock(path=board, errors=[], warnings=[])
    ]

    @contextmanager
    def fake_from_project(project, *, cache=None, **_kwargs):  # type: ignore[no-untyped-def]
        yield fake_project

    with patch(
        "dbt_charts.agent_api.ProjectSession.from_project",
        side_effect=fake_from_project,
    ) as from_project_mock:
        result = runner.invoke(
            app, ["validate", str(board), "--project-dir", str(tmp_path)]
        )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert from_project_mock.call_count == 1
    fake_project.validate_paths.assert_called_once()


def test_a_model_column_rename_fails_validate_before_dbt_run(tmp_path: Path) -> None:
    """End-to-end drift case: models/orders.sql renames customer_id → user_id,
    `dbt parse` refreshed the manifest, nothing was rebuilt — the board
    referencing customer_id must fail plain `dct validate`, no warehouse."""
    (tmp_path / "dbt_charts.yml").write_text("name: p\n")
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "manifest.json").write_text(
        json.dumps(
            {
                "metadata": {"adapter_type": "postgres"},
                "nodes": {
                    "model.p.orders": {
                        "resource_type": "model",
                        "name": "orders",
                        "schema": "main",
                        "raw_code": "SELECT id AS user_id, amount FROM raw",
                    }
                },
            }
        )
    )
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "board.yaml").write_text(
        "title: B\nsource: s\nqueries:\n"
        "  o: SELECT customer_id FROM {{ ref('orders') }}\n"
        "charts:\n  c:\n    query: o\n    type: table\nrows:\n  - c\n"
    )

    runner = CliRunner()
    result = runner.invoke(app, ["validate", "charts", "--project-dir", str(tmp_path)])

    assert result.exit_code == 1, result.output
    assert "ERR-DBT-MODEL-COLUMN-MISSING" in result.output
    assert "customer_id" in result.output
