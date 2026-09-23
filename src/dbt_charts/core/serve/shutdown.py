"""The uvicorn server `dct serve` and the embedded preview run.

uvicorn waits for in-flight responses before it runs lifespan shutdown, so a
watch closed there would be closed too late: the live-reload streams it feeds
would still be open, and uvicorn force-cancels those once its graceful timeout
expires (or waits on them forever, where no timeout is set). The server closes
the watch on its own way down instead, and the streams end before the wait.
"""

from __future__ import annotations

import socket  # noqa: TID251 — the type in uvicorn.Server.shutdown's signature
from typing import TYPE_CHECKING, Any

import uvicorn

if TYPE_CHECKING:
    from dbt_charts.core.serve.watcher import FileWatcher


class GracefulServer(uvicorn.Server):
    """Closes the file watch before uvicorn waits on the streams reading from it."""

    def __init__(self, config: uvicorn.Config, watcher: FileWatcher) -> None:
        super().__init__(config)
        self._watcher = watcher

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        # finally: a raise here must not cost uvicorn its own shutdown — the
        # listen socket would stay open and lifespan shutdown never run.
        try:
            await self._watcher.stop()
        finally:
            await super().shutdown(sockets)


def build_server(
    app: Any,  # type-state: explicit_any — duck-typed: ASGI app + state.watcher
    host: str,
    port: int,
    log_level: str = "info",
    timeout_graceful_shutdown: int | None = None,
) -> uvicorn.Server:
    """Build the uvicorn server for an app that `create_server` built.

    `timeout_graceful_shutdown` is uvicorn's own knob, passed through: `None`
    waits indefinitely. It bounds a slow render, not the watch streams — those
    end on their own once `shutdown` above closes the watch.
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
    )
