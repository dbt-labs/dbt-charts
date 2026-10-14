"""The session every CDN-downloaded driver shares.

Only the parts that are not a driver's option map: how a statement and the Arrow
stream it owns are torn down, which is a memory-safety contract rather than a
behavior anyone can see until it bites.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import nanoarrow as na
import pyarrow as pa
import pytest

from dbt_charts.core.diagnostics.codes_execute import ERR_WAREHOUSE_RESULT_WARNING
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.adapters.base import (
    WarehouseStatementError,
    resolve_effective_row_limit,
)
from dbt_charts.core.execute.drivers._cdn import (
    CdnDriverConnection,
    _ConnectFailed,
    open_cdn_driver,
)

_WARNINGS_OPTION = "adbc.dbt.last_warnings"


class _FakeHandle:
    pass


class _FakeReader:
    """Stands in for the Arrow stream read from an ADBC stream handle."""

    def __init__(self, batches: list[pa.RecordBatch], closed: list[str]) -> None:
        self._schema = na.c_schema(batches[0].schema)
        self._batches = [na.c_array(batch) for batch in batches]
        self._closed = closed

    def get_schema(self) -> Any:
        return self._schema

    def __iter__(self) -> Any:
        yield from self._batches

    def release(self) -> None:
        self._closed.append("reader")


class _FakeStatement:
    def __init__(
        self,
        closed: list[str],
        update_error: Exception | None = None,
        warnings: str = "",
    ) -> None:
        self._closed = closed
        self._update_error = update_error
        self._warnings = warnings
        self.options_read: list[str] = []
        self.sql: str | None = None
        self.queries = 0
        self.updates = 0

    def set_sql_query(self, sql: str) -> None:
        self.sql = sql

    def execute_query(self) -> tuple[_FakeHandle, int]:
        self.queries += 1
        return _FakeHandle(), -1

    def get_option(self, key: str) -> str:
        self.options_read.append(key)
        return self._warnings

    def execute_update(self) -> int:
        self.updates += 1
        if self._update_error is not None:
            raise self._update_error
        return 0

    def close(self) -> None:
        self._closed.append("statement")


def _adbc_error(sqlstate: str | None) -> Exception:
    """A real `adbc_driver_manager.Error`, so the `except` clause under test is
    the one production takes."""
    from adbc_driver_manager import Error

    return Error("rejected", status_code=1, sqlstate=sqlstate)


def _readers_returning(reader: _FakeReader) -> Any:
    """A stand-in for `nanoarrow.c_array_stream` that yields *reader*."""
    return lambda _handle: reader


def _connection() -> Any:
    """The DBAPI connection a driver hands the session — only its
    `adbc_connection` is ever read."""
    return SimpleNamespace(adbc_connection=object())


def _batches(count: int, rows: int) -> list[pa.RecordBatch]:
    return [pa.RecordBatch.from_pydict({"n": list(range(rows))}) for _ in range(count)]


def _run(
    close_order: list[str],
    batches: list[pa.RecordBatch],
    limit_rows: int,
    warnings: str = "",
    warnings_option: str | None = None,
    statement: _FakeStatement | None = None,
) -> Any:
    reader = _FakeReader(batches, close_order)
    fake = statement or _FakeStatement(close_order, warnings=warnings)
    with (
        patch("adbc_driver_manager.AdbcStatement", lambda _conn: fake),
        patch("nanoarrow.c_array_stream", _readers_returning(reader)),
    ):
        connection = CdnDriverConnection(_connection(), warnings_option, [])
        return connection.execute("SELECT 1", resolve_effective_row_limit(limit_rows))


class TestSessionSql:
    """The path a board's `setup_sql` takes: a statement run for its effect on
    the session, with nothing to read back."""

    def test_a_session_statement_runs_and_returns_no_stream(self) -> None:
        """`execute_update`, not `execute_query`: a SET has no result set, and
        asking for one leaves an Arrow stream nobody releases."""
        close_order: list[str] = []
        statement = _FakeStatement(close_order)
        with patch("adbc_driver_manager.AdbcStatement", lambda _conn: statement):
            CdnDriverConnection(_connection(), None, []).session_sql(
                "SET statement_timeout = 30000"
            )
        assert statement.sql == "SET statement_timeout = 30000"
        assert statement.updates == 1
        assert statement.queries == 0
        assert close_order == ["statement"]

    def test_a_session_statement_is_sent_verbatim(self) -> None:
        """No query comment: this is connection setup, not a board's query, and
        a warehouse that rejects a leading comment on `SET` would fail every
        connect."""
        statement = _FakeStatement([])
        with patch("adbc_driver_manager.AdbcStatement", lambda _conn: statement):
            CdnDriverConnection(_connection(), None, []).session_sql("SET x = 1")
        assert statement.sql == "SET x = 1"

    def test_a_rejected_session_statement_carries_its_sqlstate(self) -> None:
        """So `classify_warehouse_error` reads a verdict rather than guessing,
        and `_ensure_connected` can tell a setup failure from a query one."""
        close_order: list[str] = []
        statement = _FakeStatement(close_order, update_error=_adbc_error("42601"))
        with (
            patch("adbc_driver_manager.AdbcStatement", lambda _conn: statement),
            pytest.raises(WarehouseStatementError) as excinfo,
        ):
            CdnDriverConnection(_connection(), None, []).session_sql("SET nope = 1")
        assert excinfo.value.sqlstate == "42601"
        assert close_order == ["statement"]

    def test_a_rejection_with_no_sqlstate_still_arrives_redacted(self) -> None:
        """A driver that reports none: the message is all there is, so it is
        what gets classified, and it is redacted like every other."""
        from adbc_driver_manager import Error

        secret = "fake-token-value-for-redaction"
        statement = _FakeStatement(
            [], update_error=Error(f"bad {secret}", status_code=1, sqlstate=None)
        )
        with (
            patch("adbc_driver_manager.AdbcStatement", lambda _conn: statement),
            pytest.raises(WarehouseStatementError) as excinfo,
        ):
            CdnDriverConnection(_connection(), None, [secret]).session_sql("SET x")
        assert excinfo.value.sqlstate is None
        assert secret not in str(excinfo.value)


class TestStatementOutlivesItsStream:
    """`AdbcStatementExecuteQuery`: "This AdbcStatement must outlive the returned
    ArrowArrayStream", and releasing an unconsumed stream is equivalent to
    cancelling it. Closing the statement first leaves the reader's release
    callback firing into freed memory — undefined behavior that a query
    happening to succeed does not disprove.
    """

    def test_the_reader_is_released_before_the_statement(self) -> None:
        close_order: list[str] = []
        _run(close_order, _batches(1, 2), 10)
        assert close_order == ["reader", "statement"]

    def test_ordering_holds_when_the_limit_stops_the_read_mid_stream(self) -> None:
        """The dangerous case: `read_bounded_table` breaks as soon as it has
        enough rows, so the stream is still in flight when the scope ends."""
        close_order: list[str] = []
        table = _run(close_order, _batches(5, 4), 6)
        assert len(table) < 20
        assert close_order == ["reader", "statement"]

    def test_the_statement_is_closed_even_when_the_read_raises(self) -> None:
        close_order: list[str] = []
        reader = _FakeReader(_batches(1, 1), close_order)
        with (
            patch(
                "adbc_driver_manager.AdbcStatement",
                lambda _conn: _FakeStatement(close_order),
            ),
            patch("nanoarrow.c_array_stream", _readers_returning(reader)),
            patch(
                "dbt_charts.core.execute.result.read_bounded_table",
                side_effect=RuntimeError("stream died"),
            ),
            pytest.raises(RuntimeError, match="stream died"),
        ):
            CdnDriverConnection(_connection(), None, []).execute(
                "SELECT 1", resolve_effective_row_limit(1)
            )
        assert close_order == ["reader", "statement"]


class TestBackendWarnings:
    """A warehouse that answers and attaches a warning (an export-limit
    truncation, say) has told us the rows are incomplete. The caller never holds
    a live statement, so the read happens here, before the statement closes."""

    def test_a_warning_raises_with_its_text_and_still_releases_in_order(self) -> None:
        close_order: list[str] = []
        with pytest.raises(ExecutionError) as excinfo:
            _run(
                close_order,
                _batches(1, 2),
                10,
                warnings="export limit reached",
                warnings_option=_WARNINGS_OPTION,
            )
        assert excinfo.value.code is ERR_WAREHOUSE_RESULT_WARNING
        assert "export limit reached" in str(excinfo.value)
        assert close_order == ["reader", "statement"]

    def test_no_warning_returns_the_table(self) -> None:
        table = _run(
            [], _batches(1, 2), 10, warnings="", warnings_option=_WARNINGS_OPTION
        )
        assert len(table) == 2

    def test_the_option_is_read_by_the_name_the_driver_row_gave(self) -> None:
        statement = _FakeStatement([])
        _run(
            [],
            _batches(1, 2),
            10,
            warnings_option=_WARNINGS_OPTION,
            statement=statement,
        )
        assert statement.options_read == [_WARNINGS_OPTION]

    def test_a_driver_with_no_warnings_channel_is_never_asked(self) -> None:
        statement = _FakeStatement([], warnings="ignored: nobody asked")
        _run([], _batches(1, 2), 10, warnings_option=None, statement=statement)
        assert statement.options_read == []


class TestOpening:
    def test_the_entry_point_comes_from_the_driver_row(self, tmp_path) -> None:
        """Lake compute's symbol is `AdbcDriverDbtInit`, not the default
        `AdbcDriverInit`; callers never name it."""
        with (
            patch(
                "dbt_charts.core.execute.drivers._fetch.driver_path",
                return_value=tmp_path / "lib.dylib",
            ),
            patch("adbc_driver_manager.dbapi.connect") as connect,
        ):
            open_cdn_driver("dbt", {"adbc.dbt.base_url": "https://x/"})
        assert connect.call_args.kwargs["entrypoint"] == "AdbcDriverDbtInit"
        assert connect.call_args.kwargs["db_kwargs"] == {
            "adbc.dbt.base_url": "https://x/"
        }
        assert connect.call_args.kwargs["autocommit"] is True

    def test_a_connect_failure_never_quotes_a_credential_back(self, tmp_path) -> None:
        """The failing driver is what holds the unredacted text, so the exception
        is raised without a chained cause that a default traceback would print."""
        secret = "fake-token-value-for-redaction"
        with (
            patch(
                "dbt_charts.core.execute.drivers._fetch.driver_path",
                return_value=tmp_path / "lib.dylib",
            ),
            patch(
                "adbc_driver_manager.dbapi.connect",
                side_effect=RuntimeError(f"401 for bearer {secret}"),
            ),
            pytest.raises(_ConnectFailed) as excinfo,
        ):
            open_cdn_driver(
                "dbt",
                {"adbc.dbt.auth.token": secret, "adbc.dbt.base_url": "https://x/"},
            )
        _assert_no_secret_in_chain(excinfo.value, secret)


def _assert_no_secret_in_chain(error: BaseException | None, secret: str) -> None:
    """Every exception reachable from *error* — it, its cause, its context."""
    seen: set[int] = set()
    pending = [error]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        assert secret not in str(current)
        pending += [current.__cause__, current.__context__]


class TestStatementTimeRedaction:
    def test_a_credential_quoted_in_a_statement_failure_is_gone_from_the_chain(
        self,
    ) -> None:
        """A driver that authenticates lazily fails at the first statement, and
        its message may quote the token."""
        from adbc_driver_manager import Error

        secret = "fake-token-value-for-redaction"
        failure = Error(f"401 for bearer {secret}", status_code=1, sqlstate="28000")
        statement = _FakeStatement([], update_error=failure)
        with (
            patch("adbc_driver_manager.AdbcStatement", lambda _conn: statement),
            pytest.raises(WarehouseStatementError) as excinfo,
        ):
            CdnDriverConnection(_connection(), None, [secret]).session_sql("SELECT 1")
        assert excinfo.value.sqlstate == "28000"
        _assert_no_secret_in_chain(excinfo.value, secret)


class TestCancel:
    def test_cancel_reaches_the_statement_that_is_running(self) -> None:
        """`cancel()` is called from another thread while `execute` is blocked in
        the driver, so it must find the live statement."""
        import threading

        entered, released = threading.Event(), threading.Event()
        cancelled: list[bool] = []

        class _Blocking(_FakeStatement):
            def execute_query(self) -> tuple[_FakeHandle, int]:
                entered.set()
                released.wait(10)
                raise RuntimeError("cancelled")

            def cancel(self) -> None:
                cancelled.append(True)
                released.set()

        statement = _Blocking([])
        connection = CdnDriverConnection(_connection(), None, [])
        with patch("adbc_driver_manager.AdbcStatement", lambda _conn: statement):
            errors: list[BaseException] = []

            def _run_query() -> None:
                try:
                    connection.execute("SELECT 1", resolve_effective_row_limit(1))
                except RuntimeError as error:
                    errors.append(error)

            thread = threading.Thread(target=_run_query, daemon=True)
            thread.start()
            assert entered.wait(10)
            connection.cancel()
            thread.join(10)
        assert cancelled == [True]
        assert len(errors) == 1

    def test_cancel_with_nothing_running_does_nothing(self) -> None:
        CdnDriverConnection(_connection(), None, []).cancel()
