"""A classified warehouse error reads in the registry's words."""

from __future__ import annotations

from pathlib import Path

from dbt_charts.agent_api.query import execute_query
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.adapters.base import classify_warehouse_error

SNOWFLAKE_MISSING = (
    "002003 (42S02): SQL compilation error:\n"
    "Object 'ORDERS' does not exist or not authorized."
)


def test_snowflake_unknown_relation_says_a_missing_grant_looks_the_same() -> None:
    error = classify_warehouse_error(
        "snowflake SQL execution", Exception(SNOWFLAKE_MISSING), "snowflake"
    ).error

    assert error is not None
    assert str(error) == (
        f"Warehouse could not find a table or view: {SNOWFLAKE_MISSING}. "
        "Check that the relation exists and the connection can read it. "
        "Snowflake reports a missing object and a missing grant identically, "
        "so check the name and schema for a typo before asking for access."
    )


def test_snowflake_note_keeps_the_warehouse_text_as_detail() -> None:
    error = classify_warehouse_error(
        "snowflake SQL execution", Exception(SNOWFLAKE_MISSING), "snowflake"
    ).error

    assert error is not None
    assert error.detail == SNOWFLAKE_MISSING


def test_unknown_relation_on_another_warehouse_has_no_snowflake_note() -> None:
    error = classify_warehouse_error(
        "bigquery SQL execution",
        Exception("Not found: Table p:d.t was not found"),
        "bigquery",
    ).error

    assert error is not None
    assert "Snowflake" not in str(error)
    assert str(error).endswith("the connection can read it.")


def test_duckdb_unknown_relation_text_is_unchanged(tmp_path: Path) -> None:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  db:\n    type: duckdb\n    path: ':memory:'\n"
    )
    registry = build_adapter_registry(FilesystemProject(tmp_path), read_only=False)

    result = execute_query("select * from nope", source="db", adapter_registry=registry)

    assert result.errors[0].startswith(
        "Warehouse could not find a table or view: Catalog Error: "
        "Table with name nope does not exist!"
    )
    assert result.errors[0].endswith(
        "Check that the relation exists and the connection can read it."
    )
    assert "Snowflake" not in result.errors[0]


def test_unclassified_rejection_uses_the_registry_words() -> None:
    error = classify_warehouse_error(
        "bigquery SQL execution", Exception("boom"), "bigquery"
    ).error

    assert error is not None
    assert str(error).startswith("Warehouse rejected the query: boom. ")
