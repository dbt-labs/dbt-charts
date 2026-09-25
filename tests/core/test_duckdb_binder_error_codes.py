"""Regression tests: DuckDB binder errors surface typed ERR-* codes.

Before this fix, BinderException / CatalogException / TypeMismatchException
from DuckDB all bubbled up tagged ERR-UNKNOWN-INTERNAL.  After the fix each
maps to a specific ERR-BINDER-* or ERR-WAREHOUSE-RUNTIME code.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import DuckDBSourceConfig
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter
from dbt_charts.core.execute.executor import Executor


def _make_query(sql: str) -> SqlQuery:
    return SqlQuery(sql=sql, source="test_db")


class TestDuckDBBinderErrorCodes:
    """QueryResult.error.code is a typed code for DuckDB binder-class errors."""

    def test_unknown_column_returns_binder_unknown_column_code(self) -> None:
        """BinderException for a missing column → ERR-BINDER-UNKNOWN-COLUMN."""
        from dbt_charts.core.diagnostics.codes_execute import (
            ERR_BINDER_UNKNOWN_COLUMN,
        )

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
        )
        try:
            result = adapter._execute(
                _make_query("SELECT nonexistent_col FROM (SELECT 1 AS a) t")
            )
            assert result.error is not None
            assert result.error.code is ERR_BINDER_UNKNOWN_COLUMN, (
                f"expected ERR-BINDER-UNKNOWN-COLUMN, got {result.error.code!r}"
            )
        finally:
            adapter.close()

    def test_type_mismatch_exception_returns_binder_type_mismatch_code(self) -> None:
        """TypeMismatchException → ERR-BINDER-TYPE-MISMATCH (via _classify_duckdb_error)."""
        import duckdb

        from dbt_charts.core.diagnostics.codes_execute import (
            ERR_BINDER_TYPE_MISMATCH,
        )
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _classify_duckdb_error,
        )

        # TypeMismatchException is hard to trigger from plain SQL in DuckDB — the
        # binder normally raises BinderException instead.  We verify the classifier
        # directly: it maps TypeMismatchException to the right code.
        exc = duckdb.TypeMismatchException("Cannot add INTEGER and VARCHAR")
        result = _classify_duckdb_error(exc)
        assert result.error is not None
        assert result.error.code is ERR_BINDER_TYPE_MISMATCH, (
            f"expected ERR-BINDER-TYPE-MISMATCH, got {result.error.code!r}"
        )

    def test_catalog_exception_returns_binder_unknown_column_code(self) -> None:
        """CatalogException (missing table) → ERR-BINDER-UNKNOWN-COLUMN."""
        from dbt_charts.core.diagnostics.codes_execute import (
            ERR_BINDER_UNKNOWN_COLUMN,
        )

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
        )
        try:
            result = adapter._execute(_make_query("SELECT * FROM nonexistent_table"))
            assert result.error is not None
            assert result.error.code is ERR_BINDER_UNKNOWN_COLUMN, (
                f"expected ERR-BINDER-UNKNOWN-COLUMN, got {result.error.code!r}"
            )
        finally:
            adapter.close()

    def test_other_duckdb_exception_returns_warehouse_runtime_code(self) -> None:
        """Non-binder DuckDB exceptions fall back to ERR-WAREHOUSE-RUNTIME."""
        import duckdb

        from dbt_charts.core.diagnostics.codes_execute import ERR_WAREHOUSE_RUNTIME
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _classify_duckdb_error,
        )

        exc = duckdb.IOException("could not open file: /no/such/path")
        result = _classify_duckdb_error(exc)
        assert result.error is not None
        assert result.error.code is ERR_WAREHOUSE_RUNTIME

    def test_unparseable_sql_error_keeps_its_own_code_and_fields(self) -> None:
        """UnparseableSqlError raised by validate_select_only *inside* the
        same try _classify_duckdb_error classifies must keep ERR-UNPARSEABLE-
        SQL — not fall through to the generic ERR-WAREHOUSE-RUNTIME fallback,
        which would also lose the sql_line/sql_start_col/sql_end_col fields
        this task adds."""
        from dbt_charts.core.diagnostics.codes_execute import ERR_UNPARSEABLE_SQL
        from dbt_charts.core.diagnostics.execution import (
            SqlErrorPosition,
            UnparseableSqlError,
        )
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _classify_duckdb_error,
        )

        exc = UnparseableSqlError(
            "boom",
            sql_position=SqlErrorPosition(
                line=1, start_col=12, end_col=14, line_text="SELECT 1 AS bb"
            ),
        )
        result = _classify_duckdb_error(exc)
        assert result.error is not None
        assert result.error.code is ERR_UNPARSEABLE_SQL
        assert result.error.fields["sql_line"] == 1
        assert result.error.fields["sql_start_col"] == 12
        assert result.error.fields["sql_end_col"] == 14
        # The line the columns were measured against travels with them — the
        # stamping pass refuses to narrow without it.
        assert result.error.fields["sql_line_text"] == "SELECT 1 AS bb"

    def test_mutating_sql_error_keeps_its_own_code(self) -> None:
        """Same delegation for MutatingSqlError — a non-read-only statement
        raised by validate_select_only must not collapse into
        ERR-WAREHOUSE-RUNTIME either."""
        from dbt_charts.core.diagnostics.codes_execute import ERR_MUTATING_SQL
        from dbt_charts.core.diagnostics.execution import MutatingSqlError
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _classify_duckdb_error,
        )

        exc = MutatingSqlError("Drop", "DROP TABLE foo")
        result = _classify_duckdb_error(exc)
        assert result.error is not None
        assert result.error.code is ERR_MUTATING_SQL

    def test_successful_query_has_no_error(self) -> None:
        """Successful queries must leave error as None."""
        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
        )
        try:
            result = adapter._execute(_make_query("SELECT 1 AS val"))
            assert result.error is None
        finally:
            adapter.close()


class TestExecutorSurfacesBinderCode:
    """QueryError.code propagates the code on QueryResult.error through the executor."""

    def test_executor_raises_query_error_with_binder_code(self) -> None:
        """Executor raises QueryResult.error as-is, code intact (not ERR-UNKNOWN-INTERNAL)."""
        from unittest.mock import Mock

        from dbt_charts.core.compile import compile
        from dbt_charts.core.diagnostics.codes_execute import (
            ERR_BINDER_UNKNOWN_COLUMN,
        )
        from dbt_charts.core.execute.adapters.base import QueryResult

        yaml_content = """
title: Binder test
queries:
  bad_query:
    sql: "SELECT nonexistent_col FROM (SELECT 1 AS a) t"
    source: mydb
charts:
  bad_chart:
    type: table
    query: bad_query
rows:
  - bad_chart
"""
        compile_result = compile(yaml_content)
        assert compile_result.success, compile_result.errors

        board = compile_result.board
        assert board is not None

        # Mock the registry to return a pre-classified QueryResult.
        mock_registry = Mock()
        mock_registry.execute.return_value = QueryResult(
            data=[],
            error=QueryError(
                'Binder Error: Referenced column "nonexistent_col" not found',
                code=ERR_BINDER_UNKNOWN_COLUMN,
            ),
        )

        executor = Executor(board, adapter_registry=mock_registry)

        with pytest.raises(QueryError) as exc_info:
            executor.execute_query("bad_query")

        err = exc_info.value
        assert err.code is not None
        assert err.code.code == "ERR-BINDER-UNKNOWN-COLUMN", (
            f"expected ERR-BINDER-UNKNOWN-COLUMN, got {err.code.code!r}"
        )
