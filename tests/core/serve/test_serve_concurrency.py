"""Renders run off the event loop, so serve stays responsive and stoppable."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import uvicorn
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
from dbt_charts.core.serve import server as server_mod, shutdown as shutdown_mod

_BOARD_YAML = """\
title: Concurrency
source: duckdb
queries:
  q:
    sql: "SELECT 1 AS v"
charts:
  c:
    query: q
    type: kpi
    value: v
rows:
  - c
"""


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "x.yml").write_text(_BOARD_YAML)
    (tmp_path / "dbt_charts.yml").write_text("# project config\n")
    return tmp_path


def _in_thread(fn: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    out: list[Any] = []
    thread = threading.Thread(target=lambda: out.append(fn()), daemon=True)
    thread.start()
    return thread, out


class _BlockingRender:
    """Stands in for `_render_board_file`; the first call blocks until released."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.registries: list[AdapterRegistry] = []

    def __call__(self, *_a: Any, adapter_registry: AdapterRegistry, **_k: Any) -> Any:
        self.registries.append(adapter_registry)
        if len(self.registries) == 1:
            self.entered.set()
            self.release.wait(10)
        return HTMLResponse("ok")


@pytest.fixture
def blocking_render(monkeypatch: pytest.MonkeyPatch) -> _BlockingRender:
    render = _BlockingRender()
    monkeypatch.setattr(server_mod, "_render_board_file", render)
    return render


def test_health_answers_while_a_board_renders(
    tmp_path: Path, blocking_render: _BlockingRender
) -> None:
    app = server_mod.create_server(FilesystemProject(_make_project(tmp_path)))
    with TestClient(app) as client:
        board, _ = _in_thread(lambda: client.get("/x"))
        assert blocking_render.entered.wait(5)

        health, status = _in_thread(lambda: client.get("/health").status_code)
        health.join(5)
        answered_during_render = list(status)

        blocking_render.release.set()
        board.join(10)

    assert answered_during_render == [200]


def test_config_reload_closes_the_old_registry_only_after_its_render(
    tmp_path: Path, blocking_render: _BlockingRender, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path)
    app = server_mod.create_server(FilesystemProject(project))
    with TestClient(app) as client:
        board, _ = _in_thread(lambda: client.get("/x"))
        assert blocking_render.entered.wait(5)
        old = app.state.adapter_registry
        close = MagicMock(wraps=old.close)
        monkeypatch.setattr(old, "close", close)

        config = project / "dbt_charts.yml"
        st = config.stat()
        os.utime(config, (st.st_atime, st.st_mtime + 1))
        second, second_status = _in_thread(lambda: client.get("/x").status_code)
        second.join(5)
        assert second_status == [200], "second request waited on the first render"
        closed_while_rendering = close.call_count

        blocking_render.release.set()
        board.join(10)
        assert app.state.adapter_registry is not old

    assert closed_while_rendering == 0
    close.assert_called_once()
    assert blocking_render.registries[0] is old
    assert blocking_render.registries[1] is not old


@pytest.fixture
def uvicorn_shutdown_calls(monkeypatch: pytest.MonkeyPatch) -> list[None]:
    """Replace uvicorn's own shutdown, which needs a server that actually served."""
    calls: list[None] = []

    async def _shutdown(_self: uvicorn.Server, _sockets: Any = None) -> None:
        calls.append(None)

    monkeypatch.setattr(uvicorn.Server, "shutdown", _shutdown)
    return calls


def test_shutdown_interrupts_every_live_registry(
    tmp_path: Path, uvicorn_shutdown_calls: list[None]
) -> None:
    app = server_mod.create_server(FilesystemProject(_make_project(tmp_path)))
    current, leased = MagicMock(), MagicMock()
    app.state.adapter_registry = current
    app.state.registry_leases.acquire(leased)
    server = shutdown_mod.build_server(app, host="localhost", port=0)

    asyncio.run(server.shutdown())

    current.interrupt.assert_called_once()
    leased.interrupt.assert_called_once()
    assert uvicorn_shutdown_calls == [None]


def test_shutdown_arms_the_hard_exit_before_interrupting(
    monkeypatch: pytest.MonkeyPatch, uvicorn_shutdown_calls: list[None]
) -> None:
    # Armed in shutdown(), not after run(): a stuck driver blocks lifespan
    # close, so run() never returns.
    events: list[object] = []
    monkeypatch.setattr(shutdown_mod, "force_exit_after", events.append)
    watcher = MagicMock()
    watcher.stop = MagicMock(side_effect=lambda: asyncio.sleep(0))
    server = shutdown_mod.GracefulServer(
        uvicorn.Config(MagicMock()), watcher, lambda: events.append("interrupt"), 8
    )

    asyncio.run(server.shutdown())

    assert events == [8, "interrupt"]


def test_static_paths_outside_the_font_mount_still_reach_the_board_route(
    tmp_path: Path,
) -> None:
    app = server_mod.create_server(FilesystemProject(_make_project(tmp_path)))
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/static/nope.css").status_code != 500


def test_a_registry_leased_after_shutdown_began_is_interrupted() -> None:
    leases = server_mod.RegistryLeases()
    current, late = MagicMock(), MagicMock()

    leases.interrupt_all(current)
    leases.acquire(late)
    leases.acquire(current)
    leases.acquire(late)

    current.interrupt.assert_called_once()
    late.interrupt.assert_called_once()


def test_close_retired_closes_registries_still_leased() -> None:
    leases = server_mod.RegistryLeases()
    old = MagicMock()
    leases.acquire(old)
    leases.retire(old)
    old.close.assert_not_called()

    leases.close_retired()

    old.close.assert_called_once()


def test_force_exit_after_ends_an_exit_stuck_on_a_pool_thread() -> None:
    script = (
        "import time\n"
        "from concurrent.futures import ThreadPoolExecutor\n"
        "from dbt_charts.core.serve.shutdown import force_exit_after\n"
        "ThreadPoolExecutor(1).submit(time.sleep, 60)\n"
        "force_exit_after(0.5)\n"
    )
    started = time.monotonic()
    proc = subprocess.run(  # noqa: S603 — our own module
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
    )

    assert time.monotonic() - started < 20
    assert proc.returncode == 1
    assert "exiting" in proc.stderr
