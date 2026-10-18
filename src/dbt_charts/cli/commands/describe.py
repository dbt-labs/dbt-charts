"""describe command — thin wrapper over dbt_charts.agent_api.describe."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.table import Table

from dbt_charts.cli._console import dct_console
from dbt_charts.cli._error_format import print_diagnostics
from dbt_charts.cli._json_output import print_json_result
from dbt_charts.cli._project import with_project
from dbt_charts.cli.filesystem_project import FilesystemProject

if TYPE_CHECKING:
    from dbt_charts.agent_api.describe import (
        DescribeBoardResult,
        ProjectOverview,
        TemplateDescription,
    )

console = dct_console()


@with_project
def describe_command(
    paths: list[Path],
    *,
    project: FilesystemProject,
    all_charts: bool = False,
    chart: str | None = None,
    json_output: bool = False,
) -> None:
    """Describe the project (no paths), or the structure of board files or one chart."""
    from dbt_charts.agent_api import ProjectSession
    from dbt_charts.agent_api.describe import describe_project

    if not paths:
        overview = describe_project(project, include_charts=all_charts)
        if json_output:
            print_json_result(overview)
        else:
            _print_overview(overview, project.root)
        return

    with ProjectSession.from_project(project) as project_session:
        if chart is None:
            results = project_session.describe_paths(paths)
        else:
            (path,) = paths
            results = [project_session.describe_chart(path, chart)]
    _emit(results, json_output=json_output)


def _print_overview(overview: ProjectOverview, root: Path) -> None:
    # Plain echo, not rich: paths and SQL names must not wrap at 80 columns or
    # be read as markup, and the reader is usually an agent.
    echo = typer.echo
    echo(f"Project: {root}")
    echo(f"Config: {overview.config_file or 'none'}")
    if overview.sources_error:
        echo(f"Sources unreadable: {overview.sources_error}")
    if overview.configured_sources:
        names = ", ".join(s.name for s in overview.configured_sources)
        echo(f"\nSources (write the name as `source:`): {names}")
    if overview.data_files:
        echo(
            "\nData files (write the path as `source:`): "
            + ", ".join(overview.data_files)
        )
    for rel, block in overview.database_files.items():
        echo(f"\nDatabase file {rel}; declare it in dbt_charts.yml:\n{block.rstrip()}")
    echo(f"\nBoards ({len(overview.boards)}):")
    for b in overview.boards:
        echo(f"  {b.path}  {b.url}  {b.title or '(does not compile)'}")
        for c in b.charts:
            query = "(inline)" if c.inline else c.query
            parts = [c.name, f"type={c.type}"]
            if query:
                parts.append(f"query={query}")
            if c.title:
                parts.append(f"title={c.title}")
            echo("    " + "  ".join(parts))
    if not overview.tables:
        echo("\nTables read: none")
    else:
        echo("\nTables read:")
    for t in overview.tables:
        echo(f"  {t.table}: {', '.join(t.boards)}")
    if overview.incomplete:
        echo("\nIncomplete, so the table list may miss reads from:")
        for i in overview.incomplete:
            echo(f"  {i.board}: {i.reason}")


def _print_template(path: str, template: TemplateDescription) -> None:
    console.print(f"\n[bold]{template.name}[/bold] [dim]({path})[/dim]")
    if template.description:
        console.print(template.description)
    console.print()
    if template.channels:
        console.print("[bold]Channels[/bold]")
        t: Any = Table(
            show_header=True, header_style="bold", box=None, padding=(0, 2, 0, 0)
        )
        t.add_column("Name")
        t.add_column("Required")
        t.add_column("Description")
        for c in template.channels:
            need = "required" if c.required else "optional"
            t.add_row(
                c.name,
                f"{need}, multiple" if c.multiple else need,
                c.description,
            )
        console.print(t)
        console.print()
    if template.options:
        console.print("[bold]Options[/bold]")
        t = Table(show_header=True, header_style="bold", box=None, padding=(0, 2, 0, 0))
        t.add_column("Name")
        t.add_column("Type")
        t.add_column("Default")
        t.add_column("Description")
        for o in template.options:
            t.add_row(
                o.name,
                o.type,
                "required" if o.required else json.dumps(o.default),
                o.description,
            )
        console.print(t)
        console.print()


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

        if r.template:
            _print_template(r.path, r.template)
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
            t.add_column("Template")
            for c in r.charts:
                enc = ", ".join(f"{k}={v}" for k, v in c.encoding.items())
                name = "inline" if c.inline_template else c.template
                template = ": ".join(filter(None, [name, c.template_description]))
                t.add_row(c.name, c.type, c.query, enc, template)
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
