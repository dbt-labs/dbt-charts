"""Tests for agent_api.validate_query — typed stateless SQL lint verb."""

from __future__ import annotations

import pytest

from dbt_charts.agent_api import QueryDiagnostic
from dbt_charts.agent_api.validate_query import validate_query


def test_validate_query_returns_typed_diagnostics_and_honors_suppress() -> None:
    clean = validate_query("SELECT 1")
    assert clean == []

    # Comma-join over two tables → WARN-MISSING-JOIN-PREDICATE (error severity).
    bad_sql = "SELECT a.x, b.y FROM t1 a, t2 b"
    findings = validate_query(bad_sql)
    assert findings, "expected at least one diagnostic for an implicit cross join"
    assert all(isinstance(f, QueryDiagnostic) for f in findings)
    codes = {f.code for f in findings}
    assert "WARN-MISSING-JOIN-PREDICATE" in codes

    suppressed = validate_query(bad_sql, suppress={"WARN-MISSING-JOIN-PREDICATE"})
    assert "WARN-MISSING-JOIN-PREDICATE" not in {f.code for f in suppressed}


def test_missing_join_predicate_prints_the_registered_fix_naming_ignore() -> None:
    """The validator leaves `recommendation` unset, so the registry's
    `fix_template` is what prints; it must name the exact suppression."""
    from dbt_charts.core.diagnostics.from_query_diagnostic import (
        from_query_diagnostic,
    )

    (finding,) = validate_query("SELECT a.x, b.y FROM t1 a, t2 b")
    assert finding.recommendation is None
    fix = from_query_diagnostic("q", finding).fix
    assert fix is not None
    assert "`ignore: [WARN-MISSING-JOIN-PREDICATE]`" in fix
    assert "document why" not in fix


_TSQL_TOP = "SELECT TOP 3 name FROM sys.objects"


def test_validate_query_applies_dialect_alias() -> None:
    # sqlglot knows 'tsql', not 'sqlserver'/'mssql'; the alias table bridges them.
    assert validate_query(_TSQL_TOP, dialect="tsql") == []
    assert validate_query(_TSQL_TOP, dialect="sqlserver") == []
    assert validate_query(_TSQL_TOP, dialect="mssql") == []


def test_validate_query_unknown_dialect_is_a_clear_error() -> None:
    with pytest.raises(ValueError, match="Unknown SQL dialect 'nope'"):
        validate_query("SELECT 1", dialect="nope")
