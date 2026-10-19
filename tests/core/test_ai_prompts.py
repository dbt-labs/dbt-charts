"""Unit tests for ``dbt_charts.ai.prompts``."""

from pathlib import Path

import pytest

from dbt_charts.agent_api.skills import get_skill
from dbt_charts.ai.prompts import (
    PROJECT_INSTRUCTIONS_MAX_CHARS,
    build_context_section,
    build_dbt_charts_system_prompt,
    build_docs_pointer,
    build_skills_index,
    load_project_instructions,
    load_prompt,
    load_shared_prompt,
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

    def test_data_exploration_skill_renders_tool_surface(self) -> None:
        prompt = load_shared_prompt("data-exploration", surface="tool")

        assert "Data Exploration" in prompt
        assert "INFORMATION_SCHEMA" in prompt
        assert "execute_sql" in prompt
        assert "{{ s_" not in prompt

    def test_data_exploration_skill_preserves_board_template_tokens(self) -> None:
        prompt = load_shared_prompt("data-exploration", surface="tool")

        assert "{{ variable_name }}" in prompt

    def test_build_dashboard_prompt_uses_shared_skills_and_context(self) -> None:
        prompt = build_dbt_charts_system_prompt(
            "dashboard_design",
            database_context="Tables: orders",
            yaml_context="title: Existing",
        )

        assert "# Dashboard Design" in prompt
        assert "# Building dbt charts Boards" in prompt
        assert "## SQL Generation Rules" in prompt
        assert "Tables: orders" in prompt
        assert "title: Existing" in prompt
        # The analyst-runbook fronts the authoring prompt types (Cloud/Playground).
        assert "Analyst Runbook" in prompt
        assert prompt.index("Analyst Runbook") < prompt.index(
            "# Building dbt charts Boards"
        )

    def test_build_exploration_prompt_uses_data_exploration_skill(self) -> None:
        prompt = build_dbt_charts_system_prompt(
            "database_exploration",
            database_context="Source: warehouse",
        )

        assert "# Data Exploration" in prompt
        assert "Source: warehouse" in prompt
        assert "Dashboard Design" not in prompt
        # Pure schema exploration builds no artifact — no runbook there.
        assert "Analyst Runbook" not in prompt


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


class TestAnalystRunbookSkill:
    """The analyst-runbook adds the process layer the building skills lack."""

    def test_runbook_renders_on_tool_surface(self) -> None:
        prompt = load_shared_prompt("analyst-runbook", surface="tool")

        assert "Analyst Runbook" in prompt
        assert "{{ s_" not in prompt  # all macros resolved

    def test_runbook_has_intake_triage_front(self) -> None:
        """The genuinely-missing piece: triage + reuse-first before building."""
        prompt = load_shared_prompt("analyst-runbook", surface="tool")

        assert "triage" in prompt.lower()
        assert "search_files" in prompt  # reuse-first, mandatory step

    def test_runbook_verifies_the_answer_not_only_the_artifact(self) -> None:
        prompt = load_shared_prompt("analyst-runbook", surface="tool")

        assert "Right source" in prompt
        assert "Right question" in prompt

    def test_runbook_ships_analysis_method_patterns(self) -> None:
        prompt = load_shared_prompt("analyst-runbook", surface="tool")

        assert "retention" in prompt.lower()
        assert "funnel" in prompt.lower()
        assert "decomposition" in prompt.lower()

    def test_runbook_asks_for_a_written_read_of_the_charts(self) -> None:
        """Charts alone aren't the answer — the delivery step owes the user
        observations drawn from the numbers actually rendered."""
        prompt = load_shared_prompt("analyst-runbook", surface="tool")

        assert "What it shows" in prompt
        assert "Where next" in prompt

    def test_runbook_delivery_read_comes_after_verification(self) -> None:
        prompt = load_shared_prompt("analyst-runbook", surface="tool")

        assert prompt.index("Right question") < prompt.index("What it shows")

    def test_runbook_description_advertises_the_delivery_step(self) -> None:
        """`build_skills_index` ships the description and nothing else, so the
        index-only surfaces (Cloud, terminal agent) learn about the delivery
        step here or not at all."""
        from dbt_charts.agent_api.skills import skill_description

        description = skill_description("analyst-runbook")

        assert "deliver" in description.lower()
        assert "handing the work back" in description


class TestDashboardBuildDeliveryDiscipline:
    """act-don't-ask bans asking permission, not offering where to look next."""

    def test_act_dont_ask_no_longer_bans_follow_ups(self) -> None:
        prompt = load_shared_prompt("board-build", surface="tool")

        assert "Act, don't ask" in prompt
        assert "offer optional follow-ups" not in prompt

    def test_delivery_discipline_points_at_the_runbook_without_restating_it(
        self,
    ) -> None:
        """A paraphrase here would carry none of §4's skip cases (a bare number,
        a pure layout edit) and would drift the moment the runbook changes."""
        prompt = load_shared_prompt("board-build", surface="tool")

        assert "analyst-runbook" in prompt
        assert "What it shows" not in prompt
        assert "Where next" not in prompt


class TestBuildSkillsIndex:
    """The index replaces inlined skill bodies with name + description."""

    def test_lists_every_indexed_skill_by_name(self) -> None:
        index = build_skills_index()

        assert "**analyst-runbook**" in index
        assert "**board-build**" in index
        assert "**board-design**" in index
        assert "**board-review**" in index
        assert "**board-replicate**" in index

    def test_includes_descriptions_not_bodies(self) -> None:
        from dbt_charts.agent_api.skills import skill_description

        index = build_skills_index()

        assert skill_description("board-build") in index
        assert "is how you deliver a dashboard" not in index

    def test_points_at_get_skill_to_load_full_guide(self) -> None:
        index = build_skills_index()

        assert "get_skill" in index

    def test_raises_if_an_indexed_skill_has_no_description(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hardcoded index entry silently going empty (renamed/removed
        skill) is a broken-wheel condition, not a normal miss — fail loudly."""
        import dbt_charts.ai.prompts as prompts_module

        monkeypatch.setattr(prompts_module, "skill_description", lambda name: "")

        with pytest.raises(ValueError, match="analyst-runbook"):
            build_skills_index()


class TestBuildDocsPointer:
    """The docs pointer replaces the inlined DBT_CHARTS_SYNTAX.md reference."""

    def test_names_topic_slugs_from_the_live_docs_index(self) -> None:
        """Slugs, not display titles — the model passes this straight to
        docs(topic=...), so it must be the argument value, not "Getting
        Started"-style prose the model would have to re-slugify."""
        pointer = build_docs_pointer()

        assert "charts" in pointer
        assert "variables" in pointer
        assert "layout" in pointer
        assert "getting-started" in pointer

    def test_does_not_inline_reference_content(self) -> None:
        pointer = build_docs_pointer()

        assert "Unknown chart fields are rejected." not in pointer

    def test_points_at_the_docs_tool(self) -> None:
        pointer = build_docs_pointer()

        assert "docs(" in pointer


class TestSkillsIndexPromptReferencesRealTools:
    """Pins the contract progressive disclosure depends on: every tool name
    the index/pointer prose tells the model to call actually exists in
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

    def test_docs_pointer_reference_is_a_real_agent_tool(self) -> None:
        from dbt_charts.ai.tool_schemas import AGENT_TOOLS

        tool_names = {tool["name"] for tool in AGENT_TOOLS if "name" in tool}
        assert "docs" in tool_names
        assert "docs" in build_docs_pointer()


class TestDashboardBuildSkill:
    """The shared build workflow owns how boards are written, on every host."""

    def test_teaches_the_duplicate_pattern(self) -> None:
        """Regression for cloud-copilot-refuses-to-duplicate-an-existing-dashboard.

        Asked to duplicate five dashboards, the agent claimed it had no
        byte-for-byte copy tool and handed the work back — while holding both
        `read_file` and `write_file`. Copying is those two composed, and no
        skill said so. This lives here, not in a host's guidance, so every
        surface learns it.
        """
        prompt = " ".join(load_shared_prompt("board-build", surface="tool").split())

        assert "duplicate" in prompt.lower()
        assert "read_file" in prompt
        assert "write_file" in prompt

    def test_a_direct_instruction_is_work_to_do_on_every_host(self) -> None:
        """The refusal this fixes came from a preview-first host default read as
        outranking a direct instruction. That carve-out belongs in the shared
        skill: a host block that states it wins only on that host, and every
        other surface keeps the bug.
        """
        prompt = " ".join(load_shared_prompt("board-build", surface="tool").split())

        assert "A direct instruction is work to do, not work to hand back" in prompt

    def test_shared_skill_defers_write_scope_to_the_host(self) -> None:
        """How many files a request may touch, and where writes may land, is a
        permission question — and this layer has no principal concept, so it
        must not answer it.

        Cloud's `write_file`/`apply_patch` are not access-checked per destination
        (unlike `move_file`/`delete_file`, which route through the governed
        relocation service). Host-agnostic prose sanctioning a folder-wide
        sweep would tell every agent that an unchecked bulk write is fine.
        """
        prompt = " ".join(load_shared_prompt("board-build", surface="tool").split())

        assert "Scope is your host's to define" in prompt
        # No blanket folder-sweep license from a layer that cannot check access.
        assert "copy everything under" not in prompt
        assert "as explicit as one over a filename" not in prompt

    def test_file_tool_guidance_is_dropped_on_hosts_without_file_tools(self) -> None:
        """Playground and A lIe load this skill with no file tool at all.

        Teaching them `write_file` invites a hallucinated call or a claimed
        save that cannot happen — the same failure class this section exists
        to remove, just moved to another surface. The block is gated so those
        hosts never see it.
        """
        without = load_shared_prompt(
            "board-build",
            surface="tool",
            available_tools={"render_board"},
        )

        assert "read_file" not in without
        assert "write_file" not in without
        assert "Duplicating a board" not in without
        assert "An explicit ask is your authorization" not in without
        assert "{{#if_tool" not in without and "{{/if_tool}}" not in without
        # The host-agnostic save guidance around it survives the gate.
        assert "Previewing vs. saving a board" in without

    def test_file_tool_guidance_renders_where_the_tool_exists(self) -> None:
        with_tools = load_shared_prompt(
            "board-build",
            surface="tool",
            available_tools={"render_board", "read_file", "write_file"},
        )

        assert "Duplicating a board" in with_tools
        assert "read_file" in with_tools and "write_file" in with_tools

    def test_save_step_does_not_contradict_the_explicit_ask_carve_out(self) -> None:
        """The save step tells preview-first hosts that saving is the user's
        click. Left unqualified it flatly contradicts the carve-out above, and
        an agent reading both on Cloud resolves it by handing the work back —
        the original bug. The exception has to travel with the rule.
        """
        prompt = " ".join(load_shared_prompt("board-build", surface="tool").split())

        step_six = prompt[prompt.index("Step 6") :].lower()
        assert "saving an unprompted preview is the user's click" in step_six
        assert "an explicit ask is your authorization on every host" in step_six


class TestDashboardReplicateSkill:
    """The replication workflow keeps fake-data work visibly provisional."""

    def test_fake_data_banner_uses_native_warning_callout(self) -> None:
        prompt = load_shared_prompt("board-replicate", surface="tool")

        assert "type: callout" in prompt
        assert "tone: warning" in prompt
        assert prompt.count("- fake_data_warning") == 2

    def test_description_does_not_claim_in_project_duplication(self) -> None:
        """This skill rebuilds a *foreign* dashboard from a screenshot — two
        phases, inline fake data, a warning banner. Its trigger list used to
        include 'copy this dashboard' and 'move my dashboard here', which are
        how users ask to duplicate a board that already exists in the project.
        Matching those routes an in-project copy into a fake-data rebuild.
        """
        description = get_skill("board-replicate").description.lower()

        assert "copy this dashboard" not in description
        assert "move my dashboard here" not in description
        # The screenshot/export replication triggers are its real job — keep them.
        assert "replicate" in description
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
