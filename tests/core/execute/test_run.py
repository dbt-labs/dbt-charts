"""Run stage: misses in, one Outcome per query out, over fake executors."""

import logging
import sys
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.backends.base import BackendRegistry
from dbt_charts.core.execute.backends.duckdb_local import DuckDBBackend
from dbt_charts.core.execute.env import make_exec_env
from dbt_charts.core.execute.lookup import Miss
from dbt_charts.core.execute.query_result import resolve_effective_row_limit, take
from dbt_charts.core.execute.run import (
    RENDER_POOL_THREAD_PREFIX,
    Failed,
    Outcome,
    Rows,
    run,
    run_planned,
)

from ..._lookup import planned_query, sql_entry
from ..._results import rows_result
from ..._run import Run as BoardRun

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _miss(name: str, upstreams: tuple[Miss, ...] = ()) -> Miss:
    entry = sql_entry("SELECT 1", source="db")
    return Miss(
        planned_query(
            name,
            entry,
            ("s", name, "v"),
            upstreams=tuple(upstream.planned for upstream in upstreams),
        )
    )


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
        [_miss("down", (_miss("up"),)), _miss("up")],
        execute,
        settle,
        max_workers=4,
    )

    assert events == ["run:up", "settle:up", "run:down", "settle:down"]


def test_upstream_outside_the_misses_does_not_gate() -> None:
    outcomes: Mapping[str, Outcome] = run(
        [_miss("down", (_miss("cached"),))],
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


def test_a_fresh_warehouse_result_is_measured_once(
    local_project: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The producer's cut is the only one: nothing downstream re-measures it."""
    measured: list[int] = []

    def counting_take(*args: Any) -> Any:
        measured.append(1)
        return take(*args)

    for module in list(sys.modules.values()):
        if not module.__name__.startswith("dbt_charts."):
            continue
        for name, value in list(vars(module).items()):
            if value is take:
                monkeypatch.setattr(module, name, counting_take)
    env = make_exec_env(
        project=local_project(tmp_path),
        project_sources=ProjectSourcesConfig(
            sources={"db": {"type": "duckdb", "path": ":memory:"}}
        ),
        backends=BackendRegistry({"duckdb": DuckDBBackend()}),
    )
    board = compile_board(
        """
queries:
  q:
    sql: SELECT * FROM range(5)
    source: db
charts:
  c:
    query: q
    type: table
rows:
  - c
"""
    ).board
    assert board is not None

    rows = BoardRun(board, env=env, use_cache=False).execute_query("q")

    assert len(rows) == 5
    assert len(measured) == 1


def test_a_fresh_values_result_is_cut_to_the_ceiling(
    local_project: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A values query has no fetch to cut it, so its route does."""
    monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "2")
    board = compile_board(
        """
queries:
  q:
    columns: [n]
    values: [[1], [2], [3]]
charts:
  c:
    query: q
    type: table
rows:
  - c
"""
    ).board
    assert board is not None
    run = BoardRun(board, env=make_exec_env(project=local_project(tmp_path)))

    rows = run.execute_query("q", use_cache=False)

    assert rows == [{"n": 1}, {"n": 2}]
    assert run.ledger.truncations["q"].reason == "max_rows"


def test_a_fresh_schema_result_is_cut_to_the_ceiling(
    local_project: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A schema query is answered from metadata, with no fetch to cut it."""
    monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "1")
    memory = {"type": "duckdb", "path": ":memory:"}
    env = make_exec_env(
        project=local_project(tmp_path),
        project_sources=ProjectSourcesConfig(sources={"a": memory, "b": memory}),
    )
    board = compile_board(
        """
queries:
  q:
    type: schema
charts:
  c:
    query: q
    type: table
rows:
  - c
"""
    ).board
    assert board is not None
    run = BoardRun(board, env=env)

    rows = run.execute_query("q", use_cache=False)

    assert len(rows) == 1
    assert run.ledger.truncations["q"].reason == "max_rows"


def test_a_planned_values_result_is_cut_to_the_ceiling_not_the_callers_row_limit(
    local_project: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shared cache holds each upstream whole, so a caller's sample limit
    never cuts one."""
    monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "2")
    board = compile_board(
        """
queries:
  q:
    columns: [n]
    values: [[1], [2], [3]]
charts:
  c:
    query: q
    type: table
rows:
  - c
"""
    ).board
    assert board is not None
    ctx = BoardRun(board, env=make_exec_env(project=local_project(tmp_path))).ctx

    result = run_planned(
        ctx.env,
        ctx.plan,
        "q",
        source_config=None,
        row_limit=resolve_effective_row_limit(1),
    )

    assert result.data == [{"n": 1}, {"n": 2}]
    assert result.truncated_reason == "max_rows"
