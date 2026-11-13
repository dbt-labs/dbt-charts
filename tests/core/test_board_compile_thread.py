"""The engine's compile runs on a thread of its own and releases what that thread holds."""

from __future__ import annotations

import threading

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

    _compile_alongside(_python_compile, job, release)[1]()

    assert [kind for kind, _ in events] == ["job", "release"]
    assert {name for _, name in events} == {events[0][1]}
    assert events[0][1].startswith("dct-plan")
    assert events[0][1] != threading.current_thread().name


def test_the_engine_thread_releases_when_its_job_fails() -> None:
    released = threading.Event()

    def job() -> QueryPlan:
        raise ValueError("the engine refused")

    _, plan_of = _compile_alongside(_python_compile, job, released.set)
    with pytest.raises(ValueError, match="the engine refused"):
        plan_of()
    assert released.wait(timeout=5)
