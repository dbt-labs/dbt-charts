"""Skills command — thin wrapper over dbt_charts.agent_api.skills."""

from __future__ import annotations

import sys

import typer
from rich.padding import Padding
from rich.table import Table

from dbt_charts.agent_api import skills as _api
from dbt_charts.cli._console import dct_console
from dbt_charts.cli._json_output import print_json_result


def skills_command(
    name: str | None = None,
    as_json: bool = False,
) -> None:
    """List packaged agent skills, or show one by name."""
    if name is None:
        listing = _api.list_skills(surface="cli")
        if as_json:
            print_json_result(listing)
            return
        _print_skills_table(listing)
        return

    try:
        skill = _api.get_skill(name, surface="cli")
    except _api.SkillNotFound as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    if as_json:
        print_json_result(skill)
    else:
        print(skill.body)


def _print_skills_table(result: _api.SkillList) -> None:
    if not result.skills:
        typer.echo("No skills available.")
        return

    console = dct_console()

    workflows = [s for s in result.skills if s.kind == "workflow"]
    patterns = [s for s in result.skills if s.kind == "pattern"]

    typer.echo(
        "Agent skills — workflows and layout patterns for building dbt charts dashboards."
    )
    typer.echo("YAML field reference is `dct docs`, not skills.")
    typer.echo("Start here: `dct skills intro`.\n")

    name_width = max(len(s.name) for s in result.skills)

    if workflows:
        console.print("[bold]Workflows[/bold]")
        console.print(Padding(_skill_table(workflows, name_width), (0, 0, 0, 2)))
        console.print()

    if patterns:
        console.print("[bold]Patterns[/bold]")
        console.print(Padding(_skill_table(patterns, name_width), (0, 0, 0, 2)))
        console.print()

    typer.echo("Next steps")
    typer.echo(
        "  dct skills <name>  Read the full skill body and copy the example YAML"
    )
    typer.echo("  dct docs           YAML syntax reference")
    typer.echo("")
    typer.echo('YAML syntax:  dct docs -s "<query>"')


def _skill_table(skills: list[_api.Skill], name_width: int) -> Table:
    t = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    t.add_column("Name", style="bold", no_wrap=True, width=name_width)
    t.add_column("Description", overflow="fold")
    for skill in skills:
        suffix = r"  [dim]\[scaffold][/dim]" if skill.has_examples else ""
        t.add_row(skill.name, f"{_first_sentence(skill.description)}{suffix}")
    return t


def _first_sentence(text: str) -> str:
    """Collapse a YAML block-scalar description to a single sentence."""
    flat = " ".join(text.split())
    head, sep, _ = flat.partition(".")
    return head + sep if sep else flat
