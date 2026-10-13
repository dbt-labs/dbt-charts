"""artifact emit / render / schema commands — thin wrappers over dbt_charts.agent_api.board_artifact."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from dbt_charts.agent_api import Project
from dbt_charts.cli._console import dct_console
from dbt_charts.cli._error_format import print_diagnostics
from dbt_charts.cli._parsing import parse_kv_pairs
from dbt_charts.cli._project import with_project

console = dct_console()


@with_project
def emit_board_command(
    board_path: Path,
    *,
    project: Project,
    artifact: Path | None = None,
    recording: Path | None = None,
    var: list[str] | None = None,
    no_cache: bool = False,
    json_output: bool = False,
) -> None:
    """Compile, resolve, and execute a board; write its board artifact + recording."""
    from dbt_charts.agent_api import ProjectSession

    boards_dir = board_path.parent / "boards"
    artifact_path = artifact or boards_dir / f"{board_path.stem}.board.json"
    recording_path = recording or boards_dir / f"{board_path.stem}.recording.json"
    variables = parse_kv_pairs(var or [], "--var")

    with ProjectSession.from_project(
        project, allow_mutating_methods=True
    ) as project_session:
        result = project_session.emit_board(
            board_path,
            artifact_path,
            recording_path,
            variables=variables or None,
            use_cache=not no_cache,
        )

    if json_output:
        typer.echo(result.model_dump_json(exclude_none=True, indent=2))
        raise typer.Exit(0 if result.success else 1)

    if not result.success:
        print_diagnostics(result.errors)
        raise typer.Exit(1)

    query_label = "query" if result.query_count == 1 else "queries"
    console.print(f"Wrote board artifact: {result.artifact_path}")
    console.print(
        f"Wrote board recording: {result.recording_path} "
        f"({result.query_count} {query_label})"
    )


def render_board_command(
    artifact_path: Path,
    recording_path: Path,
    *,
    output: Path | None = None,
    json_output: bool = False,
) -> None:
    """Render a board from its artifact + recording — no compile, no warehouse."""
    from dbt_charts.agent_api.board_artifact import render_board_from_files

    # An artifact rendered here lands in a file nothing serves, so it carries its
    # fonts — the same reason `dct render` does, and what keeps the two identical.
    result = render_board_from_files(artifact_path, recording_path, standalone=True)

    if json_output:
        typer.echo(result.model_dump_json(exclude_none=True, indent=2))
        raise typer.Exit(0 if result.success else 1)

    if not result.success:
        print_diagnostics(result.errors)
        raise typer.Exit(1)

    assert result.svg is not None  # result.success guarantees this
    if output is None:
        typer.echo(result.svg)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result.svg, encoding="utf-8")
    console.print(f"Rendered {artifact_path} to {output}")


def schema_command() -> None:
    """Print the JSON Schema that board artifacts conform to."""
    from dbt_charts.agent_api.board_artifact import board_artifact_schema

    typer.echo(json.dumps(board_artifact_schema(), indent=2))
