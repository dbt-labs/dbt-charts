"""MCP serve command implementation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from dbt_charts.cli._project import with_project

if TYPE_CHECKING:
    from dbt_charts.cli.filesystem_project import FilesystemProject


@with_project
def serve_command(*, project: FilesystemProject) -> None:
    """Start the MCP server for AI assistant integration.

    Args:
        project: The resolved dbt charts project (injected by @with_project)

    The ``mcp`` extras gate runs in the ``mcp serve`` CLI wrapper, before this
    body's project discovery — a missing-extra install hint must win over a
    "no project found" error when both apply.
    """
    import asyncio

    from dbt_charts.agent_api import set_surface
    from dbt_charts.agent_api.cache import project_cache_ctx

    # tach-ignore(cli->ai debt: route `dct mcp serve` via agent_api; burn-down task)
    from dbt_charts.ai.mcp import run_server

    # This command is the composition root: it owns FilesystemProject
    # construction and the cache lifecycle, and injects both into the AI
    # surface. Unlike serve/render this surface stays uncached unless
    # DCT_CACHE_PATH opts in: MCP is the authoring loop, an agent rebuilds a
    # model and re-queries it in the same process, and the cache key folds in
    # the query text and the file version but never warehouse table state — so
    # a cache here serves pre-rebuild numbers for the life of the process.
    cache_env = os.getenv("DCT_CACHE_PATH")

    set_surface("mcp")

    # Run the MCP server (stdio mode)
    # Note: We don't print anything to stdout as MCP uses it for communication
    try:
        with project_cache_ctx(
            project,
            no_cache=cache_env is None,
            cache_path=Path(cache_env) if cache_env else None,
        ) as cache:
            asyncio.run(run_server(project, cache))
    except KeyboardInterrupt:
        # Clean exit on Ctrl+C
        raise typer.Exit(0) from None
