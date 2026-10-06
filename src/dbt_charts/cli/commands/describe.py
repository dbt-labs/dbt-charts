"""describe command — thin wrapper over dbt_charts.agent_api.describe."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.table import Table

from dbt_charts.agent_api import Project
from dbt_charts.cli._console import dct_console
from dbt_charts.cli._error_format import print_diagnostics
from dbt_charts.cli._project import with_project

if TYPE_CHECKING:
    from dbt_charts.agent_api.describe import DescribeBoardResult

console = dct_console()


@with_project
def describe_command(
    paths: list[Path], *, project: Project, json_output: bool = False
) -> None:
    """Describe the structure of one or more board files."""
    from dbt_charts.agent_api import ProjectSession

    with ProjectSession.from_project(project) as project_session:
        results = project_session.describe_paths(paths)
    _emit(results, json_output=json_output)


def _emit(results: list[DescribeBoardResult], *, json_output: bool) -> None:
    if json_output:
        if len(results) == 1:
            typer.echo(results[0].model_dump_json(exclude_none=True, indent=2))
        else:
            typer.echo(
                json.dumps(
                    [r.model_dump(mode="json", exclude_none=True) for r in results],
                    indent=2,
                )
            )
        has_errors = any(r.errors for r in results)
        raise typer.Exit(0 if not has_errors else 1)

    has_errors = False
    for r in results:
        if len(results) > 1:
            console.rule(str(r.path))
        if r.errors:
            has_errors = True
            print_diagnostics(r.errors)
            continue

        if r.title:
            console.print(f"\n[bold]{r.title}[/bold]")
        if r.notes:
            console.print(f"[dim]{r.notes}[/dim]")
        console.print()

        if r.queries:
            console.print("[bold]Queries[/bold]")
            t: Any = Table(
                show_header=True, header_style="bold", box=None, padding=(0, 2, 0, 0)
            )
            t.add_column("Name")
            t.add_column("Type")
            t.add_column("Summary")
            for q in r.queries:
                t.add_row(q.name, q.type, q.summary)
            console.print(t)
            console.print()

        if r.charts:
            console.print("[bold]Charts[/bold]")
            t = Table(
                show_header=True, header_style="bold", box=None, padding=(0, 2, 0, 0)
            )
            t.add_column("Name")
            t.add_column("Type")
            t.add_column("Query")
            t.add_column("Encoding")
            for c in r.charts:
                enc = ", ".join(f"{k}={v}" for k, v in c.encoding.items())
                t.add_row(c.name, c.type, c.query, enc)
            console.print(t)
            console.print()

        if r.variables:
            console.print("[bold]Variables[/bold]")
            t = Table(
                show_header=True, header_style="bold", box=None, padding=(0, 2, 0, 0)
            )
            t.add_column("Name")
            t.add_column("Type")
            t.add_column("Default")
            for v in r.variables:
                t.add_row(
                    v.name, v.type, str(v.default) if v.default is not None else ""
                )
            console.print(t)
            console.print()

        if r.layout:
            console.print("[bold]Layout[/bold]")
            console.print(f"  {r.layout.primitive}: {', '.join(r.layout.items)}")
            console.print()

    if has_errors:
        raise typer.Exit(1)
