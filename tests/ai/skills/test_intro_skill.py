"""Content-regression tests for the `intro` orientation skill.

`dct skills intro` is the first thing an agent that has never met dbt charts
reads. The body carries the install line, disambiguates the product, and
holds the board basics itself — the shape, fields, formats, and how to check
a render without pictures — so a typical board needs no other skill. Every
other skill is named as optional reading, read only when its condition
matches. Deterministic: reads the rendered body, no LLM.
"""

from __future__ import annotations

import re

import pytest

from dbt_charts.agent_api.skills import SkillNotFound, get_skill, list_skills

SKILL = "intro"


@pytest.fixture(scope="module")
def body() -> str:
    return get_skill(SKILL, surface="cli").body


def test_skill_is_cli_only() -> None:
    assert SKILL in {s.name for s in list_skills(surface="cli").skills}
    with pytest.raises(SkillNotFound):
        get_skill(SKILL, surface="tool")


def test_description_triggers_on_the_marketing_sentence() -> None:
    description = get_skill(SKILL, surface="cli").description
    assert "make charts" in description.lower()
    assert "Do NOT use" in description


def test_names_the_install_line_and_the_version_check(body: str) -> None:
    assert "uv tool install dbt-charts" in body
    assert "dct --version" in body


def test_other_skills_table_names_only_real_cli_workflow_skills(body: str) -> None:
    workflows = {
        s.name for s in list_skills(surface="cli").skills if s.kind == "workflow"
    }
    table_start = body.index("## 5. Other skills")
    table_end = body.index("`dct skills` lists every skill.")
    table = body[table_start:table_end]
    named = set(re.findall(r"`([a-z][a-z-]*[a-z])`", table))
    assert named, "no skill names found in the Other skills table"
    assert named <= workflows, f"names skills that don't exist: {named - workflows}"


def test_names_dct_skills_as_the_full_list(body: str) -> None:
    assert "`dct skills` lists every skill." in body


def test_skill_install_is_named_and_tied_to_a_git_repository(body: str) -> None:
    section = body[body.index("## 4. Install the skills") : body.index("## 5.")]
    assert "dct skills <name>" in section
    assert "dct init skills claude" in section
    assert "dct init skills agents" in section
    assert "git repository" in section.lower()


def test_covers_the_three_places_data_lives(body: str) -> None:
    assert "type: values" in body
    assert "type: csv" in body
    assert "dbt_charts.yml" in body
