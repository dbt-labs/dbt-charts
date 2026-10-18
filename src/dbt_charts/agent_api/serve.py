"""Typed startup composition for `dct serve`: validation, resolution, and app construction."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
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

    settings, dialect_inferred = resolve_serve_settings(
        project,
        dialect=dialect,
        target=target,
        max_workers=max_workers,
        no_cache=no_cache,
        cache_path=cache_path,
    )

    app = create_server(
        project,
        dialect=settings.dialect,
        target=settings.target,
        max_workers=settings.max_workers,
        no_cache=settings.no_cache,
        cache_path=settings.cache_path,
        allow_mutating_methods=True,
    )

    return ServeSetup(
        app=app,
        # 3s bounds a slow in-flight render; the live-reload streams end on
        # their own, because shutdown closes the watch they read from.
        server=build_server(
            app,
            host=host,
            port=resolved_port,
            timeout_graceful_shutdown=3,
            exit_deadline=8,
        ),
        port=resolved_port,
        dialect=settings.dialect,
        dialect_inferred=dialect_inferred,
    )


def format_startup_failure(exc: Exception) -> Diagnostic:
    """Wrap an uncaught uvicorn startup exception in the ERR-STARTUP-FAILED envelope."""
    from dbt_charts.core.diagnostics import ERR_STARTUP_FAILED

    return DbtChartsError.from_code(ERR_STARTUP_FAILED, detail=str(exc)).to_diagnostic()


class ServeSettings(BaseModel):
    """The settings a server runs with, with every default and env var resolved.

    ``prepare_serve`` (the foreground server) and the ``--detach`` check both
    start from this, so they cannot disagree; ``--detach`` records it to tell
    whether a running server still matches the request.
    """

    model_config = ConfigDict(frozen=True)

    dialect: str
    target: str | None
    max_workers: int | None
    no_cache: bool
    cache_path: Path | None


def resolve_serve_settings(
    project: FilesystemProject,
    *,
    dialect: str | None,
    target: str | None,
    max_workers: int | None,
    no_cache: bool,
    cache_path: Path | None,
) -> tuple[ServeSettings, bool]:
    """Resolve the serve options; also returns whether dbt supplied the dialect."""
    from dbt_charts.core.project_roots import infer_dialect_from_dbt

    effective_target = target or os.environ.get("DBT_TARGET") or None
    inferred = (
        None
        if dialect is not None
        else infer_dialect_from_dbt(project.dbt_root, effective_target)
    )
    settings = ServeSettings(
        dialect=dialect or inferred or "duckdb",
        target=effective_target,
        max_workers=max_workers,
        no_cache=no_cache,
        cache_path=cache_path,
    )
    return settings, bool(inferred)


_SHUTDOWN_TIMEOUT = 15.0
_PORT_RELEASE_TIMEOUT = 15.0


class DetachedServeError(RuntimeError):
    """A detached server could not be started, found or stopped."""


class CheckedBoard(BaseModel):
    """A served board: its file, its URL and what checking it found."""

    model_config = ConfigDict(frozen=True)

    path: str
    url: str
    errors: list[Diagnostic]
    warnings: list[Diagnostic]
    description: str | None


class DetachedServer(BaseModel):
    model_config = ConfigDict(frozen=True)

    pid: int
    port: int
    reused: bool
    replaced: str | None = None
    boards: list[CheckedBoard]


class _State(BaseModel):
    """What ``--detach`` records about the server it started on a port."""

    pid: int
    host: str
    settings: ServeSettings


def _state_path(port: int) -> Path:
    return Path(tempfile.gettempdir()) / f"dct-serve-{port}.json"


def _read_state(port: int) -> _State | None:
    path = _state_path(port)
    if not path.exists():
        return None
    try:
        return _State.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise DetachedServeError(
            f"The record of the detached server on port {port} is unreadable "
            f"({path}); delete it and retry."
        ) from exc


def _probe_host(bind_host: str) -> str:
    """Where to reach a server bound to *bind_host*: a wildcard bind is not an address."""
    return {"0.0.0.0": "127.0.0.1", "": "127.0.0.1", "::": "::1"}.get(
        bind_host, bind_host
    )


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def _health(host: str, port: int) -> dict[str, Any] | None:
    """What the dbt Charts server on *host*:*port* says about itself, or None.

    Identifying the server by asking it (pid and project, from ``/health``)
    needs no OS process inspection, so it works the same on Windows, which has
    no ``ps``, ``/proc`` or signal-0 liveness probe.
    """
    try:
        with urllib.request.urlopen(  # noqa: S310 — local server
            f"http://{_url_host(_probe_host(host))}:{port}/health", timeout=2
        ) as response:
            body = json.load(response)
    except (OSError, ValueError):
        return None
    return body if body.get("service") == "dbt-charts-server" else None


def _recorded_server(state: _State, port: int) -> dict[str, Any] | None:
    """The health of the server *state* recorded, or None once it is gone.

    A different pid answering means the recorded one died and its pid may have
    been reused; that server is not ours to stop or reuse.
    """
    served = _health(state.host, port)
    return served if served is not None and served["pid"] == state.pid else None


def _serves(served: dict[str, Any], root: Path) -> bool:
    return Path(served["project"]).resolve() == root.resolve()


def _serve_args(settings: ServeSettings) -> list[str]:
    """The ``dct serve`` flags that reproduce *settings* in the child."""
    return [
        "--dialect",
        settings.dialect,
        *(["--target", settings.target] if settings.target else []),
        *(["--max-workers", str(settings.max_workers)] if settings.max_workers else []),
        *(["--no-cache"] if settings.no_cache else []),
        *(["--cache", str(settings.cache_path)] if settings.cache_path else []),
    ]


def _spawn(
    root: Path, host: str, port: int, settings: ServeSettings, startup_timeout: float
) -> int:
    """Start ``dct serve`` detached from this process; returns the server's pid."""
    log_file = Path(tempfile.gettempdir()) / f"dct-serve-{port}.log"
    # Leave the terminal's session (POSIX) or console (Windows), so closing
    # it does not take the server down.
    detach_options: dict[str, Any] = (
        {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
        }
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    with log_file.open("wb") as log:
        child = subprocess.Popen(  # noqa: S603 — our own interpreter and CLI
            [
                sys.executable,
                "-m",
                "dbt_charts.cli.main",
                "serve",
                "--port",
                str(port),
                "--host",
                host,
                "--project-dir",
                str(root),
                *_serve_args(settings),
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            **detach_options,
        )
    deadline = time.monotonic() + startup_timeout
    while (served := _health(host, port)) is None:
        exited = child.poll()
        if exited is not None or time.monotonic() > deadline:
            if exited is None:
                child.kill()
                child.wait()
            log_text = log_file.read_text(encoding="utf-8", errors="replace")
            tail = "\n".join(log_text.splitlines()[-15:])
            raise DetachedServeError(
                f"The server did not start (exit {exited}):\n{tail}"
            )
        time.sleep(0.2)
    if not _serves(served, root):
        # The port was taken between the probe and the bind; our child lost.
        raise DetachedServeError(
            f"Port {port} is answered by another server ({served['project']}); "
            "pick a free one with --port."
        )
    # Not child.pid: on Windows a venv's python.exe is a launcher that
    # starts the real interpreter as its own child.
    return int(served["pid"])


def _board_urls(project: FilesystemProject, base_url: str) -> dict[str, str]:
    """Every board file of the project (relpath) and the URL it is served at."""
    from dbt_charts.agent_api.boards import list_boards
    from dbt_charts.core.project import CHARTS_SUBDIR

    listing = list_boards(project, under=CHARTS_SUBDIR)
    # A file the listing skips (not valid YAML, not a mapping) is still a board
    # the author wrote: the check reports why it does not render.
    relpaths = sorted(
        [b.file.relpath for b in listing.boards]
        + [s.file.relpath for s in listing.skipped_files]
    )
    served_at = {
        relpath: relpath.removeprefix(f"{CHARTS_SUBDIR}/").removesuffix(".yml")
        for relpath in relpaths
    }
    return {relpath: f"{base_url}/{stem}/" for relpath, stem in served_at.items()}


def _check_boards(
    project: FilesystemProject,
    urls: dict[str, str],
    *,
    settings: ServeSettings,
    describe: bool,
) -> list[CheckedBoard]:
    """Render every board as the text check, under the server's own settings.

    The registry comes from ``build_serve_registry``, the server's own, and the
    cache from the same settings, so a board passes here exactly when the
    server can render it.
    """
    from dbt_charts.agent_api._paths import (
        render_crash_diagnostic,
        resolve_board_or_error,
    )
    from dbt_charts.agent_api.cache import project_cache_ctx
    from dbt_charts.agent_api.project_session import ProjectSession
    from dbt_charts.core.serve.server import build_serve_registry

    registry = build_serve_registry(
        project,
        dialect=settings.dialect,
        target=settings.target,
        max_workers=settings.max_workers,
        allow_mutating_methods=True,
    )
    checked: list[CheckedBoard] = []
    with (
        project_cache_ctx(
            project, no_cache=settings.no_cache, cache_path=settings.cache_path
        ) as cache,
        ProjectSession.from_project(
            project,
            cache=cache,
            adapter_registry=registry,
            owns_registry=True,
            allow_mutating_methods=True,
        ) as session,
    ):
        for relpath, url in urls.items():
            errors: list[Diagnostic]
            warnings: list[Diagnostic] = []
            description: str | None = None
            try:
                resolved = resolve_board_or_error(Path(relpath), project)
                if isinstance(resolved, Diagnostic):
                    errors = [resolved]
                else:
                    result = session.render_board(
                        board=resolved,
                        format="text",
                        use_cache=not settings.no_cache,
                        max_workers=settings.max_workers,
                    )
                    errors = [
                        *(
                            result.validation_errors
                            or ([result.board_error] if result.board_error else [])
                        ),
                        *result.chart_errors,
                    ]
                    warnings = result.warnings
                    if describe and isinstance(result.data, str):
                        description = result.data
            except Exception as exc:  # noqa: BLE001 — one board's crash is its verdict
                errors = [render_crash_diagnostic(Path(relpath), exc)]
            checked.append(
                CheckedBoard(
                    path=relpath,
                    url=url,
                    errors=errors,
                    warnings=warnings,
                    description=description,
                )
            )
    return checked


def start_detached(
    project: FilesystemProject,
    *,
    port: int | None,
    host: str,
    dialect: str | None = None,
    target: str | None = None,
    max_workers: int | None = None,
    no_cache: bool = False,
    cache_path: Path | None = None,
    describe: bool = False,
    startup_timeout: float = 90,
) -> DetachedServer:
    """Serve *project* in a background process, wait until it answers, check it.

    The server takes the same options as ``dct serve``. Without *port* the
    project's own running detached server is found first, wherever
    auto-resolution once placed it. A server this project already runs on the
    port with the same options is reused; a detached server started with other
    options or for another project there is stopped and replaced (the last
    request on a port wins); any other listener is an error and nothing is
    spawned. Every board is then checked under the server's settings.

    Raises:
        DetachedServeError: port taken, or the server did not come up.
    """
    from dbt_charts.core.serve.port import (
        PORT_SCAN_ATTEMPTS,
        is_port_available,
        preferred_port,
        resolve_port,
    )

    root = project.root
    settings, _ = resolve_serve_settings(
        project,
        dialect=dialect,
        target=target,
        max_workers=max_workers,
        no_cache=no_cache,
        cache_path=cache_path,
    )
    resolved = port
    if resolved is None:
        base = preferred_port(project_dir=root)
        resolved = next(
            (
                candidate
                for candidate in range(base, min(base + PORT_SCAN_ATTEMPTS, 65536))
                if (state := _read_state(candidate)) is not None
                and (served := _recorded_server(state, candidate)) is not None
                and _serves(served, root)
            ),
            None,
        )
    if resolved is None:
        resolved = resolve_port(project_dir=root, host=host)
    state_file = _state_path(resolved)
    pid: int | None = None
    replaced: str | None = None
    if (state := _read_state(resolved)) is not None:
        served = _recorded_server(state, resolved)
        if served is None:
            state_file.unlink()  # the recorded server is gone; its pid may be reused
        elif (
            _serves(served, root) and state.host == host and state.settings == settings
        ):
            pid = state.pid
        else:
            stop_detached(resolved)
            replaced = served["project"]
    reused = pid is not None
    if pid is None:
        if not is_port_available(resolved, host):
            raise DetachedServeError(
                f"Port {resolved} is in use by another process; "
                "pick a free one with --port."
            )
        pid = _spawn(root, host, resolved, settings, startup_timeout)
        state_file.write_text(
            _State(pid=pid, host=host, settings=settings).model_dump_json(),
            encoding="utf-8",
        )
    return DetachedServer(
        pid=pid,
        port=resolved,
        reused=reused,
        replaced=replaced,
        boards=_check_boards(
            project,
            _board_urls(project, f"http://{host}:{resolved}"),
            settings=settings,
            describe=describe,
        ),
    )


def stop_detached(port: int) -> int:
    """Stop the detached server on *port*; returns its pid.

    Raises:
        DetachedServeError: no server started with ``--detach`` is running on
            the port, or the port is still bound once the server has stopped.
    """
    from dbt_charts.core.serve.port import is_port_available

    state = _read_state(port)
    if state is None:
        raise DetachedServeError(f"No detached server recorded on port {port}.")
    _state_path(port).unlink()

    def running() -> bool:
        return _recorded_server(state, port) is not None

    if not running():
        raise DetachedServeError(
            f"No detached server is running on port {port} (pid {state.pid} is gone)."
        )
    # On Windows os.kill(..., SIGTERM) is TerminateProcess; on POSIX it asks
    # the server to shut down cleanly.
    os.kill(state.pid, signal.SIGTERM)
    deadline = time.monotonic() + _SHUTDOWN_TIMEOUT
    while running() and time.monotonic() < deadline:
        time.sleep(0.1)
    if running() and sys.platform != "win32":
        os.kill(state.pid, signal.SIGKILL)
    # /health going quiet is not the socket being released: the OS frees the
    # listener after the process is gone (observably later on Windows), and a
    # replacement that binds in between fails with "port in use".
    deadline = time.monotonic() + _PORT_RELEASE_TIMEOUT
    while not is_port_available(port, state.host):
        if time.monotonic() > deadline:
            raise DetachedServeError(
                f"Stopped pid {state.pid} but port {port} is still in use after "
                f"{_PORT_RELEASE_TIMEOUT:.0f}s."
            )
        time.sleep(0.1)
    return state.pid
