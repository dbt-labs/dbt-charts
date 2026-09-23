"""Project file watching for `dct serve`'s live reload.

One watch per server, shared by every connected tab. The watch outlives any
single request, so the server owns it: startup opens it, shutdown closes it,
and closing ends every stream reading from it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)


class FileWatcher:
    """Watches a project root and fans each change out to the live-reload streams."""

    def __init__(self, root: str) -> None:
        self._root = root
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        # Queued True per change, False once when the watch closes.
        self._subscribers: set[asyncio.Queue[bool]] = set()
        self._closed = False

    async def start(self) -> None:
        """Open the watch. Idempotent, like stop()."""
        if self._task is None:
            self._task = asyncio.create_task(self._watch())

    async def stop(self) -> None:
        """Close the watch and end every stream reading from it. Idempotent."""
        self._stop.set()
        if self._task is not None:
            task, self._task = self._task, None
            await task
        self._close()  # also closes a watch that was never opened

    async def changes(self) -> AsyncGenerator[None, None]:
        """Yield once per change, until the watch closes."""
        if self._closed:
            return
        queue: asyncio.Queue[bool] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            while await queue.get():
                yield
        finally:
            self._subscribers.discard(queue)

    async def _watch(self) -> None:
        from watchfiles import awatch  # noqa: PLC0415

        try:
            async for _ in awatch(self._root, stop_event=self._stop):
                self._publish(True)
        except Exception:  # noqa: BLE001 — background-task boundary; see below
            # Nothing awaits this task until stop(), and stop() runs inside
            # uvicorn's shutdown: re-raising there would skip the rest of it and
            # turn CTRL-C into a startup-failure exit. Live reload is over
            # either way, so report it here and let the server keep serving.
            logger.exception("live-reload watch of %s failed", self._root)
        finally:
            # However the watch ends — stopped, or failed above — the streams
            # reading from it are over, and are waiting to be told.
            self._close()

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._publish(False)

    def _publish(self, more: bool) -> None:
        for queue in self._subscribers:
            queue.put_nowait(more)
