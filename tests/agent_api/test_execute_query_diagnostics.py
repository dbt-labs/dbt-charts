"""execute_query surfaces validate_query diagnostics without blocking execution."""

from __future__ import annotations

from unittest.mock import MagicMock

from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.adapters.base import QueryResult


def _registry(rows=None, error=None):
    registry = MagicMock()
    registry.resolve_source_config.return_value = {"type": "duckdb", "path": ":memory:"}
    registry.execute.return_value = QueryResult(
        data=rows if rows is not None else [],
        error=QueryError(error) if error is not None else None,
    )
    return registry


def test_reaggregation_query_warns_but_still_returns_rows() -> None:
    from dbt_charts.agent_api.query import execute_query

    registry = _registry(rows=[{"n": 5}])
    # SUM over an already-summed column is a statistical error, not a failure.
    result = execute_query(
        "SELECT AVG(t) AS n FROM (SELECT region, SUM(x) AS t FROM a GROUP BY region) s",
        source="db",
        adapter_registry=registry,
    )
    assert result.success is True  # not blocked
    assert result.data == [{"n": 5}]
    assert any(d.code == "WARN-REAGGREGATION" for d in result.diagnostics)


def test_clean_query_has_no_diagnostics() -> None:
    from dbt_charts.agent_api.query import execute_query

    registry = _registry(rows=[{"x": 1}])
    result = execute_query("SELECT x FROM t", source="db", adapter_registry=registry)
    assert result.success is True
    assert result.diagnostics == []


def test_diagnostics_attached_on_execution_error() -> None:
    from dbt_charts.agent_api.query import execute_query

    registry = _registry(error="no such column: foo")
    result = execute_query(
        "SELECT AVG(t) FROM (SELECT region, SUM(x) AS t FROM a GROUP BY region) s",
        source="db",
        adapter_registry=registry,
    )
    assert result.success is False
    assert result.errors == ["no such column: foo"]
    # Structural diagnostics still surface even when execution failed.
    assert any(d.code == "WARN-REAGGREGATION" for d in result.diagnostics)


def test_validator_failure_degrades_gracefully(monkeypatch) -> None:
    from dbt_charts.agent_api import query as mod

    def boom(*_a, **_k):
        raise RuntimeError("validator exploded")

    monkeypatch.setattr(mod, "validate_query", boom)
    registry = _registry(rows=[{"x": 1}])
    result = mod.execute_query(
        "SELECT x FROM t", source="db", adapter_registry=registry
    )
    # A validator crash must never break a real query.
    assert result.success is True
    assert result.data == [{"x": 1}]
    assert result.diagnostics == []
