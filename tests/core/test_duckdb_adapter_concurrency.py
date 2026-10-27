"""Concurrency regression tests for DuckDBAdapter.

The render pipeline submits chart queries concurrently (see
`execute_queries_parallel`). Queries with no named source share ONE default
DuckDB connection (`self._connection`). ``conn.execute()`` returns the
connection itself, so ``result.description`` reflects whichever query last ran
on that shared connection — it is only valid while `_DUCKDB_CWD_LOCK` is held.

Regression guard: column metadata read from ``result.description`` *after*
the lock was released let a concurrent query overwrite it (wrong column types)
or close the result handle ("Invalid Input Error: result closed" / DuckDB ``bad_weak_ptr``). This test
hammers the shared default connection with two queries that return distinct
column sets and asserts every result's columns match its own query.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import DuckDBSourceConfig
from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

# Large result sets widen the window between fetch and the (formerly
# out-of-lock) description read, so the race surfaces deterministically instead
# of only under rare CI timing.
_ROWS = 50_000
_ITERATIONS = 120
_WORKERS = 16

_Q_AB = f"SELECT i AS a, i AS b FROM range({_ROWS}) t(i)"
_Q_CDE = f"SELECT i AS c, i AS d, i AS e FROM range({_ROWS}) t(i)"


def test_concurrent_queries_keep_their_own_schema() -> None:
    """Concurrent queries on the shared default connection must not cross wires."""
    adapter = DuckDBAdapter(
        source_config=DuckDBSourceConfig(type="duckdb", path=":memory:"),
    )
    errors: list[str] = []

    def run(sql: str, expected: set[str]) -> None:
        result = adapter.execute(SqlQuery(sql=sql))
        if result.error:
            errors.append(result.error)
            return
        assert set(result.columns or []) == expected

    try:
        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            futures = [
                (
                    pool.submit(run, _Q_AB, {"a", "b"})
                    if i % 2 == 0
                    else pool.submit(run, _Q_CDE, {"c", "d", "e"})
                )
                for i in range(_ITERATIONS)
            ]
            for future in futures:
                future.result()
    finally:
        adapter.close()

    assert not errors, f"DuckDB execution errors under concurrency: {errors[:3]}"
