"""CLI smoke tests for `dct skills`."""

from __future__ import annotations

import json
import re

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.main import app

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plain(text: str) -> str:
    """Strip ANSI escape codes — Rich emits them when CliRunner thinks the pipe
    can take color (CI environments commonly set FORCE_COLOR), and they split
    multi-character substrings across escape sequences."""
    return _ANSI_RE.sub("", text)


class TestSkillsList:
    def test_lists_known_skills(self) -> None:
        result = runner.invoke(app, ["skills"])
        assert result.exit_code == 0, result.output
        assert "chart" in result.output

    def test_json_output(self) -> None:
        result = runner.invoke(app, ["skills", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        names = [s["name"] for s in data["skills"]]
        assert "chart" in names

    def test_board_pack_scaffolding_is_not_cli_visible(self) -> None:
        result = runner.invoke(app, ["skills", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        names = {s["name"] for s in data["skills"]}
        assert "board-pack-scaffolding" not in names

    def test_json_includes_kind_and_has_examples(self) -> None:
        result = runner.invoke(app, ["skills", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        chart = next(s for s in data["skills"] if s["name"] == "chart")
        assert chart["kind"] == "workflow"
        assert chart["has_examples"] is False
        assert chart["body"]

    def test_lists_exactly_the_kept_skills(self) -> None:
        result = runner.invoke(app, ["skills", "--json"])
        assert result.exit_code == 0, result.output
        names = {s["name"] for s in json.loads(result.output)["skills"]}
        assert names == {
            "board-migrate",
            "chart",
            "cloud-setup",
            "custom-chart-build",
            "mcp-setup",
            "report-design",
            "styling",
        }

    @pytest.mark.parametrize("name", ["intro", "board-build", "kpi-row"])
    def test_retired_skill_exits_one(self, name: str) -> None:
        result = runner.invoke(app, ["skills", name])
        assert result.exit_code == 1
        assert name in result.output + result.stderr

    def test_listing_has_no_workflow_or_pattern_sections(self) -> None:
        result = runner.invoke(app, ["skills"])
        assert result.exit_code == 0, result.output
        assert "Workflows" not in result.output
        assert "Patterns" not in result.output

    def test_next_steps_footer_present(self) -> None:
        result = runner.invoke(app, ["skills"])
        assert result.exit_code == 0, result.output
        assert "dct skills" in result.output

    def test_start_here_line_precedes_the_list(self) -> None:
        result = runner.invoke(app, ["skills"])
        assert result.exit_code == 0, result.output
        output = _plain(result.output)
        start_here_pos = output.find("dct skills chart")
        first_skill_pos = output.find("board-migrate")
        assert start_here_pos != -1, output
        assert first_skill_pos != -1, output
        assert start_here_pos < first_skill_pos


class TestSkillsHasNoSearch:
    def test_search_option_is_rejected(self) -> None:
        result = runner.invoke(app, ["skills", "--search", "chart"])
        assert result.exit_code == 2
        assert "--search" in _plain(result.stderr)


class TestSkillsGet:
    def test_prints_skill_body(self) -> None:
        result = runner.invoke(app, ["skills", "chart"])
        assert result.exit_code == 0, result.output
        assert len(result.output.strip()) > 0

    def test_json_output_for_skill(self) -> None:
        result = runner.invoke(app, ["skills", "chart", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["name"] == "chart"
        assert data["description"]
        assert data["body"]
        assert data["kind"] == "workflow"

    def test_unknown_skill_exits_one(self) -> None:
        result = runner.invoke(app, ["skills", "no-such-skill-xyz"])
        assert result.exit_code == 1
        assert "no-such-skill-xyz" in result.output + result.stderr


class TestDftSkillsHelpLayout:
    def test_help_modes_on_separate_lines(self) -> None:
        result = runner.invoke(app, ["skills", "--help"])
        assert result.exit_code == 0
        text = _plain(result.output)
        lines = [line.strip() for line in text.splitlines()]
        for form in (
            "dct skills                  # List all skills (grouped: workflows + patterns)",
            "dct skills <name>           # Show the named skill body",
            "dct skills --json           # Stable JSON for any of the above",
        ):
            assert form in lines, (
                f"Skills mode {form!r} not on its own line. Full output:\n{text}"
            )
