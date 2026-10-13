"""CLI smoke tests for `dct skills`."""

from __future__ import annotations

import json
import re

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
        assert "kpi-row" in result.output

    def test_json_output(self) -> None:
        result = runner.invoke(app, ["skills", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        names = [s["name"] for s in data["skills"]]
        assert "kpi-row" in names

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
        kpi = next(s for s in data["skills"] if s["name"] == "kpi-row")
        assert kpi["kind"] == "pattern"
        assert kpi["has_examples"] is True
        assert kpi["body"]

    def test_lists_workflows_and_patterns_sections(self) -> None:
        result = runner.invoke(app, ["skills"])
        assert result.exit_code == 0, result.output
        assert "Workflows" in result.output
        assert "Patterns" in result.output

    def test_next_steps_footer_present(self) -> None:
        result = runner.invoke(app, ["skills"])
        assert result.exit_code == 0, result.output
        assert "dct skills" in result.output

    def test_cross_link_to_docs_search(self) -> None:
        result = runner.invoke(app, ["skills"])
        assert result.exit_code == 0, result.output
        assert "dct docs" in result.output

    def test_start_here_line_precedes_the_list(self) -> None:
        result = runner.invoke(app, ["skills"])
        assert result.exit_code == 0, result.output
        output = _plain(result.output)
        start_here_pos = output.find("dct skills intro")
        workflows_pos = output.find("Workflows")
        assert start_here_pos != -1, output
        assert workflows_pos != -1, output
        assert start_here_pos < workflows_pos

    def test_scaffold_marker_shown_for_skills_with_examples(self) -> None:
        result = runner.invoke(app, ["skills"])
        assert result.exit_code == 0, result.output
        # kpi-row ships with an example YAML.
        assert "[scaffold]" in result.output


class TestSkillsHasNoSearch:
    def test_search_option_is_rejected(self) -> None:
        result = runner.invoke(app, ["skills", "--search", "kpi"])
        assert result.exit_code == 2
        assert "--search" in _plain(result.stderr)


class TestSkillsGet:
    def test_prints_skill_body(self) -> None:
        result = runner.invoke(app, ["skills", "kpi-row"])
        assert result.exit_code == 0, result.output
        assert len(result.output.strip()) > 0

    def test_json_output_for_skill(self) -> None:
        result = runner.invoke(app, ["skills", "kpi-row", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["name"] == "kpi-row"
        assert data["description"]
        assert data["body"]
        assert data["kind"] == "pattern"

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
