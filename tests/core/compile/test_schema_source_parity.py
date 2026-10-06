"""Parity tests: derived schema maps must match central model sources.

Each test verifies that a constant/map is correctly derived from its
authoritative Pydantic model, so adding a new type to the model
automatically keeps the derived value in sync.
"""

from __future__ import annotations

from typing import get_args, get_type_hints

import pytest

# ---------------------------------------------------------------------------
# Source registry parity
# ---------------------------------------------------------------------------


def _source_type_str(cls: type) -> str:
    """Extract the Literal type string from a SourceConfig subclass."""
    annotation = get_type_hints(cls)["type"]
    return get_args(annotation)[0]


def test_valid_source_types_covers_all_union_members():
    """VALID_SOURCE_TYPES must contain every type string from SourceConfig union."""
    from dbt_charts.core.compile.models.source import VALID_SOURCE_TYPES, SourceConfig

    derived = frozenset(_source_type_str(m) for m in get_args(SourceConfig))
    assert derived == VALID_SOURCE_TYPES, (
        f"VALID_SOURCE_TYPES diverges from SourceConfig union.\n"
        f"  Extra in VALID_SOURCE_TYPES: {VALID_SOURCE_TYPES - derived}\n"
        f"  Missing from VALID_SOURCE_TYPES: {derived - VALID_SOURCE_TYPES}"
    )


def test_parse_source_config_accepts_all_union_member_types():
    """parse_source_config must return the right class for each SourceConfig type string."""
    from dbt_charts.core.compile.models.source import SourceConfig, parse_source_config

    # Minimal valid dict for each source type
    _MINIMAL = {
        "postgres": {
            "host": "h",
            "port": 5432,
            "dbname": "db",
            "user": "u",
            "password": "p",
        },
        "snowflake": {
            "account": "acct",
            "user": "u",
            "password": "p",
            "database": "db",
            "warehouse": "wh",
            "schema": "sc",
        },
        "bigquery": {"project": "proj", "dataset": "ds"},
        "redshift": {
            "host": "h",
            "port": 5439,
            "dbname": "db",
            "user": "u",
            "password": "p",
        },
        "mysql": {
            "host": "h",
            "port": 3306,
            "database": "db",
            "user": "u",
            "password": "p",
        },
        "trino": {
            "host": "h",
            "port": 8080,
            "database": "cat",
            "user": "u",
            "schema": "sc",
        },
        "databricks": {
            "host": "h.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/abc",
            "token": "tok",
            "catalog": "main",
        },
        "duckdb": {"path": "db.duckdb"},
        "sqlite": {"path": "db.sqlite"},
        "csv": {"files": {"data": "x.csv"}},
        "parquet": {"files": {"data": "x.parquet"}},
        "json": {"files": {"data": "x.json"}},
        "http": {"url": "https://api.example.com"},
        "dbt_profile": {"profile": "my_profile"},
    }

    for cls in get_args(SourceConfig):
        type_str = _source_type_str(cls)
        assert type_str in _MINIMAL, (
            f"No minimal data template for {type_str!r} — update this test"
        )
        data = {"type": type_str, **_MINIMAL[type_str]}
        result = parse_source_config(data)
        assert type(result).__name__ == cls.__name__, (
            f"parse_source_config({type_str!r}) returned {type(result).__name__}, expected {cls.__name__}"
        )


def test_parse_source_config_rejects_unknown_type():
    from dbt_charts.core.compile.models.source import parse_source_config

    with pytest.raises(ValueError, match="Unknown source type"):
        parse_source_config({"type": "nonexistent_warehouse_xyz"})


# ---------------------------------------------------------------------------
# Prompt schema source names parity
# ---------------------------------------------------------------------------


def test_source_config_names_in_prompt_matches_union():
    """_SOURCE_CONFIG_NAMES in prompt.py must list SourceConfig union class names in order."""
    from dbt_charts.core.compile.models.source import SourceConfig
    from dbt_charts.core.compile.schema.renderers import prompt

    expected = [m.__name__ for m in get_args(SourceConfig)]
    assert list(prompt._SOURCE_CONFIG_NAMES) == expected, (
        f"_SOURCE_CONFIG_NAMES diverges from SourceConfig union.\n"
        f"  prompt.py: {prompt._SOURCE_CONFIG_NAMES}\n"
        f"  expected:  {expected}"
    )


# ---------------------------------------------------------------------------
# Query type parity
# ---------------------------------------------------------------------------


def test_valid_query_types_matches_authored_query_literal():
    """VALID_QUERY_TYPES must be exactly the four authored query types.

    Pinned as an explicit literal set: VALID_QUERY_TYPES is itself derived from
    the AuthoredQuery union, so deriving the expectation from the same union
    would be a tautology. Adding or removing a query type must consciously
    update this list.
    """
    from dbt_charts.core.compile.models.query.normalized import VALID_QUERY_TYPES

    expected = {
        "sql",
        "http",
        "values",
        "schema",
    }
    assert expected == VALID_QUERY_TYPES


# ---------------------------------------------------------------------------
# AI tool schema parity
# ---------------------------------------------------------------------------


def test_docs_tool_schema_matches_docs_args_model():
    """DOCS input_schema must equal DocsArgs.model_json_schema()."""
    from dbt_charts.agent_api.docs import DocsArgs
    from dbt_charts.ai.tool_schemas import DOCS

    expected = DocsArgs.model_json_schema(by_alias=True)
    assert DOCS["input_schema"] == expected, (
        "DOCS input_schema diverges from DocsArgs.model_json_schema().\n"
        "After fix: DOCS = _ai_tool('docs', DocsArgs) should be used."
    )


def test_execute_query_args_rejects_missing_sql():
    """ExecuteQueryArgs.model_validate raises when sql is absent.

    _handle_query must validate through this model rather than using
    args.get('sql', '') which silently accepts a missing sql field.
    """
    from pydantic import ValidationError

    from dbt_charts.agent_api.query import ExecuteQueryArgs

    with pytest.raises(ValidationError):
        ExecuteQueryArgs.model_validate({})
