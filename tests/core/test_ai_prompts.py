"""Unit tests for ``dbt_charts.ai.prompts``."""

from pathlib import Path

from dbt_charts.agent_api.skills import get_skill
from dbt_charts.ai.prompts import (
    PROJECT_INSTRUCTIONS_MAX_CHARS,
    build_context_section,
    build_dbt_charts_system_prompt,
    build_skills_index,
    load_project_instructions,
    load_prompt,
    load_shared_prompt,
    read_syntax_guide,
)
from dbt_charts.cli.filesystem_project import FilesystemProject


class TestLoadPrompt:
    """Tests for the load_prompt function."""

    def test_load_prompt_existing_file(self, tmp_path: Path) -> None:
        """Test loading an existing prompt file."""
        # Create a test prompt file
        prompt_content = "# Test Prompt\n\nThis is a test prompt."
        prompt_file = tmp_path / "test_prompt.md"
        prompt_file.write_text(prompt_content)

        # Load the prompt
        result = load_prompt("test_prompt", tmp_path)

        assert result == prompt_content

    def test_load_prompt_nonexistent_file(self, tmp_path: Path) -> None:
        """Test loading a prompt that doesn't exist returns empty string."""
        result = load_prompt("nonexistent_prompt", tmp_path)

        assert result == ""

    def test_load_prompt_with_special_characters(self, tmp_path: Path) -> None:
        """Test loading a prompt with special characters."""
        prompt_content = "# Prompt with émojis 🎉\n\nSpecial chars: © ® ™"
        prompt_file = tmp_path / "special.md"
        prompt_file.write_text(prompt_content, encoding="utf-8")

        result = load_prompt("special", tmp_path)

        assert result == prompt_content

    def test_load_prompt_multiline(self, tmp_path: Path) -> None:
        """Test loading a multi-line prompt."""
        prompt_content = """# Dashboard Generation

## Instructions

1. Generate valid YAML
2. Include all required fields
3. Test before returning

```yaml
title: Example
```
"""
        prompt_file = tmp_path / "multiline.md"
        prompt_file.write_text(prompt_content)

        result = load_prompt("multiline", tmp_path)

        assert result == prompt_content
        assert "# Dashboard Generation" in result
        assert "```yaml" in result


class TestBuildContextSection:
    """Tests for the build_context_section function."""

    def test_build_context_section_empty(self) -> None:
        """Test building context with no parameters returns empty string."""
        result = build_context_section()

        assert result == ""

    def test_build_context_section_database_only(self) -> None:
        """Test building context with only database context."""
        result = build_context_section(database_context="Tables: users, orders")

        assert "## ⚠️ CRITICAL: Database Context" in result
        assert "Tables: users, orders" in result
        assert "Current YAML" not in result
        assert "Chart Context" not in result

    def test_build_context_section_yaml_only(self) -> None:
        """Test building context with only YAML context."""
        result = build_context_section(yaml_context="title: My Dashboard")

        assert "## Current YAML Code" in result
        assert "```yaml" in result
        assert "title: My Dashboard" in result
        assert "Database Context" not in result

    def test_build_context_section_chart_only(self) -> None:
        """Test building context with only chart context."""
        result = build_context_section(chart_context="Bar chart showing revenue")

        assert "## Chart Context" in result
        assert "Bar chart showing revenue" in result
        assert "Database Context" not in result
        assert "Current YAML" not in result

    def test_build_context_section_all_contexts(self) -> None:
        """Test building context with all parameters."""
        result = build_context_section(
            database_context="Tables: users, orders",
            yaml_context="title: Dashboard",
            chart_context="Revenue chart",
        )

        # All sections should be present
        assert "## ⚠️ CRITICAL: Database Context" in result
        assert "Tables: users, orders" in result
        assert "## Current YAML Code" in result
        assert "title: Dashboard" in result
        assert "## Chart Context" in result
        assert "Revenue chart" in result

    def test_build_context_section_database_first(self) -> None:
        """Test that database context appears first in output."""
        result = build_context_section(
            database_context="DB info",
            yaml_context="YAML info",
            chart_context="Chart info",
        )

        # Database should come first
        db_pos = result.find("Database Context")
        yaml_pos = result.find("Current YAML")
        chart_pos = result.find("Chart Context")

        assert db_pos < yaml_pos < chart_pos

    def test_build_context_section_yaml_formatting(self) -> None:
        """Test that YAML context is properly formatted in code block."""
        yaml_content = "title: Test\nqueries:\n  test: { sql: SELECT 1 }"
        result = build_context_section(yaml_context=yaml_content)

        assert "```yaml" in result
        assert "```" in result
        assert yaml_content in result

    def test_build_context_section_preserves_newlines(self) -> None:
        """Test that context preserves newlines in input."""
        db_context = "Table: users\nColumns:\n  - id\n  - name"
        result = build_context_section(database_context=db_context)

        assert "Table: users" in result
        assert "Columns:" in result
        assert "- id" in result


class TestSharedPromptComposition:
    """Shared dbt charts prompt composition is the only generic AI instruction path."""

    def test_system_prompt_opens_with_the_american_english_rule(self) -> None:
        prompt = build_dbt_charts_system_prompt(
            "dashboard_design", database_context="Tables: orders"
        )

        assert prompt.startswith("Write in American English.")

    def test_build_dashboard_prompt_uses_shared_skills_and_context(self) -> None:
        prompt = build_dbt_charts_system_prompt(
            "dashboard_design",
            database_context="Tables: orders",
            yaml_context="title: Existing",
        )

        assert "# dbt Charts" in prompt
        assert "## SQL Generation Rules" in prompt
        assert "Tables: orders" in prompt
        assert "title: Existing" in prompt

    def test_build_exploration_prompt_composes_no_skill(self) -> None:
        prompt = build_dbt_charts_system_prompt(
            "database_exploration",
            database_context="Source: warehouse",
        )

        assert "Source: warehouse" in prompt
        # Pure schema exploration builds no artifact — no grammar page there.
        assert "# dbt Charts" not in prompt


class TestPromptGatedByAvailableTools:
    """build_dbt_charts_system_prompt drops prose for tools the surface lacks."""

    def test_playground_tool_set_omits_search_files(self) -> None:
        prompt = build_dbt_charts_system_prompt(
            "dashboard_design",
            available_tools={"render_board", "execute_sql"},
        )

        assert "search_files" not in prompt
        assert "render_board" in prompt

    def test_default_available_tools_none_keeps_search_files(self) -> None:
        """No available_tools declared (CLI/MCP/full chat) — nothing is gated."""
        prompt = build_dbt_charts_system_prompt("dashboard_design")

        assert "search_files" in prompt


class TestBuildSkillsIndex:
    """The index replaces inlined skill bodies with name + description."""

    def test_lists_every_tool_surface_skill_by_name(self) -> None:
        from dbt_charts.agent_api.skills import list_skills

        index = build_skills_index()

        names = [s.name for s in list_skills(surface="tool").skills]
        assert {"chart", "styling", "custom-chart-build"} <= set(names)
        for name in names:
            assert f"**{name}**" in index

    def test_includes_descriptions_not_bodies(self) -> None:
        from dbt_charts.agent_api.skills import skill_description

        index = build_skills_index()

        assert skill_description("chart") in index
        assert "# dbt Charts" not in index

    def test_points_at_get_skill_to_load_full_guide(self) -> None:
        index = build_skills_index()

        assert "get_skill" in index


class TestReadSyntaxGuide:
    def test_returns_the_packaged_syntax_guide(self) -> None:
        assert read_syntax_guide().startswith("# dbt charts YAML Syntax")


class TestSkillsIndexPromptReferencesRealTools:
    """Pins the contract progressive disclosure depends on: every tool name
    the index prose tells the model to call actually exists in
    AGENT_TOOLS. If one of these is ever dropped from the tool surface, the
    prompt would silently point at a nonexistent tool with no failing test —
    this is that test."""

    def test_skills_index_references_are_real_agent_tools(self) -> None:
        from dbt_charts.ai.tool_schemas import AGENT_TOOLS

        tool_names = {tool["name"] for tool in AGENT_TOOLS if "name" in tool}
        index = build_skills_index()
        for referenced in ("get_skill", "list_skills"):
            assert referenced in tool_names
            assert referenced in index


class TestBoardMigrateSkill:
    """The migration workflow keeps fake-data work visibly provisional."""

    def test_fake_data_banner_uses_native_warning_callout(self) -> None:
        prompt = load_shared_prompt("board-migrate", surface="tool")

        assert "type: callout" in prompt
        assert "tone: warning" in prompt
        assert prompt.count("- fake_data_warning") == 2

    def test_body_has_modes_rules_sources_and_bundle_layout(self) -> None:
        prompt = load_shared_prompt("board-migrate", surface="cli")

        for heading in (
            "## Modes",
            "## Rules",
            "## Bundle layout",
            "## Prove it",
            "### Looker",
            "### Sigma",
            "### Screenshots",
        ):
            assert heading in prompt, heading

    def test_description_does_not_claim_in_project_duplication(self) -> None:
        """This skill rebuilds a *foreign* dashboard from another tool — two
        phases, inline fake data, a warning banner. Its trigger list used to
        include 'copy this dashboard' and 'move my dashboard here', which are
        how users ask to duplicate a board that already exists in the project.
        Matching those routes an in-project copy into a fake-data rebuild.
        """
        description = get_skill("board-migrate").description.lower()

        assert "copy this dashboard" not in description
        assert "move my dashboard here" not in description
        # The screenshot/export replication triggers are its real job — keep them.
        assert "replicate" in description
        assert "migrate" in description
        # Every skill description carries a negative boundary.
        assert "do not use" in description


class TestLoadProjectInstructions:
    """A project's own AGENTS.md/CLAUDE.md, formatted as a guidance-not-authority
    block — the trust model this feature is built around (see
    dbt-charts/AGENTS.md task honor-project-agents-md-and-skills)."""

    def test_no_instructions_files_returns_empty_string(self, tmp_path: Path) -> None:
        project = FilesystemProject(tmp_path)

        assert load_project_instructions(project) == ""

    def test_reads_root_agents_md(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("Revenue means recognized ARR.")
        project = FilesystemProject(tmp_path)

        result = load_project_instructions(project)

        assert "Revenue means recognized ARR." in result
        assert "AGENTS.md" in result

    def test_reads_claude_md_when_no_agents_md(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("Always use the fiscal calendar.")
        project = FilesystemProject(tmp_path)

        result = load_project_instructions(project)

        assert "Always use the fiscal calendar." in result
        assert "CLAUDE.md" in result

    def test_prefers_agents_md_over_claude_md(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("From AGENTS.md.")
        (tmp_path / "CLAUDE.md").write_text("From CLAUDE.md.")
        project = FilesystemProject(tmp_path)

        result = load_project_instructions(project)

        assert "From AGENTS.md." in result
        assert "From CLAUDE.md." not in result

    def test_is_labeled_guidance_not_authority(self, tmp_path: Path) -> None:
        """The load-bearing trust framing: the block must say project
        instructions do not override safety/tool-use policy or grant new
        capabilities — never just an unlabeled content dump."""
        (tmp_path / "AGENTS.md").write_text("Prefer dark theme dashboards.")
        project = FilesystemProject(tmp_path)

        result = load_project_instructions(project)

        assert "not" in result.lower()
        assert "safety" in result.lower() or "policy" in result.lower()
        assert "capabilit" in result.lower()

    def test_oversized_agents_md_truncates_with_notice(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("x" * (PROJECT_INSTRUCTIONS_MAX_CHARS * 2))
        project = FilesystemProject(tmp_path)

        result = load_project_instructions(project)

        assert "truncated" in result.lower()
        # Capped, not silently dropped — some of the original content remains.
        assert "x" in result
        assert len(result) < PROJECT_INSTRUCTIONS_MAX_CHARS * 2

    def test_blank_agents_md_is_treated_as_absent(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("   \n\n  ")
        project = FilesystemProject(tmp_path)

        assert load_project_instructions(project) == ""

    def test_blank_agents_md_falls_back_to_claude_md(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("   \n\n  ")
        (tmp_path / "CLAUDE.md").write_text("Always use the fiscal calendar.")
        project = FilesystemProject(tmp_path)

        result = load_project_instructions(project)

        assert "Always use the fiscal calendar." in result
