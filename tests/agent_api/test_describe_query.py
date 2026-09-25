"""Tests for describe_query agent_api verb."""

from __future__ import annotations

from unittest.mock import MagicMock

from dbt_charts.agent_api.describe_query import (
    DescribeQueryColumn,
    describe_query,
)
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.adapters.base import QueryResult
from dbt_charts.core.execute.warehouse_check import WarehouseCheck, WarehouseCheckColumn


def _registry_non_duckdb(source_type: str = "bigquery"):
    """Fake AdapterRegistry for a non-DuckDB source (bigquery/postgres/etc).

    Never wires up ``registry.execute`` — the whole point of this dispatch is
    that a non-DuckDB describe reaches for ``check_ad_hoc_query``, never the
    generic execute path.
    """
    registry = MagicMock()
    registry.resolve_source_config.return_value = {
        "type": source_type,
        "project": "my-proj",
    }
    return registry


def _registry_for_duckdb(rows=None, error=None):
    """Fake AdapterRegistry for the DuckDB read-only path (registry.execute())."""
    registry = MagicMock()
    registry.resolve_source_config.return_value = {"type": "duckdb", "path": ":memory:"}
    registry.execute.return_value = QueryResult(
        data=rows or [], error=QueryError(error) if error is not None else None
    )
    return registry


def _mock_check_ad_hoc_query(monkeypatch, check=None, raises=None):
    """Patch describe_query's dispatch call and record how it was invoked."""
    from dbt_charts.agent_api import describe_query as mod

    calls = []

    def _fake(sql, *, source, adapter_registry):
        calls.append((sql, source))
        if raises is not None:
            raise raises
        assert check is not None
        return check

    monkeypatch.setattr(mod, "check_ad_hoc_query", _fake)
    return calls


class TestGating:
    def test_parse_error_short_circuits_and_does_not_dispatch(self, monkeypatch):
        calls = _mock_check_ad_hoc_query(monkeypatch)
        registry = _registry_non_duckdb()
        result = describe_query(
            "SELECT * FROM",  # incomplete SELECT — sqlglot parse error
            source="warehouse",
            adapter_registry=registry,
        )
        assert result.success is False
        assert result.columns is None
        assert any(d.code == "WARN-PARSE-ERROR" for d in result.diagnostics)
        assert calls == []

    def test_missing_join_predicate_short_circuits(self, monkeypatch):
        calls = _mock_check_ad_hoc_query(monkeypatch)
        registry = _registry_non_duckdb()
        result = describe_query(
            "SELECT a.x FROM a JOIN b",
            source="warehouse",
            dialect="duckdb",
            adapter_registry=registry,
        )
        assert result.success is False
        assert any(d.code == "WARN-MISSING-JOIN-PREDICATE" for d in result.diagnostics)
        assert calls == []

    def test_fanout_warning_does_not_block_dispatch(self, monkeypatch):
        check = WarehouseCheck(
            status="valid",
            adapter_type="bigquery",
            mechanism="bigquery-dry-run",
            columns_checked=True,
            columns=[WarehouseCheckColumn(name="x", type="INTEGER")],
        )
        calls = _mock_check_ad_hoc_query(monkeypatch, check=check)
        registry = _registry_non_duckdb()
        result = describe_query(
            "SELECT SUM(a.x) FROM a JOIN b ON a.id = b.a_id",
            source="warehouse",
            adapter_registry=registry,
        )
        assert result.success is True
        assert result.columns == [DescribeQueryColumn(name="x", type="INTEGER")]
        assert len(calls) == 1

    def test_empty_sql_short_circuits(self, monkeypatch):
        calls = _mock_check_ad_hoc_query(monkeypatch)
        registry = _registry_non_duckdb()
        result = describe_query("   ", source="warehouse", adapter_registry=registry)
        assert result.success is False
        assert any(d.code == "WARN-PARSE-ERROR" for d in result.diagnostics)
        assert calls == []


class TestNonDuckDBDispatch:
    """Every non-DuckDB source routes through check_ad_hoc_query — never a
    full-price execute/get_column_schema_from_query call."""

    def test_valid_check_returns_columns(self, monkeypatch):
        check = WarehouseCheck(
            status="valid",
            adapter_type="bigquery",
            mechanism="bigquery-dry-run",
            columns_checked=True,
            columns=[
                WarehouseCheckColumn(name="month", type="DATE"),
                WarehouseCheckColumn(name="revenue", type="DECIMAL"),
            ],
        )
        calls = _mock_check_ad_hoc_query(monkeypatch, check=check)
        registry = _registry_non_duckdb()
        result = describe_query(
            "SELECT month, SUM(revenue) AS revenue FROM orders GROUP BY 1",
            source="warehouse",
            adapter_registry=registry,
        )
        assert result.success is True
        assert result.columns is not None
        assert [c.name for c in result.columns] == ["month", "revenue"]
        assert result.columns[1].type == "DECIMAL"
        assert result.error is None
        assert calls == [
            (
                "SELECT month, SUM(revenue) AS revenue FROM orders GROUP BY 1",
                "warehouse",
            )
        ]

    def test_dispatch_exception_surfaces_on_result(self, monkeypatch):
        _mock_check_ad_hoc_query(monkeypatch, raises=RuntimeError("warehouse down"))
        registry = _registry_non_duckdb()
        result = describe_query(
            "SELECT 1 AS x",
            source="warehouse",
            adapter_registry=registry,
        )
        assert result.success is False
        assert result.columns is None
        assert result.error is not None
        assert "warehouse down" in result.error

    def test_explain_adapter_reports_reason_never_full_execution(self, monkeypatch):
        """Postgres EXPLAIN proves validity but returns no schema — the refusal
        must carry that reason, never fall back to running the query."""
        check = WarehouseCheck(
            status="valid",
            adapter_type="postgres",
            mechanism="EXPLAIN",
            columns_checked=False,
            reason="EXPLAIN validated the query but returns no result schema",
        )
        calls = _mock_check_ad_hoc_query(monkeypatch, check=check)
        registry = _registry_non_duckdb(source_type="postgres")
        result = describe_query(
            "SELECT * FROM orders",
            source="warehouse",
            adapter_registry=registry,
        )
        assert result.success is False
        assert result.columns is None
        assert result.error is not None
        assert check.reason in result.error
        # A caller who asked for columns needs a next step, not just a
        # validate-tier classification of why there aren't any.
        assert "executing it in full" in result.error
        assert len(calls) == 1
        registry.execute.assert_not_called()

    def test_real_explain_check_reaches_the_refusal_with_its_reason(self):
        """Seam test: the real check_ad_hoc_query EXPLAIN path, not a hand-built
        WarehouseCheck, feeds the refusal — this seam is where an empty-reason
        bug shipped once ("Cannot list columns without running the query: .")."""
        from dbt_charts.core.compile.models.source import parse_source_config
        from dbt_charts.core.execute.adapters.base import QueryResult

        registry = _registry_non_duckdb(source_type="postgres")
        registry.resolve_query_source.return_value = parse_source_config(
            {
                "type": "postgres",
                "host": "h",
                "dbname": "d",
                "user": "u",
                "password": "p",
            }
        )
        registry.execute.return_value = QueryResult(data=[{"QUERY PLAN": "Result"}])

        result = describe_query(
            "SELECT 1 AS x",
            source="warehouse",
            adapter_registry=registry,
        )

        assert result.success is False
        assert result.columns is None
        assert result.error is not None
        assert "no result schema" in result.error
        assert ": ." not in result.error

    def test_invalid_check_surfaces_warehouse_error(self, monkeypatch):
        check = WarehouseCheck(
            status="invalid",
            adapter_type="bigquery",
            mechanism="bigquery-dry-run",
            columns_checked=False,
            error="Table 'orders' not found",
        )
        _mock_check_ad_hoc_query(monkeypatch, check=check)
        registry = _registry_non_duckdb()
        result = describe_query(
            "SELECT * FROM orders",
            source="warehouse",
            adapter_registry=registry,
        )
        assert result.success is False
        assert result.columns is None
        assert result.error == "Table 'orders' not found"

    def test_valid_but_columns_not_checked_reports_failure(self, monkeypatch):
        """BigQuery reports 'valid' with no schema for a multi-statement dry
        run — describe_query has nothing to return, so this is a failure."""
        check = WarehouseCheck(
            status="valid",
            adapter_type="bigquery",
            mechanism="bigquery-dry-run",
            columns_checked=False,
            reason="BigQuery reports no result schema for a multi-statement dry run",
        )
        _mock_check_ad_hoc_query(monkeypatch, check=check)
        registry = _registry_non_duckdb()
        result = describe_query(
            "CREATE TEMP TABLE t AS SELECT 1; SELECT * FROM t;",
            source="warehouse",
            adapter_registry=registry,
        )
        assert result.success is False
        assert result.columns is None
        assert result.error is not None
        assert check.reason in result.error
        assert "executing it in full" in result.error


class TestDuckDBPath:
    """DuckDB sources use registry.execute() (read-only) — unchanged, and
    never reach check_ad_hoc_query."""

    def test_duckdb_returns_columns_from_describe(self, monkeypatch):
        calls = _mock_check_ad_hoc_query(monkeypatch)
        registry = _registry_for_duckdb(
            rows=[
                {"column_name": "id", "column_type": "INTEGER"},
                {"column_name": "name", "column_type": "VARCHAR"},
            ]
        )
        result = describe_query(
            "SELECT id, name FROM t",
            source="db",
            adapter_registry=registry,
        )
        assert result.success is True
        assert result.columns is not None
        assert [c.name for c in result.columns] == ["id", "name"]
        assert result.columns[0].type == "INTEGER"
        assert result.columns[1].type == "VARCHAR"
        assert calls == []

    def test_duckdb_execute_error_surfaces(self):
        registry = _registry_for_duckdb(error="table not found")
        result = describe_query(
            "SELECT x FROM missing",
            source="db",
            adapter_registry=registry,
        )
        assert result.success is False
        assert result.error is not None
        assert "table not found" in result.error

    def test_duckdb_does_not_dispatch_to_check_ad_hoc_query(self, monkeypatch):
        calls = _mock_check_ad_hoc_query(monkeypatch)
        registry = _registry_for_duckdb(
            rows=[{"column_name": "x", "column_type": "INTEGER"}]
        )
        describe_query("SELECT 1 AS x", source="db", adapter_registry=registry)
        assert calls == [], "DuckDB sources must never reach check_ad_hoc_query"
