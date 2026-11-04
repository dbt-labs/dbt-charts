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

The dbt backend runs every dbt-Core warehouse source, source-less dbt-jinja
queries included, and is the path the original bug report exercised.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_BINDER_GROUP_BY,
    ERR_BINDER_TYPE_MISMATCH,
    ERR_BINDER_UNKNOWN_COLUMN,
    ERR_BINDER_UNKNOWN_RELATION,
    ERR_MUTATING_SQL,
    ERR_UNPARSEABLE_SQL,
    ERR_WAREHOUSE_BYTES_CAP_EXCEEDED,
    ERR_WAREHOUSE_CONNECTION,
    ERR_WAREHOUSE_RUNTIME,
)
from dbt_charts.core.diagnostics.execution import MutatingSqlError, UnparseableSqlError
from dbt_charts.core.execute.adapters.base import (
    WarehouseStatementError,
    classify_warehouse_error,
    resolve_effective_row_limit,
)
from dbt_charts.core.execute.compose import ResolvedQuery

from ...conftest import DbtHarness


def _prepared(sql: str) -> ResolvedQuery:
    """What compose would hand back — these tests exercise the send half."""
    return ResolvedQuery(
        sql=sql,
        params=(),
        setup_sql=(),
        limit=resolve_effective_row_limit(None),
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

    def test_not_found_table_returns_binder_unknown_relation_code(self) -> None:
        exc = Exception(
            "Not found: Table my-project:my_dataset.my_table was not found "
            "in location US"
        )
        result = classify_warehouse_error("dbt SQL execution", exc, "bigquery")
        assert result.error is not None
        assert result.error.code is ERR_BINDER_UNKNOWN_RELATION

    def test_not_found_dataset_returns_binder_unknown_relation_code(self) -> None:
        exc = Exception("Not found: Dataset my-project:no_such_dataset")
        result = classify_warehouse_error("dbt SQL execution", exc, "bigquery")
        assert result.error is not None
        assert result.error.code is ERR_BINDER_UNKNOWN_RELATION

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

    def test_databricks_unmatched_message_falls_back_to_warehouse_runtime(
        self,
    ) -> None:
        exc = Exception("[PARSE_SYNTAX_ERROR] Syntax error at or near 'FORM'")
        result = classify_warehouse_error("dbt SQL execution", exc, "databricks")
        assert result.error is not None
        assert result.error.code is ERR_WAREHOUSE_RUNTIME

    @pytest.mark.parametrize(
        ("dialect", "message"),
        [
            ("databricks", "[TABLE_OR_VIEW_NOT_FOUND] Table or view not found: foo"),
            (
                "snowflake",
                "002003 (42S02): SQL compilation error:\n"
                "Object 'ANALYTICS.FOO' does not exist or not authorized.",
            ),
            ("postgres", 'relation "analytics.foo" does not exist\nLINE 1: select'),
            ("redshift", 'relation "analytics.foo" does not exist'),
        ],
    )
    def test_missing_relation_message_per_dialect(
        self, dialect: str, message: str
    ) -> None:
        result = classify_warehouse_error(
            "dbt SQL execution", Exception(message), dialect
        )
        assert result.error is not None
        assert result.error.code is ERR_BINDER_UNKNOWN_RELATION

    def test_snowflake_group_by_message(self) -> None:
        exc = Exception(
            "001104 (42601): SQL compilation error: error line 1 at position 7\n"
            "'T.ID' in select clause is neither an aggregate nor in the group by clause."
        )
        result = classify_warehouse_error("dbt SQL execution", exc, "snowflake")
        assert result.error is not None
        assert result.error.code is ERR_BINDER_GROUP_BY

    def test_missing_column_message_is_not_a_missing_relation(self) -> None:
        exc = Exception('column "amount" does not exist\nLINE 1: select amount')
        result = classify_warehouse_error("dbt SQL execution", exc, "postgres")
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


@pytest.mark.usefixtures("typed_via_execute")
class TestDbtBackendSurfacesClassifiedErrorCode:
    """End-to-end through the dbt backend — the
    production path for `source:`-declaring queries, i.e. the path the
    original bug report (BigQuery `Unrecognized name` surfaced as
    ERR-INTERNAL) actually exercised."""

    def test_bigquery_unrecognized_column_sets_binder_code(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        adapter = DbtHarness(local_project(tmp_path), profile_type="bigquery")
        query = SqlQuery(sql="SELECT goals_goal_dollars FROM t", source="analytics_bq")
        source_config = {"type": "bigquery", "project": "p", "dataset": "d"}
        with patch.object(adapter.backend, "_get_source_pool") as get_pool:
            get_pool.return_value.execute.side_effect = Exception(
                "Unrecognized name: 'goals_goal_dollars' at [3:42]"
            )
            result = adapter.run_resolved(
                _prepared("SELECT goals_goal_dollars FROM t"), query, source_config
            )
        assert result.error is not None
        assert "Unrecognized name: 'goals_goal_dollars' at [3:42]" in str(result.error)
        assert result.error.code is ERR_BINDER_UNKNOWN_COLUMN

    def test_generic_failure_still_gets_warehouse_runtime_not_none(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        adapter = DbtHarness(local_project(tmp_path), profile_type="snowflake")
        query = SqlQuery(sql="SELECT 1", source="my_snowflake")
        source_config = {"type": "snowflake"}
        with patch.object(adapter.backend, "_get_source_pool") as get_pool:
            get_pool.return_value.execute.side_effect = Exception("connection reset")
            result = adapter.run_resolved(_prepared("SELECT 1"), query, source_config)
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
        adapter = DbtHarness(local_project(tmp_path), profile_type="bigquery")
        query = SqlQuery(sql="SELECT 1", source="analytics_bq")
        source_config = {"type": "bigquery", "project": "p", "dataset": "d"}
        try:
            with patch(
                "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter",
                side_effect=ValueError("Could not automatically determine credentials"),
            ):
                result = adapter.run_resolved(
                    _prepared("SELECT 1"), query, source_config
                )
        finally:
            adapter.close()  # real _SourcePool spun up a ThreadPoolExecutor
        assert result.error is not None
        assert "Could not automatically determine credentials" not in str(result.error)
        assert result.error.detail is not None
        assert "Could not automatically determine credentials" in result.error.detail
        assert result.error.code is ERR_WAREHOUSE_CONNECTION


class TestClassifyWarehouseErrorBySqlstate:
    """The Arrow driver seam carries the warehouse's own SQLSTATE, so
    classification there is a lookup rather than a message-pattern guess.

    Only the SQL-standard classes are mapped. The generic 42000 class is also
    what engines answer a plain syntax error with, so it stays on the runtime
    fallback rather than being read as an unknown-reference verdict.
    """

    @pytest.mark.parametrize("sqlstate", ["42S02", "42P01"])
    def test_object_not_found_is_an_unknown_relation(self, sqlstate: str) -> None:
        error = WarehouseStatementError(sqlstate, "Table 'no_such' does not exist")
        result = classify_warehouse_error(
            "lakecompute SQL execution", error, "lakecompute"
        )
        assert result.error.code is ERR_BINDER_UNKNOWN_RELATION
        assert "no_such" in str(result.error)

    def test_column_not_found_is_an_unknown_reference(self) -> None:
        error = WarehouseStatementError("42S22", "column not found")
        result = classify_warehouse_error(
            "lakecompute SQL execution", error, "lakecompute"
        )
        assert result.error.code is ERR_BINDER_UNKNOWN_COLUMN

    def test_grouping_error_is_a_group_by_mismatch(self) -> None:
        error = WarehouseStatementError("42803", "column must appear in GROUP BY")
        result = classify_warehouse_error("postgres SQL execution", error, "postgres")
        assert result.error.code is ERR_BINDER_GROUP_BY

    def test_invalid_cast_is_a_type_mismatch(self) -> None:
        error = WarehouseStatementError("22018", "Could not convert 'abc'")
        result = classify_warehouse_error(
            "lakecompute SQL execution", error, "lakecompute"
        )
        assert result.error.code is ERR_BINDER_TYPE_MISMATCH

    @pytest.mark.parametrize("sqlstate", ["42000", "22012"])
    def test_an_unmapped_sqlstate_stays_on_the_runtime_fallback(
        self, sqlstate: str
    ) -> None:
        error = WarehouseStatementError(sqlstate, "rejected")
        result = classify_warehouse_error(
            "lakecompute SQL execution", error, "lakecompute"
        )
        assert result.error.code is ERR_WAREHOUSE_RUNTIME


class TestClassifyWarehouseErrorBigQueryBytesCap:
    """BigQuery refuses a job estimated to scan more than the source's
    ``maximum_bytes_billed`` before it runs. The agent needs the cap and the
    narrowing advice, not the raw byte counts."""

    _MESSAGE = (
        "400 Query exceeded limit for bytes billed: 20000000000. "
        "25400000000 or higher required."
    )

    def test_bytes_cap_rejection_names_the_cap_and_the_narrowing_advice(
        self,
    ) -> None:
        result = classify_warehouse_error(
            "dbt SQL execution", Exception(self._MESSAGE), "bigquery"
        )
        assert result.error is not None
        assert result.error.code is ERR_WAREHOUSE_BYTES_CAP_EXCEEDED
        assert result.error.detail == self._MESSAGE
        text = str(result.error)
        assert "20 GB" in text
        assert "25.4 GB" in text
        assert "LIMIT" in text

    def test_the_cap_message_is_only_matched_under_bigquery(self) -> None:
        result = classify_warehouse_error(
            "dbt SQL execution", Exception(self._MESSAGE), "snowflake"
        )
        assert result.error is not None
        assert result.error.code is ERR_WAREHOUSE_RUNTIME
