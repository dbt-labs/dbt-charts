"""The uvicorn server `dct serve` and the embedded preview run.

uvicorn waits for in-flight responses before it runs lifespan shutdown, so a
watch closed there would be closed too late: the live-reload streams it feeds
would still be open, and uvicorn force-cancels those once its graceful timeout
expires (or waits on them forever, where no timeout is set). The server closes
the watch on its own way down instead, and the streams end before the wait.

It interrupts running queries first: Python joins every pool thread at exit,
so a query left running holds the process until the warehouse answers.
"""

from __future__ import annotations

import asyncio
import os
import socket  # noqa: TID251 — the type in uvicorn.Server.shutdown's signature
import sys
import threading
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any

import uvicorn

from dbt_charts.core.serve.server import interrupt_queries

if TYPE_CHECKING:
    from dbt_charts.core.serve.watcher import FileWatcher


class GracefulServer(uvicorn.Server):
    """Interrupts queries and closes the file watch before uvicorn's own wait."""

    def __init__(
        self,
        config: uvicorn.Config,
        watcher: FileWatcher,
        interrupt: Callable[[], None],
        exit_deadline: float | None,
    ) -> None:
        super().__init__(config)
        self._watcher = watcher
        self._interrupt = interrupt
        self._exit_deadline = exit_deadline

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        if self._exit_deadline is not None:
            force_exit_after(self._exit_deadline)
        # finally: a raise here must not cost uvicorn its own shutdown — the
        # listen socket would stay open and lifespan shutdown never run.
        try:
            try:
                # A thread: a warehouse cancel can open a connection of its own.
                await asyncio.to_thread(self._interrupt)
            finally:
                await self._watcher.stop()
        finally:
            await super().shutdown(sockets)


def force_exit_after(seconds: float) -> None:
    """Exit hard if the process is still alive `seconds` from now.

    For a driver that ignores the cancel: its thread stays in the warehouse
    call, and both lifespan close and Python's exit wait on it. The timer
    thread is a daemon, so a clean exit ends it.
    """

    def _exit() -> None:
        sys.stderr.write(f"Shutdown still blocked after {seconds:g}s; exiting.\n")
        os._exit(1)

    timer = threading.Timer(seconds, _exit)
    timer.daemon = True
    timer.start()


def build_server(
    app: Any,  # type-state: explicit_any — duck-typed: ASGI app + state.watcher
    host: str,
    port: int,
    log_level: str = "info",
    timeout_graceful_shutdown: int | None = None,
    exit_deadline: float | None = None,
) -> uvicorn.Server:
    """Build the uvicorn server for an app that `create_server` built.

    `timeout_graceful_shutdown` is uvicorn's own knob, passed through: `None`
    waits indefinitely. It bounds a slow render, not the watch streams — those
    end on their own once `shutdown` above closes the watch.

    `exit_deadline` force-exits the process that many seconds after shutdown
    begins; `None` for a server embedded in a longer-lived process.
    """
    return GracefulServer(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level=log_level,
            timeout_graceful_shutdown=timeout_graceful_shutdown,
        ),
        app.state.watcher,
        partial(interrupt_queries, app.state),
        exit_deadline,
    )
