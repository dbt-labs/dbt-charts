"""validate command — thin wrapper over dbt_charts.agent_api.validate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from dbt_charts.agent_api import Project
from dbt_charts.cli._console import dct_console
from dbt_charts.cli._error_format import print_diagnostics
from dbt_charts.cli._project import with_project

if TYPE_CHECKING:
    from dbt_charts.agent_api.validate import ValidateResult

_console = dct_console()


@with_project
def validate_command(
    paths: list[Path] | None,
    *,
    project: Project,
    json_output: bool = False,
    strict: bool = False,
    warehouse: bool = False,
) -> None:
    from dbt_charts.agent_api import ProjectSession

    with ProjectSession.from_project(project) as project_session:
        results = project_session.validate_paths(paths, warehouse=warehouse)
    _emit(results, json_output=json_output, strict=strict)


def _emit(
    results: list[ValidateResult],
    *,
    json_output: bool,
    strict: bool,
) -> None:
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
        has_warnings = any(r.warnings for r in results)
        # Must match the plain-text branch below: --strict means warnings are
        # errors, and --json must not silently drop that contract just
        # because it takes a different code path to render the result.
        raise typer.Exit(1 if has_errors or (strict and has_warnings) else 0)

    has_errors = False
    has_warnings = False
    for r in results:
        if r.errors:
            has_errors = True
            print_diagnostics(r.errors, path=r.path)
        if r.warnings:
            has_warnings = True
            print_diagnostics(r.warnings, path=r.path)
        if not r.errors and not r.warnings:
            _console.print(f"✓ {r.path} OK")

    if has_errors:
        raise typer.Exit(1)
    if strict and has_warnings:
        raise typer.Exit(1)
