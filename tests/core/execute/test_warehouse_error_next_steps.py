"""A warehouse failure tells the agent what to fix, and never sends it at the SQL
when the SQL was fine."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.connections import classify_connection_error
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_BINDER_UNKNOWN_COLUMN,
    ERR_BINDER_UNKNOWN_RELATION,
    ERR_WAREHOUSE_AUTH,
    ERR_WAREHOUSE_CONNECTION,
    ERR_WAREHOUSE_PERMISSION,
    ERR_WAREHOUSE_RUNTIME,
)
from dbt_charts.core.execute.compose import ResolvedQuery
from dbt_charts.core.execute.query_result import (
    QueryResult,
    WarehouseStatementError,
    classify_warehouse_error,
    resolve_effective_row_limit,
)
from dbt_charts.core.project import Project
from dbt_charts.core.text.time_zones import UTC

from ..conftest import DbtHarness


def _classify(error: Exception, dialect: str) -> QueryResult:
    return classify_warehouse_error(
        f"{dialect} SQL execution", error, dialect, source="wh"
    )


@pytest.mark.parametrize(
    ("dialect", "text"),
    [
        ("postgres", "permission denied for table orders"),
        ("redshift", "permission denied for relation orders"),
        (
            "snowflake",
            "003001 (42501): SQL access control error:\n"
            "Insufficient privileges to operate on table 'ORDERS'",
        ),
        (
            "bigquery",
            "403 Access Denied: Table p:d.orders: Permission bigquery.tables.getData"
            " denied on table p:d.orders",
        ),
        (
            "databricks",
            "[INSUFFICIENT_PERMISSIONS] Insufficient privileges: User does not have"
            " SELECT on table 'main.d.orders'.",
        ),
        ("databricks", "PERMISSION_DENIED: User does not have USE SCHEMA on d"),
    ],
)
def test_denied_grant_names_the_source_and_says_the_sql_is_fine(
    dialect: str, text: str
) -> None:
    result = _classify(Exception(text), dialect)

    assert result.error is not None
    assert result.error.code is ERR_WAREHOUSE_PERMISSION
    message = str(result.error)
    assert "source 'wh'" in message
    assert text in message
    assert "Grant" in message and "switch" in message
    assert "Change the SQL" not in message


def test_postgres_sqlstate_42501_is_a_permission_failure() -> None:
    result = _classify(
        WarehouseStatementError("42501", "permission denied for table orders"),
        "postgres",
    )

    assert result.error is not None
    assert result.error.code is ERR_WAREHOUSE_PERMISSION


def test_bigquery_quota_403_is_not_a_permission_failure() -> None:
    result = _classify(
        Exception("403 Quota exceeded: Your project exceeded quota for queries"),
        "bigquery",
    )

    assert result.error is not None
    assert result.error.code is ERR_WAREHOUSE_RUNTIME


@pytest.mark.parametrize(
    ("dialect", "text"),
    [
        ("databricks", "Error during request to server: Invalid access token."),
        ("snowflake", "250001: JWT token is invalid. [uuid]"),
        ("snowflake", "390303: Invalid OAuth access token."),
        ("redshift", "The security token included in the request is invalid."),
        ("bigquery", "401 Request had invalid authentication credentials."),
    ],
)
def test_rejected_credentials_route_to_auth_not_the_sql(
    dialect: str, text: str
) -> None:
    result = _classify(Exception(text), dialect)

    assert result.error is not None
    assert result.error.code is ERR_WAREHOUSE_AUTH
    assert "The SQL is not at fault." in str(result.error)


def test_unknown_relation_off_duckdb_gives_the_inspect_command() -> None:
    result = _classify(Exception("Not found: Table p:d.t was not found"), "bigquery")

    assert result.error is not None
    assert result.error.code is ERR_BINDER_UNKNOWN_RELATION
    assert "`dct query wh --table <table>`" in str(result.error)


def test_unknown_column_off_duckdb_gives_the_inspect_command() -> None:
    result = _classify(Exception("Unrecognized name: nope at [1:8]"), "bigquery")

    assert result.error is not None
    assert result.error.code is ERR_BINDER_UNKNOWN_COLUMN
    assert "`dct query wh --table <table>`" in str(result.error)


def test_unknown_reference_on_duckdb_has_no_inspect_command() -> None:
    result = _classify(
        WarehouseStatementError("42S02", "Table with name nope does not exist!"),
        "duckdb",
    )

    assert result.error is not None
    assert result.error.code is ERR_BINDER_UNKNOWN_RELATION
    assert "dct query" not in str(result.error)


def _prepared() -> ResolvedQuery:
    return ResolvedQuery(
        sql="SELECT 1",
        params=(),
        setup_sql=(),
        limit=resolve_effective_row_limit(None),
        time_zone=UTC,
    )


def test_connection_failure_names_the_source_its_type_and_the_doctor_command(
    tmp_path: Path, local_project: Callable[..., Project]
) -> None:
    harness = DbtHarness(local_project(tmp_path), profile_type="bigquery")
    query = SqlQuery(sql="SELECT 1", source="analytics_bq")
    try:
        with patch(
            "dbt_charts.core.execute.backends.dbt_adapter_factory.build_adapter",
            side_effect=ValueError("Could not automatically determine credentials"),
        ):
            result = harness.run_resolved(
                _prepared(),
                query,
                {"type": "bigquery", "project": "p", "dataset": "d"},
            )
    finally:
        harness.close()

    assert result.error is not None
    assert result.error.code is ERR_WAREHOUSE_CONNECTION
    message = str(result.error)
    assert "source 'analytics_bq' (bigquery, from a dbt profile)" in message
    assert "target '" not in message
    assert "dct doctor --with-warehouse" in message


@pytest.mark.parametrize(
    ("source_type", "field"),
    [
        ("postgres", "host"),
        ("redshift", "host"),
        ("snowflake", "account"),
        ("databricks", "http_path"),
        ("trino", "host"),
        ("duckdb", "path"),
    ],
)
def test_unmatched_connection_failure_names_the_field_to_check(
    source_type: str, field: str
) -> None:
    probe = classify_connection_error(source_type, Exception("something unexpected"))

    assert f"`{field}`" in str(probe)


def test_bigquery_unmatched_connection_failure_names_both_credential_routes() -> None:
    probe = classify_connection_error("bigquery", Exception("something unexpected"))

    assert "gcloud auth application-default login" in str(probe)
    assert "`keyfile:`" in str(probe)
