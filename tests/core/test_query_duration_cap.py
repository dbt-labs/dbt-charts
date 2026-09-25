"""Tests for the per-source query-duration cap (server-side statement timeout).

Covers: the config field + its per-source override resolution, the SQL each
dialect emits, and the end-to-end SqlAdapter path — including a live-Postgres
test that proves the cancellation happens in the warehouse, not the client.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import (
    get_execution_config,
    resolve_max_query_duration_seconds,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.diagnostics.codes_execute import ERR_QUERY_DURATION_EXCEEDED
from dbt_charts.core.execute.adapters.base import resolve_effective_row_limit
from dbt_charts.core.execute.adapters.sql_adapter import PreparedSql, SqlAdapter

_BUILD_ADAPTER = "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter"


def _prepared(sql: str, dialect: str) -> PreparedSql:
    """What prepare_sql hands back for a param-free query — these tests drive
    the send half, which takes the already-prepared SQL."""
    return PreparedSql(
        sql=sql,
        setup_sql=None,
        dialect_name=dialect,
        row_fetch_limit=resolve_effective_row_limit(None),
    )


def _sql_adapter(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    profile_type: str = "postgres",
) -> SqlAdapter:
    return SqlAdapter(
        project=local_project(tmp_path),
        dbt_project_path=None,
        profile_type=profile_type,
    )


def _mock_dbt_adapter(execute_side_effect: Any) -> MagicMock:
    adapter = MagicMock()
    adapter.connection_named.return_value.__enter__ = lambda self: None
    adapter.connection_named.return_value.__exit__ = MagicMock(return_value=False)
    adapter.execute.side_effect = execute_side_effect
    return adapter


class TestExecutionConfigField:
    def test_max_query_duration_seconds_is_positive_int(self) -> None:
        cfg = get_execution_config()
        assert isinstance(cfg.max_query_duration_seconds, int)
        assert cfg.max_query_duration_seconds > 0

    def test_zero_raises_validation_error(self) -> None:
        from dbt_charts.core.compile.models.config import ExecutionConfig

        with pytest.raises(ValidationError, match="greater than 0"):
            ExecutionConfig.model_validate(
                {
                    "max_workers": 4,
                    "max_query_duration_seconds": 0,
                    "dialect_aliases": {},
                }
            )

    def test_negative_raises_validation_error(self) -> None:
        from dbt_charts.core.compile.models.config import ExecutionConfig

        with pytest.raises(ValidationError, match="greater than 0"):
            ExecutionConfig.model_validate(
                {
                    "max_workers": 4,
                    "max_query_duration_seconds": -30,
                    "dialect_aliases": {},
                }
            )


class TestResolveMaxQueryDurationSeconds:
    def test_global_default_when_no_source_config(self) -> None:
        assert (
            resolve_max_query_duration_seconds(None)
            == get_execution_config().max_query_duration_seconds
        )

    def test_global_default_when_source_config_key_absent(self) -> None:
        assert (
            resolve_max_query_duration_seconds({"type": "postgres"})
            == get_execution_config().max_query_duration_seconds
        )

    def test_source_override_wins(self) -> None:
        source_config = {"type": "postgres", "max_query_duration_seconds": 7}
        assert resolve_max_query_duration_seconds(source_config) == 7

    def test_source_config_present_but_unset_falls_back_to_global(self) -> None:
        """A typed SourceConfig.model_dump() where the field wasn't authored
        dumps the key with value None — must fall back, not pass None through.
        """
        source_config = {"type": "postgres", "max_query_duration_seconds": None}
        assert (
            resolve_max_query_duration_seconds(source_config)
            == get_execution_config().max_query_duration_seconds
        )

    def test_zero_override_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="must be > 0"):
            resolve_max_query_duration_seconds(
                {"type": "postgres", "max_query_duration_seconds": 0}
            )

    def test_negative_override_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="must be > 0"):
            resolve_max_query_duration_seconds(
                {"type": "postgres", "max_query_duration_seconds": -5}
            )

    def test_non_integer_override_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="positive integer"):
            resolve_max_query_duration_seconds(
                {"type": "postgres", "max_query_duration_seconds": "bad"}
            )


class TestStatementTimeoutInjection:
    """The dialect-native timeout SQL is sent once at connection setup so
    the warehouse enforces a per-statement cap for all queries on that
    connection — a session-scoped SET persists for the life of the
    connection, making once-per-connection sufficient and correct."""

    def _captured_setup_sql(
        self,
        source_config: dict[str, Any],
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> str:
        """Run one query through the pool and return the first SQL sent to
        the adapter (which is the connection-setup timeout statement)."""
        table = MagicMock()
        table.column_names = ["x"]
        table.rows = [(1,)]
        captured: list[str] = []

        def fake_execute(sql: str, **kwargs: object) -> tuple | None:
            captured.append(sql)
            if kwargs.get("fetch"):
                return (None, table)
            return None  # setup call; return value is ignored

        adapter = _mock_dbt_adapter(fake_execute)
        sql_adapter = _sql_adapter(tmp_path, local_project, source_config["type"])
        query = SqlQuery(sql="SELECT 1 AS x", source=source_config["type"])

        with patch(_BUILD_ADAPTER, return_value=adapter):
            sql_adapter._execute_via_dbt_adapter(
                _prepared("SELECT 1 AS x", source_config["type"]), query, source_config
            )

        assert captured, "no SQL reached the driver"
        # captured[0] is the connection-setup timeout statement;
        # captured[1] is the actual data query.
        return captured[0]

    def test_postgres_gets_statement_timeout(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        source_config = {
            "type": "postgres",
            "host": "h",
            "dbname": "db",
            "user": "u",
            "password": "p",
            "max_query_duration_seconds": 5,
        }
        sql = self._captured_setup_sql(source_config, tmp_path, local_project)
        assert sql == "SET statement_timeout = '5s'"

    def test_snowflake_gets_statement_timeout(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        source_config = {
            "type": "snowflake",
            "account": "xy",
            "user": "u",
            "database": "db",
            "warehouse": "wh",
            "max_query_duration_seconds": 9,
        }
        sql = self._captured_setup_sql(source_config, tmp_path, local_project)
        assert sql == "ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 9"

    def test_per_source_override_beats_global_default(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """A distinct per-source value must reach the emitted SQL, not the
        global default — proves the override actually wins the cascade.
        """
        global_default = get_execution_config().max_query_duration_seconds
        distinctive = global_default + 37
        source_config = {
            "type": "postgres",
            "host": "h",
            "dbname": "db",
            "user": "u",
            "password": "p",
            "max_query_duration_seconds": distinctive,
        }
        sql = self._captured_setup_sql(source_config, tmp_path, local_project)
        assert f"SET statement_timeout = '{distinctive}s'" in sql

    def test_no_override_falls_back_to_global_default(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        source_config = {
            "type": "postgres",
            "host": "h",
            "dbname": "db",
            "user": "u",
            "password": "p",
        }
        sql = self._captured_setup_sql(source_config, tmp_path, local_project)
        global_default = get_execution_config().max_query_duration_seconds
        assert f"SET statement_timeout = '{global_default}s'" in sql


class TestQueryDurationExceeded:
    """When the warehouse raises a statement-timeout error, the adapter
    reclassifies it as a clean duration-exceeded error and does not retry —
    the connection is healthy (only the statement was cancelled), so retrying
    would just burn the same timeout window again."""

    def test_returns_clean_error_with_source_and_seconds(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        def fake_execute(sql: str, **kwargs: object) -> tuple | None:
            if kwargs.get("fetch"):
                raise RuntimeError("canceling statement due to statement timeout")
            return None  # setup call succeeds

        adapter = _mock_dbt_adapter(fake_execute)
        source_config = {
            "type": "postgres",
            "host": "h",
            "dbname": "db",
            "user": "u",
            "password": "p",
            "max_query_duration_seconds": 5,
        }
        sql_adapter = _sql_adapter(tmp_path, local_project, "postgres")
        query = SqlQuery(sql="SELECT pg_sleep(5)", source="mypg")

        with patch(_BUILD_ADAPTER, return_value=adapter):
            result = sql_adapter._execute_via_dbt_adapter(
                _prepared("SELECT pg_sleep(5)", "postgres"), query, source_config
            )

        assert result.error is not None
        assert result.error.code is ERR_QUERY_DURATION_EXCEEDED
        assert "max_query_duration_seconds=5s" in str(result.error)
        assert "'mypg'" in str(result.error)

    def test_does_not_retry_after_duration_exceeded(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Retrying would just burn the same timeout window a second time —
        the connection is healthy, only the statement was cancelled."""
        data_calls: list[int] = []

        def fake_execute(sql: str, **kwargs: object) -> tuple | None:
            if kwargs.get("fetch"):
                data_calls.append(1)
                raise RuntimeError("canceling statement due to statement timeout")
            return None  # setup call succeeds

        adapter = _mock_dbt_adapter(fake_execute)
        source_config = {
            "type": "postgres",
            "host": "h",
            "dbname": "db",
            "user": "u",
            "password": "p",
            "max_query_duration_seconds": 5,
        }
        sql_adapter = _sql_adapter(tmp_path, local_project, "postgres")
        query = SqlQuery(sql="SELECT pg_sleep(5)", source="mypg")

        with patch(_BUILD_ADAPTER, return_value=adapter):
            sql_adapter._execute_via_dbt_adapter(
                _prepared("SELECT pg_sleep(5)", "postgres"), query, source_config
            )

        assert len(data_calls) == 1

    def test_dropped_connection_still_retries_once(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A dropped connection is not misclassified as a duration-exceeded error,
        and keeps the drop-and-retry-once behavior.

        The adapter answers nothing until its connections are cleaned up, which is
        what a dropped session actually looks like: the liveness check that decides
        whether to retry fails on it too. A mock that failed only the first call
        would report a *live* connection and prove the opposite of this test."""
        table = MagicMock()
        table.column_names = ["x"]
        table.rows = [(1,)]
        data_calls: list[int] = []

        def fake_execute(sql: str, **kwargs: object) -> tuple | None:
            if not kwargs.get("fetch"):
                return None  # setup calls succeed
            data_calls.append(1)
            if not adapter.connections.cleanup_all.called:
                raise RuntimeError("connection reset")
            return (None, table)

        adapter = _mock_dbt_adapter(fake_execute)
        source_config = {
            "type": "postgres",
            "host": "h",
            "dbname": "db",
            "user": "u",
            "password": "p",
            # Generous cap — the connection-reset error is not a statement timeout.
            "max_query_duration_seconds": 120,
        }
        sql_adapter = _sql_adapter(tmp_path, local_project, "postgres")
        query = SqlQuery(sql="SELECT 1", source="mypg")

        with patch(_BUILD_ADAPTER, return_value=adapter):
            result = sql_adapter._execute_via_dbt_adapter(
                _prepared("SELECT 1", "postgres"), query, source_config
            )

        assert result.error is None
        # original + liveness check + one retry on the rebuilt connection
        assert len(data_calls) == 3

    def test_non_timeout_error_not_reclassified(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """An error whose message does not contain 'statement timeout' is never
        reclassified — it stays a generic warehouse error and hits the retry path."""
        calls: list[int] = []

        def fake_execute(sql: str, **kwargs: object) -> tuple | None:
            if not kwargs.get("fetch"):
                return None  # setup calls succeed
            calls.append(1)
            raise RuntimeError("authentication failed")

        adapter = _mock_dbt_adapter(fake_execute)
        source_config = {
            "type": "postgres",
            "host": "h",
            "dbname": "db",
            "user": "u",
            "password": "p",
            "max_query_duration_seconds": 5,
        }
        sql_adapter = _sql_adapter(tmp_path, local_project, "postgres")
        query = SqlQuery(sql="SELECT 1", source="mypg")

        with patch(_BUILD_ADAPTER, return_value=adapter):
            result = sql_adapter._execute_via_dbt_adapter(
                _prepared("SELECT 1", "postgres"), query, source_config
            )

        assert (
            result.error is None or result.error.code is not ERR_QUERY_DURATION_EXCEEDED
        )
        # original + liveness check (which fails too, so the session is treated as
        # dead) + one retry after drop-and-reconnect.
        assert len(calls) == 3


class TestDuckDBAndSqliteNoOp:
    """DuckDB and SQLite are local file databases with no server to enforce a
    timeout against — SqlAdapter never even routes them through the
    statement-timeout-emitting pool (DuckDB uses DuckDBAdapter; SQLite uses
    the stdlib sqlite3 path in _execute_sqlite), so no timeout SQL is ever
    built for either."""

    def test_duckdb_dialect_statement_timeout_is_noop(self) -> None:
        from dbt_charts.core.dialects import get_dialect

        assert get_dialect("duckdb").statement_timeout_sql(30) is None

    def test_sqlite_dialect_statement_timeout_is_noop(self) -> None:
        from dbt_charts.core.dialects import get_dialect

        assert get_dialect("sqlite").statement_timeout_sql(30) is None


class TestLivePostgresStatementTimeout:
    """Proves the cap is enforced server-side: Postgres itself cancels
    pg_sleep(), not a client-side abandon that would still wait out the
    full sleep. Skips without a live Postgres — CI only provides one via
    apps/cloud's docker-based lanes (cloud.yml); export the same
    CLOUD_TEST_POSTGRES_* vars locally (see
    apps/cloud/tests/integration/conftest.py) to run this against a
    real database.
    """

    @pytest.fixture
    def pg_source_config(self) -> dict[str, Any]:
        host = os.environ.get("CLOUD_TEST_POSTGRES_HOST")
        if not host:
            pytest.skip("CLOUD_TEST_POSTGRES_HOST not set; no live Postgres available")
        return {
            "type": "postgres",
            "host": host,
            "port": int(os.environ["CLOUD_TEST_POSTGRES_PORT"]),
            "dbname": os.environ["CLOUD_TEST_POSTGRES_DB"],
            "user": os.environ["CLOUD_TEST_POSTGRES_USER"],
            "password": os.environ["CLOUD_TEST_POSTGRES_PASSWORD"],
        }

    def test_pg_sleep_exceeding_cap_is_cancelled_server_side(
        self,
        pg_source_config: dict[str, Any],
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        source_config = {**pg_source_config, "max_query_duration_seconds": 1}
        sql_adapter = _sql_adapter(tmp_path, local_project, "postgres")
        query = SqlQuery(sql="SELECT pg_sleep(5)", source="warehouse")

        start = time.monotonic()
        result = sql_adapter._execute_via_dbt_adapter(
            _prepared("SELECT pg_sleep(5)", "postgres"), query, source_config
        )
        elapsed = time.monotonic() - start

        assert result.error is not None
        assert result.error.code is ERR_QUERY_DURATION_EXCEEDED
        assert "max_query_duration_seconds=1s" in str(result.error)
        assert "'warehouse'" in str(result.error)
        # The load-bearing assertion: elapsed is close to the 1s cap, not the
        # full 5s pg_sleep duration — proof Postgres cancelled the statement
        # itself rather than us abandoning the wait client-side.
        assert elapsed < 3
