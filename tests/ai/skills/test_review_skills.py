"""Prompt-content regression tests for the three dashboard-review skills.

These tests are deterministic — they read the SKILL.md files from disk and
assert that the critical checklist items, severity tags, and protocol commands
remain present. They guard against accidental deletion or rename of content
the orchestrator and reviewer agents depend on.

No LLM is invoked. Skill-quality evaluation (does the prompt actually surface
useful findings against real boards) lives in a separate task owned by RJ —
see `eval-dashboard-review-skills-quality-with-llm-judge-harness.md`.
"""

from __future__ import annotations

import pytest

from dbt_charts.agent_api.skills import get_skill

REVIEW_SKILLS = (
    "board-structural-review",
    "board-visual-review",
    "board-review",
)

SEVERITY_TAGS = ("blocker", "warning", "nit")


def _rendered_body(name: str, surface: str = "cli") -> str:
    """Return the surface-rendered body for assertions.

    Default is the CLI surface because these tests originally asserted
    `dct render` literals; that vocabulary still has
    to survive on the CLI side after the macro migration. Tool-call
    assertions explicitly pass ``surface="tool"``.
    """
    return get_skill(name, surface=surface).body  # type: ignore[arg-type]


@pytest.fixture(scope="class")
def body(request: pytest.FixtureRequest) -> str:
    assert request.cls is not None
    return _rendered_body(request.cls.SKILL)


@pytest.fixture(scope="class")
def tool_body(request: pytest.FixtureRequest) -> str:
    assert request.cls is not None
    return _rendered_body(request.cls.SKILL, surface="tool")


@pytest.mark.parametrize("name", REVIEW_SKILLS)
class TestReviewSkillsParseAsValidSkills:
    """Each review skill is a valid Skill per dbt_charts.agent_api.skills.

    Description *length* is capped in one place for both skill corpora —
    ``.claude/skills/skill-creator/tests/test_description_budget.py``.
    """

    def test_description_has_negative_boundary(self, name: str) -> None:
        skill = get_skill(name)
        assert "Do NOT use" in skill.description, (
            f"{name}: description must include a 'Do NOT use' boundary"
        )

    def test_body_contains_all_severity_tags(self, name: str) -> None:
        body = _rendered_body(name)
        for tag in SEVERITY_TAGS:
            assert tag in body, f"{name}: missing severity tag '{tag}'"


class TestStructuralReviewChecklist:
    """board-structural-review must enumerate every gateway anti-pattern.

    These strings are the regression contract — if a checklist item disappears,
    the skill stops catching that anti-pattern. Each assertion below
    corresponds to a real failure mode we want the skill to surface.
    """

    SKILL = "board-structural-review"

    def test_kpi_one_row_anti_pattern(self, body: str) -> None:
        assert "exactly 1 row" in body or "1 row" in body

    def test_single_bar_anti_pattern(self, body: str) -> None:
        assert "single-bar" in body

    def test_pie_segment_cap_anti_pattern(self, body: str) -> None:
        assert "pie" in body.lower()
        assert "segments" in body or "segment" in body

    def test_chart_data_shape_section(self, body: str) -> None:
        assert "Chart-data shape" in body or "chart-data shape" in body.lower()

    def test_descriptive_metadata_section(self, body: str) -> None:
        assert "Descriptive metadata" in body
        assert "notes:" not in body

    def test_layout_intent_section(self, body: str) -> None:
        assert "Layout intent" in body or "layout intent" in body.lower()

    def test_variables_section(self, body: str) -> None:
        assert "variables" in body.lower()

    def test_output_format_section(self, body: str) -> None:
        assert "Output format" in body or "output format" in body.lower()

    def test_no_findings_short_circuit(self, body: str) -> None:
        assert "No findings" in body


class TestVisualReviewChecklist:
    """board-visual-review must render-then-evaluate and cover the
    visual failure modes that YAML inspection can't see."""

    SKILL = "board-visual-review"

    def test_renders_to_png_first_on_cli(self, body: str) -> None:
        assert "dct render" in body
        assert "--format png" in body
        assert "uv run dct render" not in body, (
            "shipped skill must use bare `dct`; in-repo guidance lives in AGENTS.md"
        )

    def test_renders_to_png_first_on_tool_surface(self, tool_body: str) -> None:
        """Tool-rendered body must use the function-call name, not the CLI command."""
        assert "render_board" in tool_body
        assert "dct render" not in tool_body

    def test_visual_hierarchy_section(self, body: str) -> None:
        assert "visual hierarchy" in body.lower()

    def test_text_legibility_section(self, body: str) -> None:
        assert "legibility" in body.lower() or "overflow" in body.lower()

    def test_contrast_section(self, body: str) -> None:
        assert "contrast" in body.lower()

    def test_kpi_precision_anti_pattern(self, body: str) -> None:
        assert "precision" in body.lower() or "format" in body.lower()

    def test_composition_section(self, body: str) -> None:
        assert "composition" in body.lower() or "balance" in body.lower()

    def test_output_format_matches_structural(self, body: str) -> None:
        assert "**Findings**" in body
        assert "No findings" in body


class TestOrchestratorProtocol:
    """board-review orchestrator must name both leaf skills, run
    structural first, and synthesize findings."""

    SKILL = "board-review"

    def test_names_structural_skill(self, body: str) -> None:
        assert "board-structural-review" in body

    def test_names_visual_skill(self, body: str) -> None:
        assert "board-visual-review" in body

    def test_structural_runs_first(self, body: str) -> None:
        structural_pos = body.index("board-structural-review")
        visual_pos = body.index("board-visual-review")
        assert structural_pos < visual_pos, (
            "Orchestrator must name structural before visual to convey order"
        )

    def test_synthesis_dedupes(self, body: str) -> None:
        assert "Dedupe" in body or "dedup" in body.lower()

    def test_synthesis_orders_by_severity(self, body: str) -> None:
        assert "severity" in body.lower()

    def test_default_cap_documented(self, body: str) -> None:
        assert "Cap at 5" in body or "default 5" in body or "top 5" in body.lower()

    def test_tags_pass_source_in_output(self, body: str) -> None:
        assert "[structural]" in body and "[visual]" in body


class TestReviewSkillsDiscoverable:
    """The three new skills are visible to the registry walk that powers
    `dct skills` and the shared list_skills tool."""

    def test_all_three_load_via_registry(self) -> None:
        from dbt_charts.agent_api.skills import list_skills

        names = {s.name for s in list_skills().skills}
        for name in REVIEW_SKILLS:
            assert name in names, f"Registry missing {name}; got {sorted(names)}"
