"""Run stage: misses in, one Outcome per query out, over fake executors."""

import logging
import threading
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from dbt_charts.core.compile.models.cache import CachePolicy
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.lookup import Miss
from dbt_charts.core.execute.run import (
    RENDER_POOL_THREAD_PREFIX,
    Failed,
    Outcome,
    Rows,
    run,
)

from ..._lookup import planned_query
from ..._results import rows_result

POLICY = CachePolicy(enabled=True, ttl="1h")
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _miss(name: str, sql: str = "SELECT 1") -> Miss:
    query = SqlQuery(sql=sql, source="db", cache=POLICY)
    return Miss(planned_query(name, query, ("s", name, "v")))


def _rows(miss: Miss) -> Outcome:
    return Rows(rows_result([{"n": miss.name}]), NOW)


def test_returns_one_outcome_per_miss() -> None:
    boom = QueryError("boom", "b")

    def execute(miss: Miss) -> Outcome:
        return Failed(boom, boom) if miss.name == "b" else _rows(miss)

    outcomes = run([_miss("a"), _miss("b")], execute, lambda m, o: None)

    assert isinstance(outcomes["a"], Rows)
    assert outcomes["b"] == Failed(boom, boom)


def test_raised_error_becomes_uncacheable_failed() -> None:
    def execute(miss: Miss) -> Outcome:
        raise RuntimeError("driver down")

    outcomes = run([_miss("a")], execute, lambda m, o: None)

    failed = outcomes["a"]
    assert isinstance(failed, Failed)
    assert failed.record is None
    assert str(failed.error) == "driver down"


def test_settles_each_outcome_once() -> None:
    settled: list[tuple[str, Outcome]] = []

    run(
        [_miss("a"), _miss("b")],
        _rows,
        lambda miss, outcome: settled.append((miss.name, outcome)),
        max_workers=4,
    )

    assert sorted(name for name, _ in settled) == ["a", "b"]


def test_pool_threads_carry_the_render_prefix() -> None:
    seen: list[str] = []

    def execute(miss: Miss) -> Outcome:
        seen.append(threading.current_thread().name)
        return _rows(miss)

    run([_miss("a"), _miss("b")], execute, lambda m, o: None, max_workers=2)

    assert all(name.startswith(RENDER_POOL_THREAD_PREFIX) for name in seen)


def test_without_workers_runs_on_the_calling_thread() -> None:
    seen: list[str] = []

    def execute(miss: Miss) -> Outcome:
        seen.append(threading.current_thread().name)
        return _rows(miss)

    run([_miss("a")], execute, lambda m, o: None)

    assert seen == [threading.current_thread().name]


def test_cache_ref_upstream_settles_before_its_consumer_starts() -> None:
    events: list[str] = []

    def execute(miss: Miss) -> Outcome:
        events.append(f"run:{miss.name}")
        return _rows(miss)

    def settle(miss: Miss, outcome: Outcome) -> None:
        events.append(f"settle:{miss.name}")

    run(
        [_miss("down", "SELECT * FROM {{ queries.up.cache }}"), _miss("up")],
        execute,
        settle,
        max_workers=4,
    )

    assert events == ["run:up", "settle:up", "run:down", "settle:down"]


def test_upstream_outside_the_misses_does_not_gate() -> None:
    outcomes: Mapping[str, Outcome] = run(
        [_miss("down", "SELECT * FROM {{ queries.cached.cache }}")],
        _rows,
        lambda m, o: None,
        max_workers=2,
    )

    assert isinstance(outcomes["down"], Rows)


def test_a_failed_store_write_keeps_the_rows_it_could_not_store(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def settle(miss: Miss, outcome: Outcome) -> None:
        if miss.name == "a":
            raise OSError("cache write failed")

    with caplog.at_level(logging.WARNING):
        outcomes = run([_miss("a"), _miss("b")], _rows, settle, max_workers=2)

    assert isinstance(outcomes["a"], Rows)
    assert isinstance(outcomes["b"], Rows)
    assert "cache write failed" in caplog.text
