"""The packaged chart/styling/theme pages are generated; this keeps them so.

`scripts/gen_skill_pages.py` renders each page from a template plus the engine's
own definitions. A page edited by hand, a model that changed under it, or an
allowlist name the engine no longer has fails here.
"""

from __future__ import annotations

import importlib.util
from types import ModuleType
from typing import Literal

import pytest

from ..._paths import DBT_CHARTS_DIR

_PAGES_BUDGET_BYTES = 39 * 1024
_MIN_GENERATED_SHARE = 0.72


@pytest.fixture(scope="module")
def gen() -> ModuleType:
    path = DBT_CHARTS_DIR / "scripts" / "gen_skill_pages.py"
    spec = importlib.util.spec_from_file_location("gen_skill_pages", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_pages_match_generator_output(gen: ModuleType) -> None:
    for name, text in gen.render_pages().items():
        committed = (gen.SKILLS_DIR / name / "SKILL.md").read_text()
        assert committed == text, (
            f"skills/{name}/SKILL.md is out of sync with the engine. "
            "Run: uv run python dbt-charts/scripts/gen_skill_pages.py"
        )


def test_every_allowlisted_name_exists_in_the_engine(gen: ModuleType) -> None:
    generator = gen.Generator(mark=False)
    for name, spec in generator.cfg["objects"].items():
        model, _ = gen._resolve(spec["path"])
        for key in [*spec["keys"], *spec.get("detail", [])]:
            gen._field(model, key, f"objects.{name}")


def test_unknown_allowlist_key_fails_generation(gen: ModuleType) -> None:
    generator = gen.Generator(mark=False)
    generator.cfg["objects"]["board"]["keys"].append("no_such_key")
    with pytest.raises(KeyError, match="no_such_key"):
        generator.keys("board")


def test_unknown_command_form_fails_generation(gen: ModuleType) -> None:
    generator = gen.Generator(mark=False)
    generator.cfg["commands"] = [
        {"cmd": "serve", "flags": ["--no-such-flag"], "option": "--detach"}
    ]
    with pytest.raises(KeyError, match="no-such-flag"):
        generator.commands()


def test_pages_fit_the_budget_and_are_mostly_generated(gen: ModuleType) -> None:
    pages = gen.render_pages(mark=True)
    share = gen.generated_share(pages)
    total = sum(size for _, size in share.values())
    generated = sum(made for made, _ in share.values())
    assert total <= _PAGES_BUDGET_BYTES, f"pages are {total} bytes"
    assert generated / total >= _MIN_GENERATED_SHARE, f"{generated}/{total} generated"


@pytest.mark.parametrize("name", ["chart", "styling"])
def test_pages_serve_both_surfaces(name: str) -> None:
    from dbt_charts.agent_api.skills import get_skill

    tool = get_skill(name, surface="tool")
    cli = get_skill(name, surface="cli")
    for skill in (tool, cli):
        assert "{{ s_" not in skill.body
        assert "{{#if_surface" not in skill.body
    assert "dct " not in tool.body
    assert "dct " not in tool.description
    assert "render_board" in tool.body
    assert "dct serve --detach" in cli.body


def test_chart_page_teaches_serve_detach_and_custom_charts() -> None:
    from dbt_charts.agent_api.skills import get_skill

    assert "dct serve --detach" in get_skill("chart", surface="cli").body
    for surface in ("tool", "cli"):
        assert "custom-chart-build" in get_skill("chart", surface=surface).body


def test_tool_surface_keeps_boards_only_guidance() -> None:
    from dbt_charts.agent_api.skills import get_skill

    only_viz_tool = "dbt Charts is the only visualization tool here"
    assert only_viz_tool in get_skill("chart", surface="tool").body
    assert only_viz_tool not in get_skill("chart", surface="cli").body


def test_file_tool_hosts_copy_by_writing_and_change_only_what_was_asked() -> None:
    from dbt_charts.agent_api.skills import get_skill

    with_files = get_skill(
        "chart", surface="tool", available_tools={"render_board", "write_file"}
    ).body
    without_files = get_skill(
        "chart", surface="tool", available_tools={"render_board"}
    ).body
    assert "write the copy" in with_files
    assert "only what the user explicitly asked" in with_files
    assert "write the copy" not in without_files
    assert "write the copy" not in get_skill("chart", surface="cli").body


@pytest.mark.parametrize("surface", ["tool", "cli"])
def test_styling_page_pins_a_color_request_to_a_hue_alias(
    surface: Literal["tool", "cli"],
) -> None:
    from dbt_charts.agent_api.skills import get_skill

    body = " ".join(get_skill("styling", surface=surface).body.split())
    assert "pins `category.<hue>` under `style.charts.category_colors`" in body
    assert "never a raw hex" in body
    assert "name the nearest alias it does have" in body


def test_tool_names_the_pages_teach_exist_in_the_tool_registry() -> None:
    import re

    from dbt_charts.agent_api.skills import get_skill
    from dbt_charts.ai import tool_schemas

    served = {t["name"] for t in [*tool_schemas.ALL_TOOLS, *tool_schemas.FILE_TOOLS]}
    chart = get_skill("chart", surface="tool").body
    styling = get_skill("styling", surface="tool").body
    # Prose before the first reference table, the tool table, the Finish loop.
    taught = [
        chart[: chart.index("## Board")],
        chart[chart.index("## Finish") :],
        styling[styling.index("After any edit") :],
    ]
    names = {n for text in taught for n in re.findall(r"`([a-z]+(?:_[a-z]+)+)\(", text)}
    assert {"render_board", "execute_sql", "search_files", "get_skill"} <= names
    assert names <= served, f"pages teach tools the registry lacks: {names - served}"
