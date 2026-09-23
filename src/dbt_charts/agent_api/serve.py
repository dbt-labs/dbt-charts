"""Typed startup composition for `dct serve`: validation, resolution, and app construction."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.diagnostics.base import DbtChartsError

if TYPE_CHECKING:
    from dbt_charts.cli.filesystem_project import FilesystemProject


class ServeSetup(BaseModel):
    """The resolved outcome of a successful `prepare_serve` call.

    `app` and `server` are `Any` rather than `FastAPI` / `uvicorn.Server`:
    `create_server` really does return a `FastAPI`, but the contract its callers
    need is duck-typed, and pinning either would reject the test doubles
    `dbt-charts/tests/cli/test_serve_cli.py` legitimately passes in place of a
    real server.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    app: Any
    server: Any
    port: int
    dialect: str
    dialect_inferred: bool


def prepare_serve(
    project: FilesystemProject,
    *,
    port: int | None,
    host: str,
    dialect: str | None,
    target: str | None,
    max_workers: int | None,
    no_cache: bool,
    cache_path: Path | None,
) -> ServeSetup | Diagnostic:
    """Validate startup config, resolve port/dialect, and build the app and server.

    Returns a `Diagnostic` (not a raise) when `DCT_DEFAULT_THEME` names an
    unknown theme — the CLI formats and exits on either return shape. A
    `dbt_charts.yml` validation failure (`load_config`) raises `ValidationError`
    uncaught; the CLI's own `try/except` around this call handles that,
    unchanged from before this function existed.
    """
    from dbt_charts.core.compile.config import list_built_in_themes, load_config
    from dbt_charts.core.diagnostics import ERR_INVALID_DEFAULT_THEME
    from dbt_charts.core.project_roots import infer_dialect_from_dbt
    from dbt_charts.core.serve.port import resolve_port
    from dbt_charts.core.serve.server import create_server
    from dbt_charts.core.serve.shutdown import build_server

    env_theme = os.environ.get("DCT_DEFAULT_THEME")  # noqa: TID251 — DCT_DEFAULT_THEME knob
    # Empty string is treated as unset (matches get_default_theme_name, which
    # falls back to the shipped default) — only a non-empty unknown name errors.
    if env_theme:
        available = list_built_in_themes()
        if env_theme not in available:
            return DbtChartsError.from_code(
                ERR_INVALID_DEFAULT_THEME,
                source="DCT_DEFAULT_THEME env var",
                theme=env_theme,
                available=available,
            ).to_diagnostic()

    # Validate dbt_charts.yml wholesale: stray keys (style:, board:, theme:, etc.) are a
    # hard error here at startup rather than silently ignored.
    load_config(project)

    project_dir = project.root
    resolved_port = resolve_port(explicit_port=port, project_dir=project_dir, host=host)

    effective_target = target or os.environ.get("DBT_TARGET") or None

    dialect_inferred = False
    effective_dialect = dialect
    if effective_dialect is None:
        inferred = infer_dialect_from_dbt(project.dbt_root, effective_target)
        effective_dialect = inferred or "duckdb"
        dialect_inferred = bool(inferred)

    app = create_server(
        project,
        dialect=effective_dialect,
        target=effective_target,
        max_workers=max_workers,
        no_cache=no_cache,
        cache_path=cache_path,
    )

    return ServeSetup(
        app=app,
        # 3s bounds a slow in-flight render; the live-reload streams end on
        # their own, because shutdown closes the watch they read from.
        server=build_server(
            app, host=host, port=resolved_port, timeout_graceful_shutdown=3
        ),
        port=resolved_port,
        dialect=effective_dialect,
        dialect_inferred=dialect_inferred,
    )


def format_startup_failure(exc: Exception) -> Diagnostic:
    """Wrap an uncaught uvicorn startup exception in the ERR-STARTUP-FAILED envelope."""
    from dbt_charts.core.diagnostics import ERR_STARTUP_FAILED

    return DbtChartsError.from_code(ERR_STARTUP_FAILED, detail=str(exc)).to_diagnostic()
