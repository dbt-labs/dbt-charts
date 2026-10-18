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
    text: str | None,
    *,
    project: Project,
    kind: str = "boards",
    path: str | None = None,
    json_output: bool = False,
    limit: int = 10,
) -> None:
    """Search the project's boards, or its meta, model, and config files."""
    from pydantic import ValidationError

    from dbt_charts.agent_api.argument_errors import format_argument_error
    from dbt_charts.agent_api.search import SearchFilesArgs, search_files

    try:
        args = SearchFilesArgs.model_validate(
            {"kind": kind, "path": path, "text": text}
        )
    except ValidationError as exc:
        typer.echo(format_argument_error("search", exc), err=True)
        raise typer.Exit(2) from exc
    result = search_files(
        project, kind=args.kind, path=args.path, text=args.text, limit=limit
    )

    if json_output:
        typer.echo(result.model_dump_json(indent=2, exclude_none=True))
        if not result.success:
            raise typer.Exit(1)
        return

    _print_rich(result)
    if not result.success:
        raise typer.Exit(1)


def _print_rich(result: SearchResult) -> None:
    from dbt_charts.agent_api.search import BoardListing, BoardMatch, FileHit

    if not result.success:
        print_diagnostics(result.errors)
        return

    if not result.results:
        typer.echo("No results found.")
        return

    for hit in result.results:
        if isinstance(hit, FileHit):
            typer.echo(f"  {hit.file_path}:{hit.line}: {hit.text}")
            continue
        if not isinstance(hit, BoardListing):
            typer.echo(f"  {hit.file_path}")
            continue
        score = f"[{hit.match_score:.2f}] " if isinstance(hit, BoardMatch) else ""
        typer.echo(f"  {score}{hit.title}  {hit.file_path}")
        if hit.summary:
            typer.echo(f"           {hit.summary}")
        if isinstance(hit, BoardMatch):
            for chart_id in hit.matched_charts:
                typer.echo(f"           chart {chart_id}")
