"""Tests for surface-aware macro expansion in skill bodies."""

from __future__ import annotations

import re
from importlib.resources import files

import pytest

from dbt_charts.agent_api.skill_render import (
    MissingSurfaceAlias,
    render_skill_body,
)


class TestSurfaceExpansion:
    def test_expands_s_macro_on_tool_surface(self) -> None:
        out = render_skill_body("Run {{ s_execute_sql }} now.", surface="tool")
        assert out == "Run execute_sql now."

    def test_expands_s_macro_on_cli(self) -> None:
        out = render_skill_body("Run {{ s_execute_sql }} now.", surface="cli")
        assert out == "Run dct query SOURCE SQL now."

    def test_expands_multi_line_block_macro_tool_surface(self) -> None:
        body = "Try this:\n{{ s_render_png_example }}\nDone."
        out = render_skill_body(body, surface="tool")
        assert 'render_board(path="' in out
        assert "dct render" not in out

    def test_expands_multi_line_block_macro_cli(self) -> None:
        body = "Try this:\n{{ s_render_png_example }}\nDone."
        out = render_skill_body(body, surface="cli")
        assert "dct render charts/" in out
        assert "render_board(" not in out


class TestSurfaceSelection:
    """Single {{ s_key }} picks the right side per surface."""

    @pytest.mark.parametrize(
        ("body", "surface", "expected"),
        [
            ("{{ s_execute_sql }}", "tool", "execute_sql"),
            ("{{ s_execute_sql }}", "cli", "dct query SOURCE SQL"),
        ],
    )
    def test_s_macro_picks_correct_side(
        self, body: str, surface: str, expected: str
    ) -> None:
        out = render_skill_body(body, surface=surface)  # type: ignore[arg-type]
        assert out == expected


class TestBareNameAliasFamily:
    """`s_skill_name_*` renders a bare skill name on `tool` and `cli`, the
    dct-prefixed install name only on `install`: for prose that names a
    skill, not one that tells the reader to run it (that's the existing
    `s_skill_*` invocation family). `cli` stays bare because `dct skills`
    resolves names against the bare registry, not the file-install directory."""

    def test_bare_name_renders_unprefixed_on_tool(self) -> None:
        out = render_skill_body("{{ s_skill_name_chart }}", surface="tool")
        assert out == "chart"

    def test_bare_name_renders_unprefixed_on_cli(self) -> None:
        out = render_skill_body("{{ s_skill_name_chart }}", surface="cli")
        assert out == "chart"

    def test_bare_name_renders_prefixed_on_install(self) -> None:
        out = render_skill_body("{{ s_skill_name_chart }}", surface="install")
        assert out == "dct-chart"

    def test_command_macro_identical_on_cli_and_install(self) -> None:
        """Non-name macros (commands, prose) don't vary between `cli` and
        `install`: only the bare-name family does."""
        cli_out = render_skill_body("{{ s_execute_sql }}", surface="cli")
        install_out = render_skill_body("{{ s_execute_sql }}", surface="install")
        assert cli_out == install_out == "dct query SOURCE SQL"


class TestUnknownKey:
    @pytest.mark.parametrize("surface", ["tool", "cli"])
    def test_unknown_key_raises(self, surface: str) -> None:
        with pytest.raises(MissingSurfaceAlias, match="bogus_key_xyz"):
            render_skill_body("{{ s_bogus_key_xyz }}", surface=surface)  # type: ignore[arg-type]

    @pytest.mark.parametrize("surface", ["tool", "cli"])
    def test_old_dashboard_named_key_no_longer_resolves(self, surface: str) -> None:
        """Pre-rename key. No back-compat alias — must raise, not silently resolve."""
        with pytest.raises(MissingSurfaceAlias, match="validate_dashboard"):
            render_skill_body("{{ s_validate_dashboard }}", surface=surface)  # type: ignore[arg-type]


class TestToolAvailabilityGate:
    """{{#if_tool NAME}}...{{/if_tool}} blocks gate on available_tools."""

    def test_keeps_block_when_tool_available(self) -> None:
        body = (
            "Before.\n{{#if_tool render_board}}Call render_board.{{/if_tool}}\nAfter."
        )
        out = render_skill_body(body, surface="tool", available_tools={"render_board"})
        assert "Call render_board." in out
        assert "{{#if_tool" not in out
        assert "{{/if_tool}}" not in out

    def test_drops_block_when_tool_unavailable(self) -> None:
        body = (
            "Before.\n{{#if_tool search_files}}Call search_files.{{/if_tool}}\nAfter."
        )
        out = render_skill_body(body, surface="tool", available_tools={"render_board"})
        assert "search_files" not in out
        assert "Before." in out
        assert "After." in out

    def test_keeps_all_blocks_when_available_tools_is_none(self) -> None:
        """available_tools=None (the default) preserves CLI/MCP behavior — nothing gated."""
        body = "{{#if_tool search_files}}Call search_files.{{/if_tool}}"
        out = render_skill_body(body, surface="tool")
        assert "Call search_files." in out


class TestPassthrough:
    def test_body_without_macros_passes_through(self) -> None:
        body = "Just a normal SKILL.md\nwith **markdown** and `code`."
        for surface in ("tool", "cli"):
            assert render_skill_body(body, surface=surface) == body

    def test_plain_variable_no_s_prefix_passes_through(self) -> None:
        """{{ variable }} without s_ prefix is a board-YAML template token — must be untouched."""
        body = "SELECT * FROM {{ region }}"
        for surface in ("tool", "cli"):
            assert render_skill_body(body, surface=surface) == body

    def test_board_yaml_variable_in_fenced_block_passes_through(self) -> None:
        """board-YAML {{ variable }} tokens inside fenced SQL blocks must not be consumed."""
        body = "```sql\nSELECT * FROM orders WHERE region = '{{ region }}'\n```"
        for surface in ("tool", "cli"):
            assert render_skill_body(body, surface=surface) == body


# Old skill-ID prefixes the board rename retires in one cut. No aliases
# survive. Old wire tool-name coverage lives in test_skills.py's
# LEGACY_TOOL_NAMES (a plain substring guard — no quote-anchoring to slip
# past on a backticked reference).
_OLD_SKILL_ID_RE = re.compile(
    r"\bdashboard-(build|design|pack-scaffolding|replicate|review|structural-review|visual-review)\b"
)


class TestNoStaleDashboardVocabularyInSkills:
    """The board rename breaks skill rendering silently if split. Guard every SKILL.md."""

    def test_no_skill_references_old_dashboard_skill_ids(self) -> None:
        skills_root = files("dbt_charts.ai.skills")
        offenders: list[str] = []
        for entry in skills_root.iterdir():
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                continue
            text = skill_md.read_text(encoding="utf-8")
            if _OLD_SKILL_ID_RE.search(text):
                offenders.append(str(skill_md))
        assert offenders == []


class TestNoBareInstallSetNameInSkillBodies:
    """A sibling reference must be a `{{ s_skill_name_* }}` macro, not a literal
    install-set skill name — the macro is what makes the CLI-installed body read
    `dct-custom-chart-build` while the tool-call body reads `custom-chart-build`. `dct://guide/`
    resource URIs are exempt: registry addresses, not filesystem paths."""

    def test_no_bare_install_set_name_outside_guide_uri(self) -> None:
        from dbt_charts.agent_api.skill_install import skills_for_file_install

        # `chart` and `styling` are ordinary words in every skill's prose, so
        # they only count in a reference position: a lookup call, the CLI
        # command, an install directory, or "the <name> skill".
        ordinary = {"chart", "styling"}
        names = sorted(
            (s.name for s in skills_for_file_install()),
            key=len,
            reverse=True,
        )
        rare = [n for n in names if n not in ordinary]
        reference = "|".join(
            rf"get_skill\(['\"]{n}['\"]\)|dct skills {n}\b|dct-{n}\b|the {n} skill"
            for n in sorted(ordinary)
        )
        name_re = re.compile(
            r"(?<![\w-])(" + "|".join(re.escape(n) for n in rare) + r")(?![\w-])"
            "|" + reference
        )
        skills_root = files("dbt_charts.ai.skills")
        offenders: list[str] = []
        for entry in skills_root.iterdir():
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                continue
            # Frontmatter `description:` is a separate rendering pass — checked
            # by test_skills.py's description-rendering coverage, not here.
            body = skill_md.read_text(encoding="utf-8").split("---", 2)[2]
            for line in body.splitlines():
                if "dct://guide/" in line:
                    continue
                if name_re.search(line):
                    offenders.append(f"{skill_md}: {line.strip()}")
        assert offenders == []


class TestIfSurface:
    BODY = (
        "a{{#if_surface cli}}CLI{{/if_surface}}b"
        "{{#if_surface tool}}TOOL{{/if_surface}}c"
    )

    def test_tool_keeps_only_tool_blocks(self) -> None:
        assert render_skill_body(self.BODY, surface="tool") == "abTOOLc"

    def test_cli_keeps_cli_blocks(self) -> None:
        assert render_skill_body(self.BODY, surface="cli") == "aCLIbc"

    def test_install_keeps_cli_blocks(self) -> None:
        assert render_skill_body(self.BODY, surface="install") == "aCLIbc"
