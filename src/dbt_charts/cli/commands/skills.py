"""Skills command — thin wrapper over dbt_charts.agent_api.skills."""

from __future__ import annotations

import sys

import typer
from rich.padding import Padding
from rich.table import Table

from dbt_charts.agent_api import skills as _api
from dbt_charts.cli._console import dct_console
from dbt_charts.cli._json_output import print_json_result

err_console = dct_console(stderr=True)


def skills_command(
    name: str | None = None,
    search: str | None = None,
    limit: int = 10,
    as_json: bool = False,
) -> None:
    """List packaged agent skills, search them, or show one by name — selected from the input args."""
    if name is not None and search is not None:
        err_console.print(
            "[red]Error:[/red] cannot combine a skill name with --search; use one or the other."
        )
        raise typer.Exit(1)

    if search is not None:
        result = _api.search_skills(search, limit=limit, surface="cli")
        if as_json:
            print_json_result(result)
            return
        _print_search_results(result)
        return

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
    typer.echo('Find a skill: dct skills -s "<query>"')
    typer.echo('YAML syntax:  dct docs -s "<query>"')


def _skill_table(skills: list[_api.Skill], name_width: int) -> Table:
    t = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    t.add_column("Name", style="bold", no_wrap=True, width=name_width)
    t.add_column("Description", overflow="fold")
    for skill in skills:
        suffix = r"  [dim]\[scaffold][/dim]" if skill.has_examples else ""
        t.add_row(skill.name, f"{_first_sentence(skill.description)}{suffix}")
    return t


def _print_search_results(result: _api.SkillSearchResult) -> None:
    if not result.hits:
        typer.echo(f'Agent skills — search results for "{result.query}"\n')
        typer.echo(
            'No skills matched. Try `dct skills -s "kpi"` or `dct skills` (full list).'
        )
        return

    console = dct_console()
    typer.echo(f'Agent skills — search results for "{result.query}"\n')
    for hit in result.hits:
        scaffold = r" \[scaffold]" if hit.has_examples else ""
        console.print(
            f"  [bold]{hit.name}[/bold] \\[{hit.kind}]{scaffold}  "
            f"[dim]score {hit.score:.2f}[/dim]"
        )
        console.print(f"    {_first_sentence(hit.description)}")
    typer.echo("")
    typer.echo(
        f"{len(result.hits)} matches. "
        "Read one: `dct skills <name>` — copy the example YAML directly."
    )


def _first_sentence(text: str) -> str:
    """Collapse a YAML block-scalar description to a single sentence."""
    flat = " ".join(text.split())
    head, sep, _ = flat.partition(".")
    return head + sep if sep else flat
