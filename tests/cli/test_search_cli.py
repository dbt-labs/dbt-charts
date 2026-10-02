"""Tests for the dct search CLI command."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_charts.agent_api.search import SearchResult
from dbt_charts.cli.main import app

from .._paths import DBT_CHARTS_DIR

runner = CliRunner()

_FIXTURE_DIR = DBT_CHARTS_DIR / "tests" / "fixtures"


class TestDftSearchHappyPath:
    """dct search against a project directory returns ranked results."""

    def test_search_returns_results_for_matching_query(self, tmp_path: Path) -> None:
        shutil.copytree(_FIXTURE_DIR / "multi-query-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")

        result = runner.invoke(
            app,
            ["search", "revenue", "--project-dir", str(tmp_path / "project")],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert "Multi Query Board" in result.output

    def test_search_zero_results_exits_0(self, tmp_path: Path) -> None:
        shutil.copytree(_FIXTURE_DIR / "multi-query-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")

        result = runner.invoke(
            app,
            [
                "search",
                "zzznomatchxxx",
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0, result.output


class TestDftSearchJsonOutput:
    """dct search --json produces a parseable SearchResult envelope."""

    def test_json_output_roundtrips_through_search_result(self, tmp_path: Path) -> None:
        shutil.copytree(_FIXTURE_DIR / "multi-query-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")

        result = runner.invoke(
            app,
            [
                "search",
                "revenue",
                "--json",
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0, result.output
        envelope = SearchResult.model_validate_json(result.output)
        assert envelope.success is True
        assert envelope.results, "revenue query should match at least one board"

    def test_json_zero_results_is_valid_envelope(self, tmp_path: Path) -> None:
        shutil.copytree(_FIXTURE_DIR / "multi-query-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")

        result = runner.invoke(
            app,
            [
                "search",
                "zzznomatchxxx",
                "--json",
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["results"] == []


class TestDftSearchLimitOption:
    """dct search --limit caps the number of results returned."""

    def test_limit_1_returns_at_most_one_result(self, tmp_path: Path) -> None:
        shutil.copytree(_FIXTURE_DIR / "multi-query-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")

        result = runner.invoke(
            app,
            [
                "search",
                "revenue",
                "--json",
                "--limit",
                "1",
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data["results"]) <= 1


class TestDftSearchMissingProject:
    """dct search against a nonexistent directory exits 2 (Typer exists=True check)."""

    def test_missing_project_exits_2(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "search",
                "revenue",
                "--json",
                "--project-dir",
                str(tmp_path / "nonexistent"),
            ],
        )
        # Typer's exists=True check makes nonexistent --project-dir exit 2
        assert result.exit_code == 2


class TestDftSearchHelperRouting:
    """_print_rich routes SearchResult errors through print_diagnostics."""

    def test_print_rich_routes_failure_through_helper(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from dbt_charts.agent_api.search import SearchResult
        from dbt_charts.cli.commands.search import _print_rich
        from dbt_charts.core.diagnostics import ERR_INTERNAL
        from dbt_charts.core.diagnostics.base import DbtChartsError

        err = DbtChartsError.from_code(
            ERR_INTERNAL, message="test error"
        ).to_diagnostic()
        result = SearchResult(success=False, errors=[err], results=[])
        _print_rich(result)
        captured = capsys.readouterr()
        assert "ERR-INTERNAL" in captured.err
        # print_diagnostics writes a "Docs:" line for each error;
        # raw typer.echo(str(error)) would not, proving the helper ran.
        assert "Docs:" in captured.err


class TestDftSearchHelpLayout:
    """Each `dct search ...` example in --help renders on its own line."""

    def test_help_examples_on_separate_lines(self) -> None:
        import re

        result = runner.invoke(app, ["search", "--help"])
        assert result.exit_code == 0
        output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        lines = [line.strip() for line in output.splitlines()]
        for form in (
            "dct search revenue",
            'dct search "monthly trends" --limit 5',
            "dct search orders --json",
        ):
            assert form in lines, (
                f"Search example {form!r} not on its own line. Full output:\n{output}"
            )
