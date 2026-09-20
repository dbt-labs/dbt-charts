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
    multi-character substrings like ``--search`` across escape sequences."""
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

    def test_scaffold_marker_shown_for_skills_with_examples(self) -> None:
        result = runner.invoke(app, ["skills"])
        assert result.exit_code == 0, result.output
        # kpi-row ships with an example YAML.
        assert "[scaffold]" in result.output


class TestSkillsSearch:
    def test_search_returns_kpi_row(self) -> None:
        result = runner.invoke(app, ["skills", "--search", "kpi"])
        assert result.exit_code == 0, result.output
        assert "kpi-row" in result.output

    def test_search_and_name_mutually_exclusive_exit_one(self) -> None:
        result = runner.invoke(app, ["skills", "kpi-row", "--search", "kpi"])
        assert result.exit_code == 1
        assert "--search" in _plain(result.output + result.stderr)

    def test_search_no_matches_message(self) -> None:
        result = runner.invoke(app, ["skills", "--search", "zzznevermatch"])
        assert result.exit_code == 0, result.output
        assert "No skills matched" in result.output or "0 matches" in result.output

    def test_search_json_output(self) -> None:
        result = runner.invoke(app, ["skills", "--search", "kpi", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert "hits" in data
        names = [h["name"] for h in data["hits"]]
        assert "kpi-row" in names

    def test_search_does_not_return_board_pack_scaffolding(self) -> None:
        result = runner.invoke(app, ["skills", "--search", "board pack", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        names = {h["name"] for h in data["hits"]}
        assert "board-pack-scaffolding" not in names


class TestSkillsLimitBounds:
    def test_in_range_limit_accepted(self) -> None:
        result = runner.invoke(
            app, ["skills", "--search", "kpi", "--limit", "5", "--json"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data["hits"]) <= 5

    def test_zero_limit_exit_two(self) -> None:
        result = runner.invoke(app, ["skills", "--search", "kpi", "--limit", "0"])
        assert result.exit_code == 2
        assert "--limit" in _plain(result.stderr)

    def test_oversize_limit_exit_two(self) -> None:
        result = runner.invoke(app, ["skills", "--search", "kpi", "--limit", "26"])
        assert result.exit_code == 2
        assert "--limit" in _plain(result.stderr)

    def test_search_limit_caps_hits(self) -> None:
        result = runner.invoke(
            app, ["skills", "--search", "kpi", "--limit", "1", "--json"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data["hits"]) <= 1


class TestSkillsHelp:
    def test_help_lists_search_option(self) -> None:
        result = runner.invoke(app, ["skills", "--help"])
        assert result.exit_code == 0, result.output
        output = _plain(result.output)
        assert "--search" in output
        assert "-s" in output

    def test_root_help_advertises_search_for_skills(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0, result.output
        output = _plain(result.output).lower()
        # Root command table should advertise that `skills` supports search.
        assert "skills" in output
        assert "search" in output


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
            'dct skills -s "<query>"     # Search names, descriptions, and bodies',
            "dct skills --json           # Stable JSON for any of the above",
        ):
            assert form in lines, (
                f"Skills mode {form!r} not on its own line. Full output:\n{text}"
            )
