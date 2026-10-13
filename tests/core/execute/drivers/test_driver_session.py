"""A driver source runs through `SqlAdapter` on a pooled driver connection.

The dbt path is not exercised here, and these tests never let it run: a
`build_adapter` that raises is patched in for every one of them, so a driver
source that fell through to dbt would fail loudly instead of passing by accident.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import get_execution_config
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import LakeComputeTokenSource
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_BINDER_UNKNOWN_RELATION,
    ERR_WAREHOUSE_CONNECTION,
    ERR_WAREHOUSE_RESULT_WARNING,
    ERR_WAREHOUSE_RUNTIME,
)
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.dialects.lakecompute import LakeComputeDialect
from dbt_charts.core.execute.adapters.base import (
    RowFetchLimit,
    WarehouseStatementError,
    resolve_effective_row_limit,
)
from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
    AdapterNotInstalledError,
)
from dbt_charts.core.execute.adapters.sql_adapter import PreparedSql, SqlAdapter

_OPEN = "dbt_charts.core.execute.drivers.lakecompute.open_lakecompute"
_CDN = "dbt_charts.core.execute.drivers.lakecompute.open_cdn_driver"
_TABLE = pa.table({"Month": [1, 2], "n": [10, 20]})


class _Connection:
    """A `DriverConnection` whose answers a test scripts per call."""

    def __init__(self, *outcomes: Any) -> None:
        self._outcomes = list(outcomes)
        self.statements: list[str] = []
        self.session_statements: list[str] = []
        self.closed = False

    def execute(self, sql: str, limit: Any) -> pa.Table:
        self.statements.append(sql)
        outcome = self._outcomes.pop(0) if self._outcomes else _TABLE
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def session_sql(self, sql: str) -> None:
        self.session_statements.append(sql)

    def cancel(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _dbt_path_is_forbidden() -> Generator[None]:
    with patch(
        "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter",
        side_effect=AssertionError("a driver source must never build a dbt adapter"),
    ):
        yield


@pytest.fixture
def adapter(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> Generator[SqlAdapter]:
    sql_adapter = SqlAdapter(
        project=local_project(tmp_path),
        dbt_project_path=None,
        profile_type="duckdb",
        max_workers=1,
    )
    yield sql_adapter
    sql_adapter.close()


def _source(**fields: Any) -> LakeComputeTokenSource:
    return LakeComputeTokenSource(type="lakecompute", token="t", **fields)


def _prepared(
    sql: str = 'SELECT 1 AS "Month"', setup_sql: str | None = None
) -> PreparedSql:
    return PreparedSql(
        sql=sql,
        setup_sql=setup_sql,
        dialect_name="lakecompute",
        row_fetch_limit=resolve_effective_row_limit(100),
    )


def _run(adapter: SqlAdapter, prepared: PreparedSql, source: Any = None) -> Any:
    query = SqlQuery(sql=prepared.sql, source="lake")
    return adapter.execute_prepared(prepared, query, source or _source())


@contextmanager
def _opened(*connections: _Connection) -> Generator[MagicMock]:
    with patch(_OPEN, side_effect=list(connections)) as opener:
        yield opener


def test_rows_come_back_with_the_case_the_query_gave_them(adapter: SqlAdapter) -> None:
    connection = _Connection(_TABLE)
    with _opened(connection):
        result = _run(adapter, _prepared())
    assert result.error is None
    assert result.columns == ["Month", "n"]
    assert result.data == [{"Month": 1, "n": 10}, {"Month": 2, "n": 20}]
    assert connection.statements == ['SELECT 1 AS "Month"']


def test_the_ceiling_overfetch_is_sliced_and_flagged(adapter: SqlAdapter) -> None:
    table = pa.table({"a": list(range(11))})
    prepared = PreparedSql(
        sql="SELECT a FROM t",
        setup_sql=None,
        dialect_name="lakecompute",
        row_fetch_limit=RowFetchLimit(
            fetch_limit=11, effective_limit=10, ceiling_binding=True
        ),
    )
    with _opened(_Connection(table)):
        result = _run(adapter, prepared)
    assert len(result.data) == 10
    assert result.truncated_reason == "max_rows"


def test_ordinary_queries_reuse_one_session(adapter: SqlAdapter) -> None:
    connection = _Connection()
    with _opened(connection) as opener:
        _run(adapter, _prepared())
        _run(adapter, _prepared())
    assert opener.call_count == 1
    assert len(connection.statements) == 2


def test_setup_sql_runs_first_and_the_next_query_gets_a_fresh_session(
    adapter: SqlAdapter,
) -> None:
    """Lake compute has no statement that clears session objects, so the pool
    drops the connection rather than let a temp object outlive its board."""
    first, second = _Connection(), _Connection()
    with _opened(first, second) as opener:
        _run(adapter, _prepared(setup_sql="CREATE TEMP TABLE t AS SELECT 1"))
        _run(adapter, _prepared())
    assert first.session_statements == ["CREATE TEMP TABLE t AS SELECT 1"]
    assert first.closed
    assert opener.call_count == 2
    assert second.session_statements == []


def test_the_source_timeout_reaches_the_driver_as_its_own_option(
    adapter: SqlAdapter,
) -> None:
    with patch(_CDN) as open_cdn:
        open_cdn.return_value = _Connection()
        _run(adapter, _prepared(), _source(max_query_duration_seconds=45))
    assert open_cdn.call_args.args[1]["adbc.dbt.timeout_seconds"] == "45"


def test_a_source_with_no_cap_gets_the_projects_default(adapter: SqlAdapter) -> None:
    """Resolved by the same function the dbt pool uses, so the number the driver
    enforces is the number a timeout error would report."""
    with patch(_CDN) as open_cdn:
        open_cdn.return_value = _Connection()
        _run(adapter, _prepared())
    expected = str(get_execution_config().max_query_duration_seconds)
    assert open_cdn.call_args.args[1]["adbc.dbt.timeout_seconds"] == expected


def test_a_rejected_query_on_a_live_session_is_not_retried(adapter: SqlAdapter) -> None:
    """The warehouse read it and refused it; re-sending fails identically, and the
    reconnect would throw away a warm session to do it."""
    connection = _Connection(WarehouseStatementError("42S02", "no such table"))
    with _opened(connection) as opener:
        result = _run(adapter, _prepared())
    assert opener.call_count == 1
    assert result.error.code is ERR_BINDER_UNKNOWN_RELATION
    assert len(connection.statements) == 2  # the query, then the liveness probe


def test_a_dead_session_is_replaced_and_the_query_retried_once(
    adapter: SqlAdapter,
) -> None:
    dead = _Connection(RuntimeError("socket closed"), RuntimeError("socket closed"))
    fresh = _Connection(_TABLE)
    with _opened(dead, fresh) as opener:
        result = _run(adapter, _prepared())
    assert result.error is None
    assert opener.call_count == 2
    assert dead.closed
    assert result.data[0]["Month"] == 1


def test_an_unclassified_failure_surfaces_as_a_warehouse_error(
    adapter: SqlAdapter,
) -> None:
    connection = _Connection(RuntimeError("boom"))
    with _opened(connection):
        result = _run(adapter, _prepared())
    assert result.error.code is ERR_WAREHOUSE_RUNTIME


def test_a_backend_warning_is_the_error_not_a_silently_short_result(
    adapter: SqlAdapter,
) -> None:
    from dbt_charts.core.diagnostics.codes_execute import (
        ERR_WAREHOUSE_RESULT_WARNING as code,
    )

    warning = ExecutionError.from_code(code, warnings="export limit reached")
    with _opened(_Connection(warning)):
        result = _run(adapter, _prepared())
    assert result.error.code is ERR_WAREHOUSE_RESULT_WARNING
    assert "export limit reached" in str(result.error)


def test_a_connect_failure_is_a_connection_error_not_a_query_rejection(
    adapter: SqlAdapter,
) -> None:
    with patch(_OPEN, side_effect=RuntimeError("could not reach the API")):
        result = _run(adapter, _prepared())
    assert result.error.code is ERR_WAREHOUSE_CONNECTION


def test_a_missing_extra_reports_its_install_command(adapter: SqlAdapter) -> None:
    from dbt_charts.core.diagnostics.codes_execute import ERR_ADAPTER_NOT_INSTALLED

    missing = AdapterNotInstalledError.from_code(
        ERR_ADAPTER_NOT_INSTALLED,
        adapter_type="lakecompute",
        package="adbc-driver-manager",
        install='pip install "dbt-charts[lakecompute]"',
    )
    with patch(_OPEN, side_effect=missing):
        result = _run(adapter, _prepared())
    assert "dbt-charts[lakecompute]" in str(result.error)


def test_close_releases_every_open_session(adapter: SqlAdapter) -> None:
    connection = _Connection()
    with _opened(connection):
        _run(adapter, _prepared())
        adapter.close()
    assert connection.closed


def _checksum_mismatch() -> ExecutionError:
    from dbt_charts.core.diagnostics.codes_execute import ERR_DRIVER_CHECKSUM_MISMATCH

    return ExecutionError.from_code(
        ERR_DRIVER_CHECKSUM_MISMATCH,
        subject="https://cdn/x",
        actual="a",
        expected="b",
        outcome="Discarded.",
    )


_DRIVER_PATH = "dbt_charts.core.execute.drivers._fetch.driver_path"


def test_a_driver_download_verdict_keeps_its_own_code(adapter: SqlAdapter) -> None:
    """A checksum mismatch can mean a tampered binary; it must not read as a
    warehouse connectivity problem."""
    from dbt_charts.core.diagnostics.codes_execute import ERR_DRIVER_CHECKSUM_MISMATCH

    with patch(_DRIVER_PATH, side_effect=_checksum_mismatch()):
        result = _run(adapter, _prepared())
    assert result.error.code is ERR_DRIVER_CHECKSUM_MISMATCH


def test_test_connection_surfaces_a_driver_download_verdict() -> None:
    from dbt_charts.core.connections import test_connection as run_test_connection
    from dbt_charts.core.diagnostics.codes_execute import ERR_DRIVER_CHECKSUM_MISMATCH

    with (
        patch(_DRIVER_PATH, side_effect=_checksum_mismatch()),
        pytest.raises(ExecutionError) as excinfo,
    ):
        run_test_connection(_source())
    assert excinfo.value.code is ERR_DRIVER_CHECKSUM_MISMATCH


def test_bulk_schema_surfaces_a_driver_download_verdict() -> None:
    from dbt_charts.core.connections import bulk_schema_for_config
    from dbt_charts.core.diagnostics.codes_execute import ERR_DRIVER_CHECKSUM_MISMATCH

    with (
        patch(_DRIVER_PATH, side_effect=_checksum_mismatch()),
        pytest.raises(ExecutionError) as excinfo,
    ):
        bulk_schema_for_config(_source())
    assert excinfo.value.code is ERR_DRIVER_CHECKSUM_MISMATCH


class _TimeoutDialect(LakeComputeDialect):
    """Answers True to the timeout question, as a dialect that knows the SQLSTATE would."""

    def is_statement_timeout_error(self, exc: Exception) -> bool:
        return True


def test_a_dialect_recognized_timeout_becomes_the_duration_error(
    adapter: SqlAdapter,
) -> None:
    from dbt_charts.core.diagnostics.codes_execute import ERR_QUERY_DURATION_EXCEEDED

    connection = _Connection(RuntimeError("cancelled"))
    with (
        _opened(connection),
        patch(
            "dbt_charts.core.execute.adapters.sql_adapter.get_dialect",
            return_value=_TimeoutDialect(),
        ),
    ):
        result = _run(adapter, _prepared(), _source(max_query_duration_seconds=7))
    assert result.error.code is ERR_QUERY_DURATION_EXCEEDED
    assert "7s" in str(result.error)


class _BlockingConnection(_Connection):
    """A statement that blocks in `execute` until `cancel()` is called."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.cancelled = threading.Event()

    def execute(self, sql: str, limit: Any) -> pa.Table:
        self.entered.set()
        assert self.cancelled.wait(10), "interrupt() never cancelled the statement"
        raise RuntimeError("statement cancelled")

    def cancel(self) -> None:
        self.cancelled.set()


class TestInterrupt:
    def test_interrupt_cancels_an_in_flight_driver_statement(
        self, adapter: SqlAdapter
    ) -> None:
        from dbt_charts.core.diagnostics.codes_execute import ERR_QUERY_INTERRUPTED

        connection = _BlockingConnection()
        out: list[Any] = []
        with _opened(connection):
            thread = threading.Thread(
                target=lambda: out.append(_run(adapter, _prepared())), daemon=True
            )
            thread.start()
            assert connection.entered.wait(10)
            adapter.interrupt()
            thread.join(10)
        assert not thread.is_alive(), "query kept running after interrupt"
        assert out[0].error.code is ERR_QUERY_INTERRUPTED

    def test_an_interrupted_adapter_refuses_new_driver_queries(
        self, adapter: SqlAdapter
    ) -> None:
        from dbt_charts.core.diagnostics.codes_execute import ERR_QUERY_INTERRUPTED

        adapter.interrupt()
        with _opened(_Connection()) as opener:
            result = _run(adapter, _prepared())
        assert result.error.code is ERR_QUERY_INTERRUPTED
        opener.assert_not_called()
