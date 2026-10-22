"""exposures command — thin wrapper over dbt_charts.agent_api.exposures."""

from __future__ import annotations

import typer
from rich.markup import escape

from dbt_charts.agent_api import Project
from dbt_charts.cli._console import dct_console
from dbt_charts.cli._json_output import print_json_result
from dbt_charts.cli._project import with_project

_console = dct_console()
_err_console = dct_console(stderr=True)


@with_project
def exposures_command(
    *,
    project: Project,
    check: bool = False,
    json_output: bool = False,
) -> None:
    from dbt_charts.agent_api.exposures import ExposuresError, emit_exposures

    try:
        result = emit_exposures(project, check)
    except ExposuresError as exc:
        _err_console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc

    if json_output:
        print_json_result(result)
    else:
        for skipped in result.skipped:
            _err_console.print(
                f"skipped {escape(skipped.board)}: {escape(skipped.reason)}"
            )
        if check:
            state = "is stale" if result.changed else "is up to date"
            _console.print(f"{escape(result.path)} {state}")
        else:
            state = "wrote" if result.changed else "unchanged"
            _console.print(
                f"{state} {escape(result.path)} ({len(result.exposures)} exposures)"
            )
    if check and result.changed:
        raise typer.Exit(1)
