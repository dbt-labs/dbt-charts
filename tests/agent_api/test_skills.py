"""Tests for dbt_charts.agent_api.skills registry."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.agent_api import ProjectSession
from dbt_charts.agent_api.skills import (
    _SKILLS_DIR,
    AUTHORED_SKILL_BODY_MAX_CHARS,
    Skill,
    SkillNotFound,
    get_skill,
    list_skills,
    search_skills,
    skill_description,
)
from dbt_charts.ai.tools import TOOL_HANDLERS
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import raise_on_dashboard_failure
from dbt_charts.core.compile import compile_file

# _SKILLS_DIR is a Traversable (importlib.resources) — no `.glob()` in that
# protocol. Every shipped install is a real on-disk directory, so `str()` +
# `Path()` round-trips to the same location for this glob-based discovery.
_SKILLS_DIR_PATH = Path(str(_SKILLS_DIR))
EXAMPLES = sorted(_SKILLS_DIR_PATH.glob("*/examples/*.yml"))
SKILL_MARKDOWN = sorted(_SKILLS_DIR_PATH.glob("*/SKILL.md")) + sorted(
    _SKILLS_DIR_PATH.glob("*/references/*.md")
)
PACKAGED_SKILL_NAMES = sorted(
    p.parent.name for p in _SKILLS_DIR_PATH.glob("*/SKILL.md")
)

LEGACY_TOOL_NAMES = (
    "check_dashboard",
    "test_execution",
    "save_dashboard",
    "get_database_schema",
    "inspect_table",
    "validate_dashboard",
    "render_dashboard",
    "search_dashboards",
    "describe_dashboard",
    "query_face",
    "suggest_dashboard_placement",
)

# Matches any leftover `{{ s_X }}` macro after rendering.
# If this matches a rendered body, the surface_aliases.yaml is missing an entry
# OR render_skill_body was not called.
_LEFTOVER_MACRO_RE = re.compile(r"\{\{\s*s_[a-z][a-z0-9_]*\s*\}\}")
# Matches any unconsumed {{#if_tool ...}} or {{/if_tool}} marker after rendering.
# If this fires, a marker was malformed/misspelled so _IF_TOOL_RE didn't match it
# and the literal token leaked into the agent's context window.
_LEFTOVER_IF_TOOL_RE = re.compile(r"\{\{#if_tool|\{\{/if_tool\}\}")
# Captures the NAME out of a raw (pre-render) {{#if_tool NAME}} opening marker,
# for the marker-name validity check below -- distinct from _LEFTOVER_IF_TOOL_RE,
# which only detects an unconsumed marker in a RENDERED body.
_IF_TOOL_MARKER_RE = re.compile(r"\{\{#if_tool\s+([a-z][a-z0-9_]*)\s*\}\}")


class TestListSkills:
    def test_includes_known_skill(self) -> None:
        result = list_skills()
        names = [s.name for s in result.skills]
        assert "board-build" in names

    def test_skills_have_required_fields(self) -> None:
        result = list_skills()
        for skill in result.skills:
            assert skill.name
            assert skill.description
            assert isinstance(skill.directory, Path)
            assert skill.directory.is_dir()
            assert skill.body  # non-empty markdown body


class TestGetSkill:
    def test_returns_skill_by_name(self) -> None:
        skill = get_skill("board-build")
        assert skill.name == "board-build"
        assert skill.description

    def test_raises_on_unknown_name(self) -> None:
        with pytest.raises(SkillNotFound):
            get_skill("no-such-skill-xyz")

    def test_directory_resolves_to_skill_dir(self) -> None:
        skill = get_skill("board-build")
        assert skill.directory is not None, "a builtin skill is file-backed"
        assert (skill.directory / "SKILL.md").is_file()

    def test_available_tools_gates_if_tool_blocks_in_the_body(self) -> None:
        """Same contract as `load_shared_prompt`'s `available_tools` — a caller
        that holds less than the full tool surface (the MCP connector) must be
        able to gate `{{#if_tool}}` blocks the same way."""
        without_write = get_skill(
            "board-build",
            available_tools={"render_board", "validate_board"},
        )
        assert "write_file" not in without_write.body

        with_write = get_skill(
            "board-build",
            available_tools={"render_board", "validate_board", "write_file"},
        )
        assert "write_file" in with_write.body

    def test_available_tools_none_keeps_every_block(self) -> None:
        """The default (`None`) is the CLI/full-chat/stdio-MCP contract — no
        gating, every `{{#if_tool}}` block survives."""
        skill = get_skill("board-build")
        assert "write_file" in skill.body


class TestSkillDirectoryTyping:
    """``Skill.directory`` is typed ``Traversable``, not ``Any`` — pydantic
    validates it at construction and the field still serializes to a plain
    string on the MCP/tool-call wire."""

    def test_rejects_value_missing_traversable_protocol(self) -> None:
        """A value that doesn't satisfy Traversable (iterdir/is_dir/is_file/
        joinpath/...) is rejected. Under the old `Any` typing this was
        accepted silently — retyping closes that validation hole.

        Built via ``model_validate`` (not the keyword constructor) since the
        bad ``directory`` value is deliberately the wrong type — the point
        under test is the *runtime* validation, not a static one.
        """
        with pytest.raises(ValidationError):
            Skill.model_validate(
                {
                    "name": "x",
                    "description": "x",
                    "kind": "pattern",
                    "directory": "not-a-traversable",
                    "body": "body",
                }
            )

    def test_directory_serializes_to_str_on_wire(self) -> None:
        skill = get_skill("board-build")
        dumped = skill.model_dump(mode="json")
        assert dumped["directory"] == str(skill.directory)
        assert isinstance(dumped["directory"], str)


class TestSkillDescription:
    """skill_description matches get_skill(...).description without the
    per-call surface-macro render pass over the body."""

    def test_matches_get_skill_description(self) -> None:
        assert skill_description("board-build") == get_skill("board-build").description

    def test_unknown_name_returns_empty_string_not_raise(self) -> None:
        assert skill_description("no-such-skill-xyz") == ""

    def test_description_renders_sibling_macro_per_surface(self) -> None:
        """board-build's description references board-design: bare on both
        tool and cli. `dct skills` resolves names against the bare registry,
        so the cli-rendered description must not print a name it would then
        reject."""
        tool_description = get_skill("board-build", surface="tool").description
        cli_description = get_skill("board-build", surface="cli").description
        assert "board-design" in tool_description
        assert "dct-board-design" not in tool_description
        assert "board-design" in cli_description
        assert "dct-board-design" not in cli_description

    def test_skill_description_helper_also_renders(self) -> None:
        assert (
            skill_description("board-build", surface="cli")
            == get_skill("board-build", surface="cli").description
        )

    def test_search_skills_matches_description_only_via_macro_rendering(self) -> None:
        """board-visual-review's raw frontmatter description embeds
        `{{ s_skill_name_structural_review }}` inside a sentence found nowhere
        in its body — the phrase below only exists once rendered. If
        search_skills scores the raw, unrendered description, this hit is
        unreachable even though the rendered text is what the agent reads."""
        result = search_skills(
            "data-shape problems (use board-structural-review)", surface="tool"
        )

        assert [hit.name for hit in result.hits] == ["board-visual-review"]


def _write_project_skill(
    root: Path,
    relpath: str,
    *,
    name: str,
    description: str = "A project skill.",
    kind: str = "pattern",
    body: str = "Do the project-specific thing.\n",
) -> None:
    skill_dir = root / relpath
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nkind: {kind}\n---\n{body}"
    )


class TestProjectSkills:
    """A project's own skills/ (+ .claude/skills/, .agents/skills/) union into
    the same registry as built-ins — the skills half of the
    honor-project-agents-md-and-skills task. Labeled source="project" so the
    model (and Cloud UI) can tell them apart from built-ins; on-demand only
    (list_skills/get_skill/search_skills), never inlined into the always-on
    prompt prefix."""

    def test_project_skill_appears_in_list_skills(self, tmp_path: Path) -> None:
        _write_project_skill(tmp_path, "skills/my-metric", name="my-metric")
        project = FilesystemProject(tmp_path)

        result = list_skills(project=project)

        by_name = {s.name: s for s in result.skills}
        assert "my-metric" in by_name
        assert by_name["my-metric"].source == "project"

    def test_builtin_skills_are_labeled_builtin(self) -> None:
        result = list_skills()
        by_name = {s.name: s for s in result.skills}
        assert by_name["board-build"].source == "builtin"

    def test_without_project_arg_project_skills_are_invisible(
        self, tmp_path: Path
    ) -> None:
        _write_project_skill(tmp_path, "skills/my-metric", name="my-metric")

        result = list_skills()

        assert "my-metric" not in {s.name for s in result.skills}

    def test_project_skill_loadable_via_get_skill(self, tmp_path: Path) -> None:
        _write_project_skill(
            tmp_path, "skills/my-metric", name="my-metric", body="Special guidance.\n"
        )
        project = FilesystemProject(tmp_path)

        skill = get_skill("my-metric", project=project)

        assert "Special guidance." in skill.body
        assert skill.source == "project"

    def test_get_skill_without_project_does_not_see_project_skill(
        self, tmp_path: Path
    ) -> None:
        _write_project_skill(tmp_path, "skills/my-metric", name="my-metric")

        with pytest.raises(SkillNotFound):
            get_skill("my-metric")

    @pytest.mark.parametrize("location", ["skills", ".claude/skills", ".agents/skills"])
    def test_honors_all_standard_project_skill_locations(
        self, tmp_path: Path, location: str
    ) -> None:
        _write_project_skill(tmp_path, f"{location}/my-metric", name="my-metric")
        project = FilesystemProject(tmp_path)

        result = list_skills(project=project)

        assert "my-metric" in {s.name for s in result.skills}

    def test_dct_prefixed_project_dir_is_skipped(self, tmp_path: Path) -> None:
        """`dct init skills` writes `dct-board-build/SKILL.md` with
        `name: dct-board-build` into `.claude/skills/`: the project-skill
        scanner must not re-read its own file-install output as a second,
        distinct registry entry."""
        _write_project_skill(
            tmp_path, ".claude/skills/dct-board-build", name="dct-board-build"
        )
        project = FilesystemProject(tmp_path)

        result = list_skills(project=project)

        assert "dct-board-build" not in {s.name for s in result.skills}

    def test_project_skill_overrides_builtin_of_same_name(self, tmp_path: Path) -> None:
        _write_project_skill(
            tmp_path,
            "skills/board-build",
            name="board-build",
            description="Overridden by the project.",
            kind="workflow",
            body="Project override body.\n",
        )
        project = FilesystemProject(tmp_path)

        skill = get_skill("board-build", project=project)

        assert skill.source == "project"
        assert "Project override body." in skill.body

    def test_malformed_project_skill_surfaces_as_error_not_raise(
        self, tmp_path: Path
    ) -> None:
        skill_dir = tmp_path / "skills" / "broken"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("not frontmatter at all")
        project = FilesystemProject(tmp_path)

        result = list_skills(project=project)

        assert result.success
        assert any("broken" in error for error in result.errors)
        assert "broken" not in {s.name for s in result.skills}

    def test_search_skills_finds_project_skill(self, tmp_path: Path) -> None:
        _write_project_skill(
            tmp_path,
            "skills/my-metric",
            name="my-metric",
            description="Explains recognized ARR.",
        )
        project = FilesystemProject(tmp_path)

        result = search_skills("recognized ARR", project=project)

        assert any(hit.name == "my-metric" for hit in result.hits)

    def test_search_skills_without_project_misses_project_skill(
        self, tmp_path: Path
    ) -> None:
        _write_project_skill(
            tmp_path,
            "skills/my-metric",
            name="my-metric",
            description="Explains recognized ARR.",
        )

        result = search_skills("recognized ARR")

        assert result.hits == []

    def test_project_skill_body_is_framed_as_guidance_not_authority(
        self, tmp_path: Path
    ) -> None:
        """Same trust posture as AGENTS.md (load_project_instructions): a
        project-authored skill body must say it's project context, not system
        policy, and can't override our tool-use policy — not just a raw
        content dump labeled source="project"."""
        _write_project_skill(
            tmp_path, "skills/my-metric", name="my-metric", body="Special guidance.\n"
        )
        project = FilesystemProject(tmp_path)

        skill = get_skill("my-metric", project=project)

        assert "Special guidance." in skill.body
        assert "not" in skill.body.lower()
        assert "polic" in skill.body.lower() or "authorit" in skill.body.lower()

    def test_builtin_skill_body_is_not_reframed(self) -> None:
        """The disclaimer wrap is project-only — built-in skills are our own
        trusted content and must render exactly as authored."""
        skill = get_skill("board-build")

        assert "not system policy" not in skill.body

    def test_oversized_project_skill_body_truncates_with_notice(
        self, tmp_path: Path
    ) -> None:
        _write_project_skill(
            tmp_path,
            "skills/my-metric",
            name="my-metric",
            body="x" * (AUTHORED_SKILL_BODY_MAX_CHARS * 2),
        )
        project = FilesystemProject(tmp_path)

        skill = get_skill("my-metric", project=project)

        assert "truncated" in skill.body.lower()
        assert "x" in skill.body
        assert len(skill.body) < AUTHORED_SKILL_BODY_MAX_CHARS * 2

    def test_authored_body_between_old_and_new_cap_is_served_whole(
        self, tmp_path: Path
    ) -> None:
        """A body past the old 6k limit arrives whole — the cap bounds abuse,
        not legitimate authored content."""
        body = "x" * 8_000
        _write_project_skill(tmp_path, "skills/my-metric", name="my-metric", body=body)
        project = FilesystemProject(tmp_path)

        skill = get_skill("my-metric", project=project)

        assert "truncated" not in skill.body.lower()
        assert body in skill.body

    def test_project_skill_body_with_macro_shaped_text_does_not_crash_list_skills(
        self, tmp_path: Path
    ) -> None:
        """A project author has no knowledge of the wheel-internal
        `{{ s_key }}` macro convention (surface_aliases.yaml) — an innocuous
        body containing that shape must never raise MissingSurfaceAlias and
        take down the whole list_skills call. Project bodies are already
        final text and must never be macro-rendered."""
        _write_project_skill(
            tmp_path,
            "skills/my-metric",
            name="my-metric",
            body="Use the {{ s_curve }} model to fit growth.\n",
        )
        project = FilesystemProject(tmp_path)

        result = list_skills(project=project)

        by_name = {s.name: s for s in result.skills}
        assert "my-metric" in by_name
        assert "{{ s_curve }}" in by_name["my-metric"].body

    def test_project_skill_body_with_macro_shaped_text_does_not_crash_get_skill(
        self, tmp_path: Path
    ) -> None:
        _write_project_skill(
            tmp_path,
            "skills/my-metric",
            name="my-metric",
            body="Use the {{ s_curve }} model to fit growth.\n",
        )
        project = FilesystemProject(tmp_path)

        skill = get_skill("my-metric", project=project)

        assert "{{ s_curve }}" in skill.body

    def test_project_skill_description_with_macro_shaped_text_does_not_crash_list_skills(
        self, tmp_path: Path
    ) -> None:
        """The description twin of the two body tests above:
        `_rendered_description` carries the identical `source != "builtin"`
        guard as `_rendered_body`, so it needs the same proof — a
        macro-shaped description must never raise MissingSurfaceAlias and
        take down list_skills for every skill, built-ins included."""
        _write_project_skill(
            tmp_path,
            "skills/my-metric",
            name="my-metric",
            description="Use the {{ s_totally_unknown_key }} approach.",
        )
        project = FilesystemProject(tmp_path)

        result = list_skills(project=project)

        by_name = {s.name: s for s in result.skills}
        assert "my-metric" in by_name
        assert "{{ s_totally_unknown_key }}" in by_name["my-metric"].description

    def test_project_skill_body_with_macro_shaped_text_does_not_crash_search_skills(
        self, tmp_path: Path
    ) -> None:
        """Search term matches only in the body (not name/description) so the
        search actually reaches the body-render branch — a body-only match on
        a name/description miss must not crash on macro-shaped text either."""
        _write_project_skill(
            tmp_path,
            "skills/my-metric",
            name="my-metric",
            description="Project metric guidance.",
            body="Use the {{ s_curve }} model to fit revenuegrowthonly.\n",
        )
        project = FilesystemProject(tmp_path)

        result = search_skills("revenuegrowthonly", project=project)

        assert any(hit.name == "my-metric" for hit in result.hits)

    def test_search_does_not_match_the_disclaimer_boilerplate(
        self, tmp_path: Path
    ) -> None:
        """_format_authored_skill_body prepends the same guidance-not-authority
        disclaimer to every project skill ("...tool-use policy... capability
        beyond..."). Search must score against the raw, undecorated body —
        otherwise a query like "policy" or "capability" would spuriously
        match every project skill via that identical boilerplate."""
        _write_project_skill(
            tmp_path,
            "skills/my-metric",
            name="my-metric",
            description="Project metric guidance.",
            body="Revenue is recognized ARR.\n",
        )
        project = FilesystemProject(tmp_path)

        result = search_skills("capability", project=project)

        assert "my-metric" not in {hit.name for hit in result.hits}


class TestExtraSkillFiles:
    """A project may point at arbitrary extra files (Cloud's Chat Settings ->
    extra skill paths) to expose as on-demand skills, without hand-authoring
    SKILL.md frontmatter — the point is letting a project reuse an existing doc
    (a style guide, a glossary) as-is. Merged the same way as directory-scanned
    project skills: unioned into list_skills/get_skill/search_skills, labeled
    source="project", framed as guidance-not-authority."""

    def test_extra_skill_file_appears_in_list_skills(self, tmp_path: Path) -> None:
        (tmp_path / "style-guide.md").write_text("# Style Guide\n\nUse title case.\n")
        project = FilesystemProject(tmp_path)

        result = list_skills(project=project, extra_skill_files=("style-guide.md",))

        by_name = {s.name: s for s in result.skills}
        assert "style-guide" in by_name
        assert by_name["style-guide"].source == "project"

    def test_extra_skill_file_invisible_without_extra_arg(self, tmp_path: Path) -> None:
        (tmp_path / "style-guide.md").write_text("# Style Guide\n\nUse title case.\n")
        project = FilesystemProject(tmp_path)

        result = list_skills(project=project)

        assert "style-guide" not in {s.name for s in result.skills}

    def test_extra_skill_file_loadable_via_get_skill(self, tmp_path: Path) -> None:
        (tmp_path / "style-guide.md").write_text("Use title case.\n")
        project = FilesystemProject(tmp_path)

        skill = get_skill(
            "style-guide", project=project, extra_skill_files=("style-guide.md",)
        )

        assert "Use title case." in skill.body
        assert skill.source == "project"

    def test_extra_skill_file_name_derived_from_filename(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "Metric_Glossary.md").write_text("ARR definitions.\n")
        project = FilesystemProject(tmp_path)

        result = list_skills(
            project=project, extra_skill_files=("docs/Metric_Glossary.md",)
        )

        assert "metric-glossary" in {s.name for s in result.skills}

    def test_extra_skill_file_description_from_first_line(self, tmp_path: Path) -> None:
        (tmp_path / "style-guide.md").write_text("# Voice and Tone\n\nBody text.\n")
        project = FilesystemProject(tmp_path)

        skill = get_skill(
            "style-guide", project=project, extra_skill_files=("style-guide.md",)
        )

        assert skill.description == "Voice and Tone"

    def test_missing_extra_skill_file_surfaces_as_error_not_raise(
        self, tmp_path: Path
    ) -> None:
        project = FilesystemProject(tmp_path)

        result = list_skills(project=project, extra_skill_files=("no-such-file.md",))

        assert result.success
        assert any("no-such-file.md" in error for error in result.errors)

    def test_extra_skill_file_is_framed_as_guidance_not_authority(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "style-guide.md").write_text("Special guidance.\n")
        project = FilesystemProject(tmp_path)

        skill = get_skill(
            "style-guide", project=project, extra_skill_files=("style-guide.md",)
        )

        assert "Special guidance." in skill.body
        assert "not" in skill.body.lower()
        assert "polic" in skill.body.lower() or "authorit" in skill.body.lower()

    def test_search_skills_finds_extra_skill_file(self, tmp_path: Path) -> None:
        (tmp_path / "style-guide.md").write_text("Explains recognized ARR.\n")
        project = FilesystemProject(tmp_path)

        result = search_skills(
            "recognized ARR", project=project, extra_skill_files=("style-guide.md",)
        )

        assert any(hit.name == "style-guide" for hit in result.hits)

    def test_empty_skill_dirs_skips_directory_scan_but_keeps_extra_skill_files(
        self, tmp_path: Path
    ) -> None:
        """The seam Cloud's Chat Settings uses: skill_dirs=() turns off the
        skills/ folder scan (honor_agent_instructions off) while extra_skill_files
        (a deliberate, curated list) still resolves off the same project."""
        _write_project_skill(tmp_path, "skills/my-metric", name="my-metric")
        (tmp_path / "style-guide.md").write_text("Use title case.\n")
        project = FilesystemProject(tmp_path)

        result = list_skills(
            project=project, skill_dirs=(), extra_skill_files=("style-guide.md",)
        )

        names = {s.name for s in result.skills}
        assert "my-metric" not in names
        assert "style-guide" in names


class TestFrontmatterValidation:
    def test_frontmatter_name_matches_directory(self, tmp_path: Path) -> None:
        """Registry must error when SKILL.md name: disagrees with its directory."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: wrong-name\ndescription: A test skill.\nkind: pattern\n---\n\n# My Skill\n"
        )

        from dbt_charts.agent_api.skills import _parse_skill_dir

        with pytest.raises(ValueError, match="wrong-name"):
            _parse_skill_dir(skill_dir)

    def test_missing_kind_raises(self, tmp_path: Path) -> None:
        """Registry must error when SKILL.md frontmatter lacks `kind`."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: A test skill.\n---\n\n# My Skill\n"
        )

        from dbt_charts.agent_api.skills import _parse_skill_dir

        with pytest.raises(ValueError, match="kind"):
            _parse_skill_dir(skill_dir)

    def test_invalid_kind_raises(self, tmp_path: Path) -> None:
        """Registry must error when `kind` is not workflow or pattern."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: A test skill.\nkind: bogus\n---\n\n# My Skill\n"
        )

        from dbt_charts.agent_api.skills import _parse_skill_dir

        with pytest.raises(ValueError, match="kind"):
            _parse_skill_dir(skill_dir)


class TestPackagedSkillsHaveKind:
    def test_every_packaged_skill_declares_kind(self) -> None:
        result = list_skills()
        for skill in result.skills:
            assert skill.kind in (
                "workflow",
                "pattern",
            ), f"{skill.name}: kind={skill.kind!r}"

    def test_has_examples_reflects_examples_list(self) -> None:
        result = list_skills()
        for skill in result.skills:
            assert skill.has_examples == (len(skill.examples) > 0)


@pytest.mark.parametrize(
    "example_path",
    EXAMPLES,
    ids=lambda p: f"{p.parent.parent.name}/{p.name}",
)
def test_skill_example_compiles_and_renders(
    example_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = local_project(example_path.parent)
    board_path = project.path_for_fspath(example_path)
    compile_result = compile_file(board_path.read_board())
    assert not compile_result.errors, f"{example_path}: {compile_result.errors}"

    session = ProjectSession.from_project(project)
    try:
        rendered = session.render_board(board=board_path.read_board(), format="html")
        raise_on_dashboard_failure(rendered)
        output = rendered.data
    finally:
        session.close()
    assert output and len(output) > 0


@pytest.mark.parametrize("skill_path", SKILL_MARKDOWN, ids=lambda p: str(p))
def test_skills_reference_current_mcp_tool_names(skill_path: Path) -> None:
    text = skill_path.read_text(encoding="utf-8")
    stale = [name for name in LEGACY_TOOL_NAMES if name in text]

    assert stale == [], f"{skill_path} references stale tool names: {stale}"


def test_at_least_ten_skills_have_examples() -> None:
    result = list_skills()
    skills_with_examples = [s for s in result.skills if s.examples]
    assert len(skills_with_examples) >= 10, (
        f"Expected >= 10 skills with examples, found {len(skills_with_examples)}: "
        + ", ".join(s.name for s in skills_with_examples)
    )


# --- Surface rendering fan-out ----------------------------------------------
#
# Every packaged skill must render cleanly on every surface it claims to
# support. Catches: typo'd macro keys (`{{ s_validat }}`), missing entries
# in surface_aliases.yaml, accidentally-skipped surface filtering, and any
# regression where a SKILL.md ships with an unexpanded macro.


@pytest.mark.parametrize("skill_name", PACKAGED_SKILL_NAMES)
@pytest.mark.parametrize("surface", ["tool", "cli"])
def test_packaged_skill_renders_cleanly_on_each_surface(
    skill_name: str, surface: str
) -> None:
    """Every wheel-shipped skill renders on every surface it declares.

    Skills that opt out of a surface (via `surfaces:` frontmatter) are
    skipped on that surface — ``get_skill`` raises ``SkillNotFound`` and
    we treat that as the documented contract, not a failure.
    """
    try:
        skill = get_skill(skill_name, surface=surface)  # type: ignore[arg-type]
    except SkillNotFound:
        pytest.skip(f"{skill_name!r} is not exposed on surface {surface!r}")

    leftovers = _LEFTOVER_MACRO_RE.findall(skill.body)
    assert not leftovers, (
        f"{skill_name} ({surface}): unexpanded macros {leftovers!r} — "
        "either the key is missing from surface_aliases.yaml or "
        "`render_skill_body` was not called."
    )

    if_tool_leftovers = _LEFTOVER_IF_TOOL_RE.findall(skill.body)
    assert not if_tool_leftovers, (
        f"{skill_name} ({surface}): residual {{{{#if_tool}}}} markers {if_tool_leftovers!r} — "
        "a marker was malformed/misspelled/unclosed so _IF_TOOL_RE did not consume it; "
        "the literal token would leak into the agent's context window."
    )


@pytest.mark.parametrize("skill_path", SKILL_MARKDOWN, ids=lambda p: str(p))
def test_every_if_tool_marker_names_a_real_tool(skill_path: Path) -> None:
    """A misspelled `{{#if_tool NAME}}` marker silently drops its block on every
    surface that gates (the MCP connector) and survives everywhere else, so no
    rendered-body test notices. Check the raw markdown instead."""
    text = skill_path.read_text(encoding="utf-8")
    names = _IF_TOOL_MARKER_RE.findall(text)
    stale = [name for name in names if name not in TOOL_HANDLERS]
    assert not stale, (
        f"{skill_path}: {{{{#if_tool}}}} marker(s) {stale!r} name no real "
        f"tool (TOOL_HANDLERS has {sorted(TOOL_HANDLERS)!r})"
    )


def test_mcp_setup_is_cli_only() -> None:
    """`mcp-setup` is CLI-only — invisible to tool-call agents. Registry name
    stays bare; the `dct-` prefix is introduced only on the CLI install surface."""
    cli_skill = get_skill("mcp-setup", surface="cli")
    assert cli_skill.name == "mcp-setup"
    assert "cli" in cli_skill.surfaces

    with pytest.raises(SkillNotFound):
        get_skill("mcp-setup", surface="tool")

    cli_names = {s.name for s in list_skills(surface="cli").skills}
    tool_names = {s.name for s in list_skills(surface="tool").skills}
    assert "mcp-setup" in cli_names
    assert "mcp-setup" not in tool_names


def test_dashboard_pack_scaffolding_is_tool_only() -> None:
    """Pack scaffolding remains available to agent tools, not public CLI help."""
    tool_skill = get_skill("board-pack-scaffolding", surface="tool")
    assert tool_skill.name == "board-pack-scaffolding"
    assert "tool" in tool_skill.surfaces

    with pytest.raises(SkillNotFound):
        get_skill("board-pack-scaffolding", surface="cli")

    cli_names = {s.name for s in list_skills(surface="cli").skills}
    tool_names = {s.name for s in list_skills(surface="tool").skills}
    assert "board-pack-scaffolding" not in cli_names
    assert "board-pack-scaffolding" in tool_names


def test_surface_aliases_yaml_has_no_dead_entries() -> None:
    """Every alias in surface_aliases.yaml must be referenced by some SKILL.md.

    Dead entries rot silently — the surface fan-out test only catches the
    inverse direction (macro used → alias must exist). Add entries when an
    author reaches for them, not preemptively.
    """
    from dbt_charts.agent_api.skill_render import _aliases

    aliases = set(_aliases())
    referenced: set[str] = set()
    macro_re = re.compile(r"\{\{\s*s_([a-z][a-z0-9_]*)\s*\}\}")
    for skill_md in _SKILLS_DIR_PATH.glob("*/SKILL.md"):
        for match in macro_re.finditer(skill_md.read_text(encoding="utf-8")):
            referenced.add(match.group(1))

    unused = sorted(aliases - referenced)
    assert not unused, (
        f"surface_aliases.yaml has unused entries: {unused!r}. "
        "Either reference them from a SKILL.md in this PR or drop them — "
        "add new aliases when an author actually reaches for one."
    )


def test_rendered_skill_marks_its_surface() -> None:
    """`Skill.rendered_for` records which surface the body was rendered for."""
    for surface in ("tool", "cli"):
        skill = get_skill("board-build", surface=surface)  # type: ignore[arg-type]
        assert skill.rendered_for == surface


def test_load_all_enumerates_skills_via_iterdir_not_glob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_glob_traversable: Callable[[Path], Any],
) -> None:
    """Regression: the skill registry must walk _SKILLS_DIR via iterdir(),
    never glob() — this test's double implements only the
    importlib.resources.Traversable protocol."""
    import dbt_charts.agent_api.skills as skills_mod

    for name in ("zz-fake-b", "zz-fake-a"):
        skill_dir = tmp_path / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: fake skill.\nkind: pattern\n---\n\nBody.\n"
        )
    (tmp_path / "not-a-skill.txt").write_text("ignore me\n")

    monkeypatch.setattr(skills_mod, "_SKILLS_DIR", no_glob_traversable(tmp_path))
    skills_mod._load_all.cache_clear()
    try:
        names = sorted(skills_mod._load_all())
    finally:
        skills_mod._load_all.cache_clear()

    assert names == ["zz-fake-a", "zz-fake-b"]


def _user_skill(
    name: str,
    *,
    description: str = "A personal skill.",
    body: str = "Do the personal thing.\n",
) -> Skill:
    """A caller-constructed Skill, the shape Cloud builds from a UserSkill row.

    No ``directory`` — this skill is a database row with no files behind it.
    """
    return Skill(
        name=name,
        description=description,
        kind="pattern",
        directory=None,
        body=body,
        source="user",
    )


class TestExtraSkills:
    """Caller-supplied Skill objects union into the same registry as built-ins
    and project files. The store is the caller's problem (Cloud reads DB rows);
    the registry only needs the parsed shape, which keeps agent_api Django-free.
    """

    def test_extra_skill_appears_in_list_skills(self) -> None:
        result = list_skills(extra_skills=(_user_skill("weekly-status-report"),))

        by_name = {s.name: s for s in result.skills}
        assert "weekly-status-report" in by_name
        assert by_name["weekly-status-report"].source == "user"

    def test_extra_skill_invisible_without_the_arg(self) -> None:
        result = list_skills()

        assert "weekly-status-report" not in {s.name for s in result.skills}

    def test_extra_skill_loadable_via_get_skill(self) -> None:
        skill = get_skill(
            "weekly-status-report",
            extra_skills=(_user_skill("weekly-status-report"),),
        )

        assert "Do the personal thing." in skill.body

    def test_extra_skill_findable_via_search_skills(self) -> None:
        result = search_skills(
            "weekly-status",
            extra_skills=(_user_skill("weekly-status-report"),),
        )

        assert [h.name for h in result.hits] == ["weekly-status-report"]

    def test_directoryless_skill_serializes_directory_as_null(self) -> None:
        """A skill with no files must not name a path on the tool wire: for
        every other source that string is a real location, so a fabricated one
        invites the agent to read_file something that does not exist."""
        skill = get_skill("solo", extra_skills=(_user_skill("solo"),))

        assert skill.directory is None
        assert skill.model_dump(mode="json")["directory"] is None

    def test_extra_skill_needs_no_project(self) -> None:
        """A personal skill belongs to the user, not a project — it must
        resolve on a project-less surface too."""
        result = list_skills(project=None, extra_skills=(_user_skill("solo"),))

        assert "solo" in {s.name for s in result.skills}

    def test_extra_skill_overrides_project_skill_of_same_name(
        self, tmp_path: Path
    ) -> None:
        """The user's own skill is the nearest scope — a project must not be
        able to shadow a skill its user authored for themselves."""
        _write_project_skill(tmp_path, "skills/shared-name", name="shared-name")
        project = FilesystemProject(tmp_path)

        skill = get_skill(
            "shared-name",
            project=project,
            extra_skills=(_user_skill("shared-name", body="Personal wins.\n"),),
        )

        assert skill.source == "user"
        assert "Personal wins." in skill.body

    def test_extra_skill_body_is_framed_as_guidance_not_authority(self) -> None:
        """User-authored bodies are untrusted input like project ones — they
        must not read to the model as system policy."""
        skill = get_skill("solo", extra_skills=(_user_skill("solo"),))

        assert "polic" in skill.body.lower() or "authorit" in skill.body.lower()

    def test_oversized_extra_skill_body_truncates_with_notice(self) -> None:
        skill = get_skill(
            "solo",
            extra_skills=(
                _user_skill("solo", body="x" * (AUTHORED_SKILL_BODY_MAX_CHARS * 2)),
            ),
        )

        assert "truncated" in skill.body.lower()
        assert len(skill.body) < AUTHORED_SKILL_BODY_MAX_CHARS * 2

    def test_search_does_not_match_the_disclaimer_boilerplate(self) -> None:
        """Scoring runs against the raw body, so the shared wrapper text can't
        make every personal skill match an unrelated query."""
        result = search_skills("system policy", extra_skills=(_user_skill("solo"),))

        assert "solo" not in {h.name for h in result.hits}


# ---------------------------------------------------------------------------
# Visualization exclusivity
#
# The agents that author boards run inside hosts that ship their own charting
# and artifact tools. Disabling those is possible on some hosts and not others
# (Claude Code has a bundled-skill kill switch; Codex has no equivalent we can
# reach), so the durable guarantee has to live in the skill text itself, stated
# generically rather than by naming any one vendor's tool. This is the rule
# that survives a host we have never seen.
# ---------------------------------------------------------------------------

# Skills that compose an authoring system prompt or get installed to disk by
# `dct init skills` — the surfaces an agent actually reads before writing YAML.
_AUTHORING_SKILL = "board-build"


def test_authoring_skill_forbids_other_visualization_tools() -> None:
    """A host's own chart/artifact tools answer the same request differently —
    with a standalone image or HTML file instead of board YAML. The skill must
    rule them out in general terms, not per-vendor."""
    body = get_skill(_AUTHORING_SKILL, surface="cli").body.lower()

    assert "only visualization tool" in body, (
        f"{_AUTHORING_SKILL} no longer states that dbt charts is the only "
        "visualization tool the agent may use"
    )


def test_authoring_skill_teaches_single_chart_as_a_board() -> None:
    """ "Just a chart" is the request most likely to send an agent to another
    tool, so the skill has to say the dbt charts answer out loud: one chart in a
    board, no title."""
    body = get_skill(_AUTHORING_SKILL, surface="cli").body.lower()

    assert "a single chart is just a board with one chart in it" in body
    assert "leave `title:` off" in body


def test_visualization_rule_is_vendor_neutral() -> None:
    """Naming a vendor's tool dates the rule and misses every other host. The
    text must generalize instead — the eval harness names specific tools, the
    shipped skill must not."""
    body = get_skill(_AUTHORING_SKILL, surface="cli").body.lower()
    start = body.index("only visualization tool")
    rule = body[start : start + 700]

    named_vendors = [v for v in ("claude", "codex", "cursor", "copilot") if v in rule]
    assert named_vendors == [], (
        f"visualization rule names specific hosts {named_vendors}; state it "
        "generically so it holds on hosts we have not seen"
    )


def test_visualization_rule_reaches_every_authoring_prompt() -> None:
    """The rule is only worth writing if it is in context whenever an agent
    authors YAML. Pins that the carrying skill composes every authoring prompt
    type, so moving it into a narrower skill fails here rather than silently."""
    from dbt_charts.ai.prompts import _PROMPT_SKILLS, AUTHORING_PROMPT_TYPES

    for prompt_type in AUTHORING_PROMPT_TYPES:
        assert _AUTHORING_SKILL in _PROMPT_SKILLS[prompt_type], (
            f"{prompt_type} no longer includes {_AUTHORING_SKILL}, so the "
            "visualization-exclusivity rule is not in its system prompt"
        )


def test_visualization_rule_is_installed_to_disk_for_cli_agents() -> None:
    """Claude Code and Codex read skills off disk, not from our system prompt.
    The rule only reaches them if its skill is in the file-install set."""
    from dbt_charts.agent_api.skill_install import skills_for_file_install

    assert _AUTHORING_SKILL in {s.name for s in skills_for_file_install()}
