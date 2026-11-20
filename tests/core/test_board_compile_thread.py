"""The engine's compile runs on a thread of its own and releases what that thread holds."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest

from dbt_charts.core.board import _compile_alongside
from dbt_charts.core.compile.compiler import CompileResult
from dbt_charts.core.execute.query_plan import QueryPlan


def _python_compile() -> CompileResult:
    return CompileResult(errors=[], warnings=[], suppressed_warnings=[], diagnostics=[])


def test_the_engine_thread_releases_after_its_job() -> None:
    events: list[tuple[str, str]] = []

    def job() -> QueryPlan:
        events.append(("job", threading.current_thread().name))
        return {}

    def release() -> None:
        events.append(("release", threading.current_thread().name))

    with _compile_alongside(_python_compile, job, release) as (_, plan_of):
        plan_of()

    assert [kind for kind, _ in events] == ["job", "release"]
    assert {name for _, name in events} == {events[0][1]}
    assert events[0][1].startswith("dct-plan")
    assert events[0][1] != threading.current_thread().name


def test_the_engine_thread_releases_when_its_job_fails() -> None:
    released = threading.Event()

    def job() -> QueryPlan:
        raise ValueError("the engine refused")

    with (
        _compile_alongside(_python_compile, job, released.set) as (_, plan_of),
        pytest.raises(ValueError, match="the engine refused"),
    ):
        plan_of()
    assert released.wait(timeout=5)


def _slow_job(finished: threading.Event) -> Callable[[], QueryPlan]:
    def job() -> QueryPlan:
        time.sleep(0.2)
        finished.set()
        return {}

    return job


def test_leaving_without_the_plan_waits_for_the_engine_thread() -> None:
    """Nothing reads the project after a caller that never asked for the plan leaves."""
    finished = threading.Event()

    with _compile_alongside(_python_compile, _slow_job(finished), lambda: None):
        pass

    assert finished.is_set()


def test_a_raising_compile_waits_for_the_engine_thread() -> None:
    finished = threading.Event()

    def python_compile() -> CompileResult:
        raise RuntimeError("the compile crashed")

    with (
        pytest.raises(RuntimeError, match="the compile crashed"),
        _compile_alongside(python_compile, _slow_job(finished), lambda: None),
    ):
        pass

    assert finished.is_set()
