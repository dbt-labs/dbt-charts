"""Tests for the uvicorn server `dct serve` and the embedded preview run."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

from dbt_charts.core.serve.shutdown import GracefulServer, build_server
from dbt_charts.core.serve.watcher import FileWatcher


async def _shutdown_with_a_stream_open(
    server: uvicorn.Server, watcher: FileWatcher
) -> None:
    """Shut down while a live-reload stream is open; it must end, not hang."""
    stream = asyncio.create_task(anext(watcher.changes().__aiter__(), None))
    await asyncio.sleep(0)
    await server.shutdown()
    await asyncio.wait_for(stream, timeout=5)


@pytest.fixture
def uvicorn_shutdown_calls(monkeypatch: pytest.MonkeyPatch) -> list[None]:
    """Replace uvicorn's own shutdown, which needs a server that actually served."""
    calls: list[None] = []

    async def _shutdown(
        _self: uvicorn.Server, _sockets: list[socket.socket] | None = None
    ) -> None:
        calls.append(None)

    monkeypatch.setattr(uvicorn.Server, "shutdown", _shutdown)
    return calls


def test_shutdown_closes_the_watch(uvicorn_shutdown_calls: list[None]) -> None:
    """Shutting down ends the live-reload streams by closing what feeds them."""
    watcher = FileWatcher("charts")
    server = GracefulServer(uvicorn.Config(FastAPI()), watcher)

    asyncio.run(_shutdown_with_a_stream_open(server, watcher))

    assert uvicorn_shutdown_calls == [None], "must still hand off to uvicorn"


def test_build_server_takes_the_apps_own_watch(
    tmp_path: Path, uvicorn_shutdown_calls: list[None]
) -> None:
    """The server closes the watch its app hands out, not one of its own."""
    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.serve.server import create_server

    (tmp_path / "dbt_charts.yml").write_text("# project config\n")
    app = create_server(FilesystemProject(tmp_path))
    server = build_server(app, host="localhost", port=0)

    asyncio.run(_shutdown_with_a_stream_open(server, app.state.watcher))
