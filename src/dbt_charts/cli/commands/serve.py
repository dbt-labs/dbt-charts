"""Serve command implementation."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer

from dbt_charts.agent_api import Diagnostic
from dbt_charts.cli._error_format import print_diagnostics
from dbt_charts.cli._project import with_project

if TYPE_CHECKING:
    from dbt_charts.cli.filesystem_project import FilesystemProject


def _count_noun(n: int, noun: str) -> str:
    return f"no {noun}s" if n == 0 else f"{n} {noun}{'' if n == 1 else 's'}"


def _detached(
    project: FilesystemProject,
    *,
    port: int | None,
    host: str,
    stop: bool,
    describe: bool,
    dialect: str | None,
    target: str | None,
    max_workers: int | None,
    no_cache: bool,
    cache_path: Path | None,
) -> None:
    from dbt_charts.agent_api.serve import (
        DetachedServeError,
        start_detached,
        stop_detached,
    )

    try:
        if stop:
            if port is None:
                raise DetachedServeError("--stop needs --port.")
            typer.echo(f"Stopped server (pid {stop_detached(port)}) on port {port}.")
            return
        server = start_detached(
            project,
            port=port,
            host=host,
            dialect=dialect,
            target=target,
            max_workers=max_workers,
            no_cache=no_cache,
            cache_path=cache_path,
            describe=describe,
        )
    except DetachedServeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None

    if server.replaced is not None:
        typer.echo(f"Replaced the server for {server.replaced} on port {server.port}.")
    for board in server.boards:
        if board.description is not None:
            typer.echo(board.description)
    print_diagnostics([d for b in server.boards for d in (*b.warnings, *b.errors)])
    for board in server.boards:
        status = (
            f"{_count_noun(len(board.errors), 'error')}, "
            f"{_count_noun(len(board.warnings), 'warning')}"
            if board.errors or board.warnings
            else "ok"
        )
        typer.echo(f"{board.url}  {status}")
    verb = "reusing" if server.reused else "started"
    typer.echo(
        f"Server {verb} (pid {server.pid}); stop with: "
        f"dct serve --stop --port {server.port}"
    )
    if any(board.errors for board in server.boards):
        typer.echo("The server is up; the boards above have errors.")
        raise typer.Exit(1)


@with_project
def serve_command(
    port: int | None = None,
    host: str = "localhost",
    *,
    project: FilesystemProject,
    dialect: str | None = None,
    target: str | None = None,
    max_workers: int | None = None,
    no_cache: bool = False,
    cache_path: Path | None = None,
    detach: bool = False,
    stop: bool = False,
    describe: bool = False,
) -> None:
    """Start unified dbt charts server.

    This command starts a FastAPI server that serves:
    - Any board file at /{path}/?var=value (path.yml → /path/)
    - Health check at /health

    Example URLs:
        http://localhost:9876/
        http://localhost:9876/sales/?region=West

    Args:
        port: Port number to serve on (auto-resolved if not specified)
        host: Host address to bind to
        project: The resolved dbt charts project (injected by @with_project)
        dialect: SQL dialect (auto-detected from dbt profile, or duckdb)
        target: dbt target name (defaults to DBT_TARGET env var, then profile default)
        no_cache: Skip the query-result cache entirely.
        detach: Serve in a background process, check every board, print each
            board's URL and status, and return; the server keeps running.
        stop: Stop the server ``--detach`` left on ``port``.
        describe: With ``detach``, also print what each chart draws.
        cache_path: Persist the query-result cache to this DuckDB file (created
            if absent). None (the default) falls through to the project's
            cache: block, then to the per-user file.

    Returns:
        None (exits with code 0 on success, 1 on errors, 3 when the server
        never started, which is uvicorn's own STARTUP_FAILURE code)
    """
    from dbt_charts.agent_api import set_surface
    from dbt_charts.agent_api.serve import format_startup_failure, prepare_serve

    if stop or detach:
        _detached(
            project,
            port=port,
            host=host,
            stop=stop,
            describe=describe,
            dialect=dialect,
            target=target,
            max_workers=max_workers,
            no_cache=no_cache,
            cache_path=cache_path,
        )
        return

    # Narrows the root callback's "cli": a served dashboard's queries are a
    # different cost story from a one-shot `dct render`.
    set_surface("serve")

    # Validate DCT_DEFAULT_THEME/dbt_charts.yml, resolve port/dialect, and build the
    # ASGI app — a dbt_charts.yml validation failure raises pydantic's ValidationError
    # uncaught (matches pre-existing behavior: a plain echo, not the Diagnostic
    # envelope used everywhere else in this command).
    from pydantic import ValidationError as _PydanticError

    try:
        setup = prepare_serve(
            project,
            port=port,
            host=host,
            dialect=dialect,
            target=target,
            max_workers=max_workers,
            no_cache=no_cache,
            cache_path=cache_path,
        )
    except _PydanticError as e:
        typer.echo(f"dbt_charts.yml validation error: {e}", err=True)
        raise typer.Exit(1) from None

    if isinstance(setup, Diagnostic):
        print_diagnostics([setup])
        raise typer.Exit(1) from None

    if setup.dialect_inferred:
        typer.echo(f"  Auto-detected dialect: {setup.dialect} (from dbt profile)")

    port = setup.port

    # Start server. The URL is the whole banner: it lists every board in the
    # project, so any other route printed here is a second way to say the same
    # thing — or, once a route is renamed, a link that 404s.
    typer.echo("📈 Starting dashboard server")
    typer.echo(f"   URL: http://{host}:{port}")
    typer.echo("")
    typer.echo("   Press CTRL+C to stop")

    try:
        setup.server.run()
    except KeyboardInterrupt:
        typer.echo("\n👋 Server stopped")
        raise typer.Exit(0) from None
    except Exception as e:  # noqa: BLE001 — uvicorn surface
        print_diagnostics([format_startup_failure(e)])
        raise typer.Exit(1) from None

    # A lifespan startup exception leaves `started` false and returns without
    # raising, so nothing above catches it.
    if not setup.server.started:
        raise typer.Exit(3)
