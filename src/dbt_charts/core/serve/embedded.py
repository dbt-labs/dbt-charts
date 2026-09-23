"""Lifecycle for the embedded preview HTTP server.

`dct mcp serve` runs a uvicorn-served FastAPI app alongside its stdio
transport so the render_dashboard URLs the model emits are clickable.
This module owns the parts that are not MCP-specific — port resolution,
the uvicorn availability probe, and `uvicorn.Config` / `uvicorn.Server`
construction.

It lives in `core/` because it composes `core.serve` internals and belongs
with them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dbt_charts._install_hint import install_hint
from dbt_charts.core.execute.adapters import LOCAL_AUTHORING_REGISTRY_KWARGS
from dbt_charts.core.serve.port import find_available_port
from dbt_charts.core.serve.server import create_server

if TYPE_CHECKING:
    from dbt_charts.cli.filesystem_project import FilesystemProject


def build_embedded_server(
    project: FilesystemProject,
    port_hint: int = 8765,
    *,
    host: str = "localhost",
    log_level: str = "warning",
    max_attempts: int = 10,
) -> tuple[Any, int]:
    """Resolve a free port and build a configured `uvicorn.Server`.

    Returns ``(server, resolved_port)``. Raises ``ImportError`` (with an
    install hint) when uvicorn isn't installed, or ``RuntimeError`` from
    ``find_available_port`` when no free port is available — callers
    surface those errors with their own context. Callers run the server
    on whatever asyncio loop fits their lifecycle (one-shot async task,
    background thread, etc.).
    """
    try:
        from dbt_charts.core.serve.shutdown import build_server  # noqa: PLC0415
    except ImportError as e:
        # shutdown.py imports uvicorn at module scope. uvicorn is a runtime
        # dependency, so this only fires on a broken install. Naming the `mcp`
        # extra here would be actively misleading — it ships `mcp` alone and
        # cannot supply uvicorn.
        raise ImportError(
            "Embedded preview server requires uvicorn, which ships as a "
            f"dbt charts runtime dependency. Reinstall dbt charts: {install_hint()}"
        ) from e

    resolved = find_available_port(port_hint, host=host, max_attempts=max_attempts)

    # The embedded preview backs `dct mcp serve`, whose primary
    # session opens with LOCAL_AUTHORING_REGISTRY_KWARGS (read-only + external
    # access on). Pass the same posture so the clickable preview agrees with the
    # tool-side render — standalone `dct serve` is the surface that tightens to
    # strict read-only, not these authoring surfaces.
    server = build_server(
        create_server(project, **LOCAL_AUTHORING_REGISTRY_KWARGS),
        host=host,
        port=resolved,
        log_level=log_level,
    )
    return server, resolved
