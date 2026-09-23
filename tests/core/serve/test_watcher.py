"""Tests for the server-owned project file watch behind `/__livereload`.

Covers the contract the SSE streams depend on: a change reaches every stream,
and closing the watch ends every stream instead of leaving it waiting for a
change that can never arrive.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator

import pytest

from dbt_charts.core.serve.watcher import FileWatcher


async def _two_changes_awatch(
    *_args: object, **_kwargs: object
) -> AsyncGenerator[set[object], None]:
    """Stub for watchfiles.awatch: two changes, then the watch ends on its own."""
    yield {("modified", "charts/hello.yml")}
    yield {("modified", "charts/hello.yml")}


async def _blocking_awatch(
    *_args: object, stop_event: asyncio.Event, **_kwargs: object
) -> AsyncGenerator[set[object], None]:
    """Stub for watchfiles.awatch that reports no changes until stopped."""
    await stop_event.wait()
    for _ in ():
        yield set()


async def _raising_awatch(
    *_args: object, **_kwargs: object
) -> AsyncGenerator[set[object], None]:
    """Stub for watchfiles.awatch failing the way an inotify limit does."""
    # The empty loop is what makes this an async generator; the raise lands on
    # the first __anext__, like a watch that cannot be opened.
    for _ in ():
        yield set()
    raise OSError("inotify watch limit reached")


async def _drain(watcher: FileWatcher) -> int:
    return len([None async for _ in watcher.changes()])


def test_every_stream_sees_every_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """One watch feeds every open tab — that is why it is server-owned."""
    monkeypatch.setattr("watchfiles.awatch", _two_changes_awatch)

    async def scenario() -> list[int]:
        watcher = FileWatcher("charts")
        streams = [asyncio.create_task(_drain(watcher)) for _ in range(2)]
        await asyncio.sleep(0)  # let both subscribe before the watch runs
        await watcher.start()
        return await asyncio.gather(*streams)

    assert asyncio.run(scenario()) == [2, 2]


def test_stop_ends_a_waiting_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stream is parked on an unchanging project; stop must release it.

    This is the CTRL-C path: without it the SSE response stays open and uvicorn
    force-cancels it mid-response at the graceful-shutdown timeout.
    """
    monkeypatch.setattr("watchfiles.awatch", _blocking_awatch)

    async def scenario() -> int:
        watcher = FileWatcher("charts")
        stream = asyncio.create_task(_drain(watcher))
        await asyncio.sleep(0)
        await watcher.start()
        await watcher.stop()
        return await asyncio.wait_for(stream, timeout=5)

    assert asyncio.run(scenario()) == 0


def test_a_stream_opened_after_the_watch_closed_ends_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream that arrives late must not wait on a watch that is already over."""
    monkeypatch.setattr("watchfiles.awatch", _two_changes_awatch)

    async def scenario() -> int:
        watcher = FileWatcher("charts")
        await watcher.start()
        await watcher.stop()
        return await asyncio.wait_for(_drain(watcher), timeout=5)

    assert asyncio.run(scenario()) == 0


def test_stop_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both the server and the lifespan close the watch; the second is a no-op."""
    monkeypatch.setattr("watchfiles.awatch", _blocking_awatch)

    async def scenario() -> None:
        watcher = FileWatcher("charts")
        await watcher.start()
        await watcher.stop()
        await watcher.stop()

    asyncio.run(scenario())


def test_start_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second start must not orphan the first watch past stop()."""
    monkeypatch.setattr("watchfiles.awatch", _blocking_awatch)

    async def scenario() -> int:
        watcher = FileWatcher("charts")
        await watcher.start()
        await watcher.start()
        await watcher.stop()
        return len([t for t in asyncio.all_tasks() if not t.done()]) - 1

    assert asyncio.run(scenario()) == 0


def test_stop_closes_a_watch_that_never_opened() -> None:
    """The lifespan's backstop runs even when startup failed before start()."""

    async def scenario() -> int:
        watcher = FileWatcher("charts")
        await watcher.stop()
        return await asyncio.wait_for(_drain(watcher), timeout=5)

    assert asyncio.run(scenario()) == 0


def test_a_failed_watch_is_reported_and_does_not_break_shutdown(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """awatch can raise — an inotify limit, a permission error.

    Re-raising out of stop() would run inside uvicorn's shutdown and skip the
    rest of it, so the failure is reported here and the streams still end.
    """
    monkeypatch.setattr("watchfiles.awatch", _raising_awatch)

    async def scenario() -> int:
        watcher = FileWatcher("charts")
        await watcher.start()
        await watcher.stop()  # must not raise
        return await asyncio.wait_for(_drain(watcher), timeout=5)

    with caplog.at_level(logging.ERROR):
        assert asyncio.run(scenario()) == 0
    assert "inotify watch limit reached" in caplog.text
