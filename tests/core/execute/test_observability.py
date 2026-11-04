"""Tests for notify_observers and AdapterRegistry injected-observer integration.

Pins:
- notify_observers fires each observer with normalized warehouse_type
- None warehouse_type is normalized to "unknown"
- a raising observer is isolated; others still fire
- AdapterRegistry(observers=[...]).execute calls observer once per real adapter call
- no-adapter short-circuit does not call observers
- error path yields status "error"
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
from dbt_charts.core.execute.backends.base import BackendRegistry
from dbt_charts.core.execute.backends.duckdb_local import DuckDBBackend
from dbt_charts.core.execute.observability import WarehouseObserver, notify_observers

_TEST_DB_SOURCES = ProjectSourcesConfig(
    sources={"test_db": {"type": "duckdb", "path": ":memory:"}}
)


def _duckdb_select_one_query() -> SqlQuery:
    """Minimal valid DuckDB query that returns exactly one row."""
    return SqlQuery(sql="SELECT 1 AS n", source="test_db")


def test_notify_observers_none_warehouse_type_normalized_to_unknown() -> None:
    calls: list[tuple[str, str, float]] = []
    notify_observers([lambda wt, s, d: calls.append((wt, s, d))], None, "success", 0.5)
    assert len(calls) == 1
    assert calls[0][0] == "unknown"


def test_notify_observers_calls_each_observer() -> None:
    calls_a: list[str] = []
    calls_b: list[str] = []

    def obs_a(wt: str, s: str, d: float) -> None:
        calls_a.append(wt)

    def obs_b(wt: str, s: str, d: float) -> None:
        calls_b.append(wt)

    notify_observers([obs_a, obs_b], "duckdb", "success", 0.1)
    assert calls_a == ["duckdb"]
    assert calls_b == ["duckdb"]


def test_notify_observers_raising_observer_does_not_break_others() -> None:
    calls: list[str] = []

    def raiser(wt: str, s: str, d: float) -> None:
        raise RuntimeError("boom")

    def good(wt: str, s: str, d: float) -> None:
        calls.append(wt)

    # raiser fires first — good must still fire
    notify_observers([raiser, good], "snowflake", "success", 0.2)
    assert calls == ["snowflake"]


def test_execute_invokes_observer_with_profile_type_status_and_duration(
    tmp_path,
    local_project: Callable[..., FilesystemProject],
) -> None:
    calls: list[tuple[str, str, float]] = []

    def capture(warehouse_type: str, status: str, duration: float) -> None:
        calls.append((warehouse_type, status, duration))

    registry = AdapterRegistry(
        project=local_project(tmp_path),
        project_sources=_TEST_DB_SOURCES,
        observers=[capture],
        backends=BackendRegistry({"duckdb": DuckDBBackend()}),
    )
    result = registry.execute(_duckdb_select_one_query())

    assert result.error is None
    assert len(calls) == 1
    warehouse_type, status, duration = calls[0]
    assert warehouse_type == "duckdb"
    assert status == "success"
    assert duration >= 0.0


def test_no_adapter_short_circuit_does_not_call_observer(
    local_project: Callable[..., FilesystemProject],
) -> None:
    calls: list[tuple[str, str, float]] = []
    registry = AdapterRegistry(
        project=local_project(Path()),
        observers=[lambda wt, s, d: calls.append((wt, s, d))],
    )
    # No adapters registered — execute will short-circuit
    result = registry.execute(_duckdb_select_one_query())

    assert result.error is not None
    assert len(calls) == 0


def test_observer_status_is_error_on_failed_result(
    tmp_path, local_project: Callable[..., FilesystemProject]
) -> None:
    calls: list[tuple[str, str, float]] = []
    registry = AdapterRegistry(
        project=local_project(tmp_path),
        project_sources=_TEST_DB_SOURCES,
        observers=[lambda wt, s, d: calls.append((wt, s, d))],
        backends=BackendRegistry({"duckdb": DuckDBBackend()}),
    )
    # A query that will fail at execution time
    bad_query = SqlQuery(sql="SELECT * FROM nonexistent_table_xyz", source="test_db")
    result = registry.execute(bad_query)

    assert result.error is not None
    assert len(calls) == 1
    _, status, _ = calls[0]
    assert status == "error"


def test_observer_type_alias_is_callable() -> None:
    """WarehouseObserver type alias is importable and usable as annotation."""
    obs: WarehouseObserver = lambda wt, s, d: None  # noqa: E731
    assert callable(obs)


def test_preparation_failure_is_reported_to_observers(
    tmp_path, local_project: Callable[..., FilesystemProject]
) -> None:
    calls: list[tuple[str, str, float]] = []
    registry = AdapterRegistry(
        project=local_project(tmp_path),
        project_sources=_TEST_DB_SOURCES,
        observers=[lambda wt, s, d: calls.append((wt, s, d))],
        backends=BackendRegistry({"duckdb": DuckDBBackend()}),
    )

    result = registry.execute(SqlQuery(sql="DROP TABLE t", source="test_db"))

    assert result.error is not None
    assert [status for _, status, _ in calls] == ["error"]
