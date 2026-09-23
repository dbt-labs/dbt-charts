"""Tests for the embedded-preview server helper.

`build_embedded_server` backs `dct mcp serve`'s FastAPI-on-uvicorn preview
lifecycle. This module covers port resolution, the uvicorn availability
probe, and server construction.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.serve.embedded import build_embedded_server


def test_build_embedded_server_returns_server_and_port(tmp_path: Path) -> None:
    server, port = build_embedded_server(FilesystemProject(tmp_path), port_hint=18790)
    assert server.config.host == "localhost"
    # When the hint is free, resolved port equals the hint.
    assert port == 18790


def test_build_embedded_server_keeps_local_authoring_posture(tmp_path: Path) -> None:
    """The `dct mcp serve` preview keeps external DuckDB access on.

    Standalone `dct serve` tightened to strict read-only, but the embedded
    preview must agree with its authoring session (opened with
    LOCAL_AUTHORING_REGISTRY_KWARGS) so a clickable render URL against a project
    using SQL file readers works when the tool-side render did.
    """
    from dbt_charts.core.execute.adapters import LOCAL_AUTHORING_REGISTRY_KWARGS

    server, _port = build_embedded_server(FilesystemProject(tmp_path), port_hint=18795)
    app_state = server.config.app.state
    assert app_state.read_only is LOCAL_AUTHORING_REGISTRY_KWARGS["read_only"]
    assert app_state.allow_external_access_in_readonly is True
    assert app_state.duckdb_config == LOCAL_AUTHORING_REGISTRY_KWARGS["duckdb_config"]


def test_build_embedded_server_closes_the_watch_on_shutdown(tmp_path: Path) -> None:
    """`dct mcp serve` sets no graceful timeout, so an open stream would hang it.

    What that shutdown does is covered in tests/core/serve/test_shutdown.py; this
    pins that the embedded preview gets it too.
    """
    from dbt_charts.core.serve.shutdown import GracefulServer

    server, _port = build_embedded_server(FilesystemProject(tmp_path), port_hint=18797)

    assert isinstance(server, GracefulServer)
    assert server.config.app.state.watcher is not None


def test_build_embedded_server_increments_when_port_taken(tmp_path: Path) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("localhost", 0))
    s.listen(1)
    try:
        taken = s.getsockname()[1]
        _server, port = build_embedded_server(
            FilesystemProject(tmp_path), port_hint=taken
        )
        assert port != taken
        assert port > taken
    finally:
        s.close()


def test_build_embedded_server_raises_import_error_on_uvicorn_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing uvicorn is an install-time problem with a clear remediation —
    surface it as ImportError, not as a None return that the caller has to
    pattern-match.

    The remediation names uvicorn and a plain dbt charts reinstall: uvicorn is a
    runtime dependency, so pointing at the `mcp` extra (which ships `mcp`
    alone) would send the user somewhere that cannot fix it.
    """
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    # Evict the importer too, so it re-executes its own `import uvicorn`.
    monkeypatch.delitem(sys.modules, "dbt_charts.core.serve.shutdown", raising=False)
    with pytest.raises(ImportError, match=r"uvicorn"):
        build_embedded_server(FilesystemProject(tmp_path), port_hint=18800)


def test_build_embedded_server_raises_runtime_error_on_port_exhaustion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Port exhaustion is a runtime resource problem, not a missing-dep problem.

    `find_available_port` already raises `RuntimeError`. The builder must
    let that propagate rather than swallow it into a None return — silent
    None means callers can't tell why preview is unavailable.
    """
    from dbt_charts.core.serve import embedded

    def _exhausted(*args: object, **kwargs: object) -> int:
        raise RuntimeError(
            "Could not find an available port after 10 attempts (starting from 8765)"
        )

    monkeypatch.setattr(embedded, "find_available_port", _exhausted)

    with pytest.raises(RuntimeError, match="Could not find an available port"):
        embedded.build_embedded_server(FilesystemProject(tmp_path), port_hint=8765)
