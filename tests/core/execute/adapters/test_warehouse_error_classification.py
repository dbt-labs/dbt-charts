"""Regression tests: warehouse SQL errors (BigQuery/Snowflake/Databricks)
surface typed ERR-* codes instead of ERR-INTERNAL.

`classify_warehouse_error` (execute/adapters/base.py) classifies on message
content because dbt's adapters wrap every warehouse driver's error in a bare
string — there is no shared exception hierarchy to isinstance-check across
BigQuery/Snowflake/Databricks the way DuckDB's own driver exceptions allow.
Only BigQuery's error formats are confidently pattern-matched; every other
dialect (and any unmatched BigQuery message) falls back to
ERR-WAREHOUSE-RUNTIME. A DbtChartsError raised inside the classified
scope (e.g. MutatingSqlError from the SQL guard) keeps its own registered
code instead of being overwritten by that fallback.

Two adapters share this classifier: DbtAdapter (source-less dbt-jinja
queries) and SqlAdapter (`source:`-declaring queries — the production path
for named warehouse sources, and the one the original bug report exercised).
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_BINDER_UNKNOWN_COLUMN,
    ERR_MUTATING_SQL,
    ERR_UNPARSEABLE_SQL,
    ERR_WAREHOUSE_CONNECTION,
    ERR_WAREHOUSE_RUNTIME,
)
from dbt_charts.core.diagnostics.codes_unknown import ERR_INTERNAL
from dbt_charts.core.diagnostics.execution import MutatingSqlError, UnparseableSqlError
from dbt_charts.core.execute.adapters.base import (
    classify_warehouse_error,
    resolve_effective_row_limit,
)
from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter
from dbt_charts.core.execute.adapters.sql_adapter import PreparedSql, SqlAdapter


def _prepared(sql: str, dialect_name: str) -> PreparedSql:
    """What prepare_sql would hand back — these tests exercise the send half."""
    return PreparedSql(
        sql=sql,
        setup_sql=None,
        dialect_name=dialect_name,
        row_fetch_limit=resolve_effective_row_limit(None),
    )


class TestClassifyWarehouseErrorBigQuery:
    """BigQuery message formats are pattern-matched with high confidence."""

    def test_unrecognized_name_returns_binder_unknown_column_code(self) -> None:
        exc = Exception("Unrecognized name: 'goals_goal_dollars' at [3:42]")
        result = classify_warehouse_error("dbt SQL execution", exc, "bigquery")
        assert result.error is not None
        assert result.error.code is ERR_BINDER_UNKNOWN_COLUMN
        # Original warehouse message is preserved verbatim in the error text.
        assert "Unrecognized name: 'goals_goal_dollars' at [3:42]" in str(result.error)

    def test_not_found_table_returns_binder_unknown_column_code(self) -> None:
        exc = Exception(
            "Not found: Table my-project:my_dataset.my_table was not found "
            "in location US"
        )
        result = classify_warehouse_error("dbt SQL execution", exc, "bigquery")
        assert result.error is not None
        assert result.error.code is ERR_BINDER_UNKNOWN_COLUMN

    def test_not_found_dataset_returns_binder_unknown_column_code(self) -> None:
        exc = Exception("Not found: Dataset my-project:no_such_dataset")
        result = classify_warehouse_error("dbt SQL execution", exc, "bigquery")
        assert result.error is not None
        assert result.error.code is ERR_BINDER_UNKNOWN_COLUMN

    def test_unmatched_bigquery_message_falls_back_to_warehouse_runtime(self) -> None:
        """A BigQuery error we don't have a confident pattern for must not be
        guessed into a specific code — the generic code is still correct."""
        exc = Exception("Resources exceeded during query execution")
        result = classify_warehouse_error("dbt SQL execution", exc, "bigquery")
        assert result.error is not None
        assert result.error.code is ERR_WAREHOUSE_RUNTIME


class TestClassifyWarehouseErrorOtherDialects:
    """Snowflake/Databricks message formats are not (yet) pattern-matched —
    never guess a specific code from an unverified format; route generic."""

    def test_snowflake_dialect_never_gets_bigquery_pattern_matched(self) -> None:
        # Even text that looks like a BigQuery message must not be classified
        # as BINDER_UNKNOWN_COLUMN under a different dialect.
        exc = Exception("Unrecognized name: 'foo' at [1:1]")
        result = classify_warehouse_error("dbt SQL execution", exc, "snowflake")
        assert result.error is not None
        assert result.error.code is ERR_WAREHOUSE_RUNTIME

    def test_databricks_dialect_falls_back_to_warehouse_runtime(self) -> None:
        exc = Exception("[TABLE_OR_VIEW_NOT_FOUND] Table or view not found: foo")
        result = classify_warehouse_error("dbt SQL execution", exc, "databricks")
        assert result.error is not None
        assert result.error.code is ERR_WAREHOUSE_RUNTIME


class TestClassifyWarehouseErrorPreservesDbtChartsErrorCode:
    """A DbtChartsError raised inside the classified scope keeps its own code.

    validate_select_only/validate_setup_sql raise MutatingSqlError or
    UnparseableSqlError from *inside* the try both adapters classify — these
    are the fail-closed security gate for jinja-shaped SQL the compile-time
    guard deferred to runtime. Overwriting their code with the generic
    warehouse-rejected fallback would be a regression, not a classification.
    """

    def test_mutating_sql_error_keeps_its_own_code(self) -> None:
        exc = MutatingSqlError("Drop", "DROP TABLE foo")
        result = classify_warehouse_error("dbt SQL execution", exc, "bigquery")
        assert result.error is not None
        assert result.error.code is ERR_MUTATING_SQL

    def test_unparseable_sql_error_keeps_its_own_code(self) -> None:
        exc = UnparseableSqlError("unsupported_jinja_node:Call")
        result = classify_warehouse_error("dbt SQL execution", exc, "bigquery")
        assert result.error is not None
        assert result.error.code is ERR_UNPARSEABLE_SQL


class TestDbtAdapterSurfacesClassifiedErrorCode:
    """End-to-end through DbtAdapter._execute: a warehouse failure gets a
    typed code, not the ERR-INTERNAL fallback."""

    def test_bigquery_unrecognized_column_sets_binder_code(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        adapter = DbtAdapter(
            project=local_project(tmp_path),
            dbt_project_path=tmp_path,
            target_name="dev",
        )
        adapter._adapter = MagicMock()
        adapter._adapter.execute.side_effect = Exception(
            "Unrecognized name: 'goals_goal_dollars' at [3:42]"
        )
        adapter._dialect = "bigquery"
        with patch.object(adapter, "_resolve_dbt_sql", return_value="SELECT 1"):
            query = SqlQuery(sql="SELECT {{ ref('x') }}", source="my_named_source")
            result = adapter._execute(query)
        assert result.error is not None
        assert "Unrecognized name: 'goals_goal_dollars' at [3:42]" in str(result.error)
        assert result.error.code is ERR_BINDER_UNKNOWN_COLUMN

    def test_generic_failure_still_gets_warehouse_runtime_not_none(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Any warehouse execution failure gets classified — never left at the
        ERR-INTERNAL fallback."""
        adapter = DbtAdapter(
            project=local_project(tmp_path),
            dbt_project_path=tmp_path,
            target_name="dev",
        )
        adapter._adapter = MagicMock()
        adapter._adapter.execute.side_effect = Exception("connection reset")
        adapter._dialect = "snowflake"
        with patch.object(adapter, "_resolve_dbt_sql", return_value="SELECT 1"):
            query = SqlQuery(sql="SELECT {{ ref('x') }}", source="my_named_source")
            result = adapter._execute(query)
        assert result.error is not None
        assert result.error.code is ERR_WAREHOUSE_RUNTIME

    def test_setup_failure_is_not_mislabeled_as_warehouse_rejection(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A profile/manifest/target setup failure never reaches the warehouse
        — it must not be classified ERR-WAREHOUSE-RUNTIME, which would
        assert a false cause ("warehouse rejected the query") for a query
        that was never sent.

        This is resolving `_get_dbt_adapter()` — reading profiles.yml,
        picking a target, validating the credentials shape — not opening a
        connection to the warehouse, so it stays out of scope for
        ERR-WAREHOUSE-CONNECTION (which is reserved for opening the
        connection itself, see test_connect_failure_returns_warehouse_connection_code
        below). It stays at the ERR-INTERNAL fallback code by deliberate
        choice, not oversight.
        """
        adapter = DbtAdapter(
            project=local_project(tmp_path),
            dbt_project_path=tmp_path,
            target_name="dev",
        )
        with patch.object(
            adapter,
            "_get_dbt_adapter",
            side_effect=ValueError("Profile 'x' not found in profiles.yml"),
        ):
            query = SqlQuery(sql="SELECT {{ ref('x') }}", source="my_named_source")
            result = adapter._execute(query)
        assert result.error is not None
        assert "Profile 'x' not found in profiles.yml" in str(result.error)
        assert result.error.code is ERR_INTERNAL

    def test_connect_failure_returns_warehouse_connection_code(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A credentials/host failure while opening the actual warehouse
        connection (adapter.connection_named(...) forcing the lazy handle
        open) must not be classified ERR-WAREHOUSE-RUNTIME — nothing read
        the SQL yet. Distinct from test_setup_failure_is_not_mislabeled_as_
        warehouse_rejection above, which covers profile/target resolution
        before any connection is attempted.
        """

        class _FailingHandleConnection:
            @property
            def handle(self) -> object:
                raise RuntimeError("Invalid access token")

        adapter = DbtAdapter(
            project=local_project(tmp_path),
            dbt_project_path=tmp_path,
            target_name="dev",
        )
        adapter._adapter = MagicMock()
        adapter._adapter.connections.get_thread_connection.return_value = (
            _FailingHandleConnection()
        )
        adapter._dialect = "databricks"
        with patch.object(adapter, "_resolve_dbt_sql", return_value="SELECT 1"):
            query = SqlQuery(sql="SELECT {{ ref('x') }}", source="my_named_source")
            result = adapter._execute(query)
        assert result.error is not None
        # databricks has no classifier rules, so the driver text is not
        # matched into a specific bucket — it must still never reach the
        # authored message, only .detail.
        assert "Invalid access token" not in str(result.error)
        assert result.error.detail is not None
        assert "Invalid access token" in result.error.detail
        assert result.error.code is ERR_WAREHOUSE_CONNECTION
        # The connect failure must short-circuit before the query is sent.
        adapter._adapter.execute.assert_not_called()


# --- dct query: the cleanup crash must not replace the real cause -------------

_PEM_FAILURE = (
    "Database Error\n  Unable to load PEM file. See "
    "https://cryptography.io/en/latest/faq/ for more details. "
    "InvalidData(InvalidPadding)"
)

# A private key that fails PEM parsing before any network call is attempted.
_UNPARSEABLE_KEYFILE_JSON = {
    "type": "service_account",
    "project_id": "my-project",
    "private_key_id": "abc123",
    "private_key": "-----BEGIN PRIVATE KEY-----\nNOTAREALKEY\n-----END PRIVATE KEY-----\n",
    "client_email": "svc@my-project.iam.gserviceaccount.com",
    "client_id": "1",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
}


class _UnopenableConnection:
    """dbt's Connection after a failed open: no handle, and asking for one raises."""

    def __init__(self, open_error: Exception) -> None:
        self._open_error = open_error

    @property
    def handle(self) -> Any:
        raise self._open_error


class _CleanupCrashingAdapter:
    """dbt-bigquery's release: a bare handle.close(), whatever the open did."""

    def __init__(self, connection: object) -> None:
        self.connections = MagicMock()
        self.connections.get_thread_connection.return_value = connection
        self.execute = MagicMock(
            return_value=(None, MagicMock(column_names=[], rows=[]))
        )

    @contextmanager
    def connection_named(self, name: str) -> Generator[None]:
        try:
            yield
        finally:
            # BigQueryConnectionManager.close, verbatim in effect.
            raise AttributeError("'NoneType' object has no attribute 'close'")


class TestDbtAdapterConnectFailureSurvivesCleanupCrash:
    def test_reports_the_credential_error_not_the_cleanup_crash(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        adapter = DbtAdapter(
            project=local_project(tmp_path),
            dbt_project_path=tmp_path,
            target_name="dev",
        )
        adapter._adapter = _CleanupCrashingAdapter(
            _UnopenableConnection(RuntimeError(_PEM_FAILURE))
        )
        adapter._dialect = "bigquery"
        with patch.object(adapter, "_resolve_dbt_sql", return_value="SELECT 1"):
            result = adapter._execute(SqlQuery(sql="SELECT 1", source="bq"))

        assert result.error is not None
        assert "'NoneType' object has no attribute 'close'" not in str(result.error)
        assert "Unable to load PEM file" not in str(result.error)
        assert result.error.detail is not None
        assert "Unable to load PEM file" in result.error.detail
        assert result.error.code is ERR_WAREHOUSE_CONNECTION
        adapter._adapter.execute.assert_not_called()

    def test_a_release_crash_after_a_clean_open_still_raises(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Only an open failure may outrank the release's own error: a crash
        after the query ran is neither a connect failure nor a rejection."""
        adapter = DbtAdapter(
            project=local_project(tmp_path),
            dbt_project_path=tmp_path,
            target_name="dev",
        )
        adapter._adapter = _CleanupCrashingAdapter(MagicMock(handle=object()))
        adapter._dialect = "bigquery"
        with (
            patch.object(adapter, "_resolve_dbt_sql", return_value="SELECT 1"),
            pytest.raises(AttributeError, match="has no attribute 'close'"),
        ):
            adapter._execute(SqlQuery(sql="SELECT 1", source="bq"))
        adapter._adapter.execute.assert_called_once()

    def test_names_an_unparseable_bigquery_key(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The whole path, against the real dbt-bigquery adapter.

        Offline: PEM parsing fails while the credential is being built, long
        before anything would reach Google.
        """
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        (tmp_path / "profiles.yml").write_text(
            yaml.safe_dump(
                {
                    "bq": {
                        "target": "dev",
                        "outputs": {
                            "dev": {
                                "type": "bigquery",
                                "method": "service-account-json",
                                "project": "my-project",
                                "dataset": "analytics",
                                "keyfile_json": _UNPARSEABLE_KEYFILE_JSON,
                            }
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        adapter = DbtAdapter(
            project=local_project(tmp_path),
            dbt_project_path=tmp_path,
            target_name="dev",
            profile_name="bq",
        )
        with patch.object(adapter, "_resolve_dbt_sql", return_value="SELECT 1"):
            result = adapter._execute(SqlQuery(sql="SELECT 1", source="bq"))

        assert result.error is not None
        assert "'NoneType' object has no attribute 'close'" not in str(result.error)
        assert "PEM" not in str(result.error)
        assert result.error.detail is not None
        assert "PEM" in result.error.detail
        assert result.error.code is ERR_WAREHOUSE_CONNECTION


class TestSqlAdapterSurfacesClassifiedErrorCode:
    """End-to-end through SqlAdapter._execute_via_dbt_adapter — the
    production path for `source:`-declaring queries, i.e. the path the
    original bug report (BigQuery `Unrecognized name` surfaced as
    ERR-INTERNAL) actually exercised."""

    def test_bigquery_unrecognized_column_sets_binder_code(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        adapter = SqlAdapter(
            project=local_project(tmp_path),
            dbt_project_path=None,
            profile_type="bigquery",
        )
        query = SqlQuery(sql="SELECT goals_goal_dollars FROM t", source="analytics_bq")
        source_config = {"type": "bigquery", "project": "p", "dataset": "d"}
        with patch.object(adapter, "_get_source_pool") as get_pool:
            get_pool.return_value.execute.side_effect = Exception(
                "Unrecognized name: 'goals_goal_dollars' at [3:42]"
            )
            result = adapter._execute_via_dbt_adapter(
                _prepared("SELECT goals_goal_dollars FROM t", "bigquery"),
                query,
                source_config,
            )
        assert result.error is not None
        assert "Unrecognized name: 'goals_goal_dollars' at [3:42]" in str(result.error)
        assert result.error.code is ERR_BINDER_UNKNOWN_COLUMN

    def test_generic_failure_still_gets_warehouse_runtime_not_none(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        adapter = SqlAdapter(
            project=local_project(tmp_path),
            dbt_project_path=None,
            profile_type="snowflake",
        )
        query = SqlQuery(sql="SELECT 1", source="my_snowflake")
        source_config = {"type": "snowflake"}
        with patch.object(adapter, "_get_source_pool") as get_pool:
            get_pool.return_value.execute.side_effect = Exception("connection reset")
            result = adapter._execute_via_dbt_adapter(
                _prepared("SELECT 1", "snowflake"), query, source_config
            )
        assert result.error is not None
        assert result.error.code is ERR_WAREHOUSE_RUNTIME

    def test_connection_setup_failure_is_not_mislabeled_as_warehouse_rejection(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A bad-credentials/unreachable-host failure while the pool's worker
        thread builds+connects its dbt adapter never reaches the warehouse —
        classifying it ERR-WAREHOUSE-RUNTIME would assert a false
        cause. This is the real-adapter path (unlike the other tests in this
        class, which stub _get_source_pool): it exercises the actual
        _SourcePool.execute -> _ensure_connected failure, raised as
        ConnectionSetupFailed and routed to connection_failure (typed
        ERR-WAREHOUSE-CONNECTION) instead of classify_warehouse_error.
        """
        adapter = SqlAdapter(
            project=local_project(tmp_path),
            dbt_project_path=None,
            profile_type="bigquery",
        )
        query = SqlQuery(sql="SELECT 1", source="analytics_bq")
        source_config = {"type": "bigquery", "project": "p", "dataset": "d"}
        try:
            with patch(
                "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter",
                side_effect=ValueError("Could not automatically determine credentials"),
            ):
                result = adapter._execute_via_dbt_adapter(
                    _prepared("SELECT 1", "bigquery"), query, source_config
                )
        finally:
            adapter.close()  # real _SourcePool spun up a ThreadPoolExecutor
        assert result.error is not None
        assert "Could not automatically determine credentials" not in str(result.error)
        assert result.error.detail is not None
        assert "Could not automatically determine credentials" in result.error.detail
        assert result.error.code is ERR_WAREHOUSE_CONNECTION
