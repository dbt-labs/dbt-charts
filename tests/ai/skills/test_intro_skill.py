"""Content-regression tests for the `intro` orientation skill.

`dct skills intro` is the first thing an agent that has never met dbt charts
reads. The body carries the install line, disambiguates the product, and
holds the board basics itself — the shape, fields, formats, and how to check
a render without pictures — so a typical board needs no other skill, and
the page names none to read. Deterministic: reads the rendered body, no LLM.
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


def test_does_not_route_a_first_build_to_other_skills(body: str) -> None:
    """Every first build used to read the skills its conditions matched.

    The page holds all a typical board needs, so it names no skill to read.
    Only the Done section's cloud-setup mention (publishing) may remain.
    """
    assert "Other skills" not in body
    assert "`dct skills` lists every skill." not in body
    workflows = {
        s.name for s in list_skills(surface="cli").skills if s.kind == "workflow"
    }
    named = set(re.findall(r"`([a-z][a-z-]*[a-z])`", body)) & workflows
    assert named <= {"cloud-setup"}, f"routes to other skills: {named}"


def test_skill_install_is_named_and_tied_to_a_git_repository(body: str) -> None:
    section = body[
        body.index("## 4. Install the skills") : body.index("## Done looks like")
    ]
    assert "dct skills <name>" in section
    assert "dct init skills claude" in section
    assert "dct init skills agents" in section
    assert "git repository" in section.lower()


def test_covers_the_three_places_data_lives(body: str) -> None:
    assert "type: values" in body
    assert "type: csv" in body
    assert "dbt_charts.yml" in body
