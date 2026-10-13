"""CLI command for `dct search`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from dbt_charts.agent_api import Project
from dbt_charts.cli._error_format import print_diagnostics
from dbt_charts.cli._project import with_project

if TYPE_CHECKING:
    from dbt_charts.agent_api.search import SearchResult


@with_project
def search_command(
    query: str,
    *,
    project: Project,
    json_output: bool = False,
    limit: int = 10,
) -> None:
    """Search dashboards by keyword with ranked results."""
    from dbt_charts.agent_api import ProjectSession

    with ProjectSession.from_project(project) as project_session:
        result = project_session.search_boards(query, limit=limit)

    if json_output:
        typer.echo(result.model_dump_json(indent=2, exclude_none=True))
        if not result.success:
            raise typer.Exit(1)
        return

    _print_rich(result)
    if not result.success:
        raise typer.Exit(1)


def _print_rich(result: SearchResult) -> None:
    if not result.success:
        print_diagnostics(result.errors)
        return

    if not result.results:
        typer.echo("No results found.")
        return

    for hit in result.results:
        typer.echo(f"  [{hit.match_score:.2f}] {hit.title}  {hit.file_path}")
        if hit.summary:
            typer.echo(f"           {hit.summary}")
        charts = {c.id: c for c in hit.charts}
        for chart_id in hit.matched_charts:
            chart = charts[chart_id]
            kind = f" ({chart.type})" if chart.type else ""
            typer.echo(f"           chart {chart_id}{kind}: {chart.title}")
