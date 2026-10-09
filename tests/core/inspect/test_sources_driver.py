"""Per-level introspection of a driver source, answered from one bulk query.

A driver source has no dbt adapter, so `DbtSchemaSource` cannot serve it. The
tree `bulk_schema_for_config` already builds from `information_schema` answers
the schema, table and column levels directly.
"""

from __future__ import annotations

from unittest.mock import patch

import pyarrow as pa
import pytest

from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.source import LakeComputeTokenSource
from dbt_charts.core.execute.adapters import AdapterRegistry
from dbt_charts.core.inspect.resolver import LayeredSchemaResolver
from dbt_charts.core.inspect.sources.driver import DriverSchemaSource

from ..._fake_driver import fake_lakecompute_driver
from .test_resolver import _NonFilesystemProject

_COLUMNS = pa.table(
    {
        "table_schema": ["main", "main", "raw"],
        "table_name": ["orders", "orders", "events"],
        "column_name": ["id", "total", "at"],
        "data_type": ["BIGINT", "DOUBLE", "TIMESTAMP"],
    }
)


def _source() -> DriverSchemaSource:
    return DriverSchemaSource(LakeComputeTokenSource(type="lakecompute", token="t"))


def test_schemas_come_from_the_bulk_tree() -> None:
    with fake_lakecompute_driver(_COLUMNS):
        assert _source().list_schemas() == {"schemas": {"main": {}, "raw": {}}}


def test_tables_of_a_schema() -> None:
    with fake_lakecompute_driver(_COLUMNS):
        assert _source().list_tables("main") == {
            "tables": {"orders": {"kind": "table"}}
        }


def test_an_unknown_schema_has_no_tables() -> None:
    with fake_lakecompute_driver(_COLUMNS):
        assert _source().list_tables("nope") is None


def test_a_tables_columns_carry_their_types() -> None:
    with fake_lakecompute_driver(_COLUMNS):
        profile = _source().profile_table("main", "orders")
    assert profile is not None
    assert profile["columns"] == {
        "id": {"actual_type": "BIGINT"},
        "total": {"actual_type": "DOUBLE"},
    }
    assert profile["table_exists"] is True


def test_an_unknown_table_is_none() -> None:
    with fake_lakecompute_driver(_COLUMNS):
        assert _source().profile_table("main", "nope") is None


def test_the_resolver_routes_lakecompute_to_the_driver_source_not_dbt() -> None:
    registry = AdapterRegistry(
        project=_NonFilesystemProject(),
        project_sources=ProjectSourcesConfig(
            sources={"lake": {"type": "lakecompute", "token": "t"}}
        ),
    )
    resolver = LayeredSchemaResolver(
        cache=None, adapter_registry=registry, project=registry.project
    )
    with (
        fake_lakecompute_driver(_COLUMNS),
        patch(
            "dbt_charts.core.inspect.resolver.build_adapter",
            side_effect=AssertionError("lake compute must not build a dbt adapter"),
        ),
    ):
        result = resolver.list_schemas("lake")
    assert result["sources"]["lake"]["schemas"] == {"main": {}, "raw": {}}


def test_a_raw_registry_dict_is_parsed_not_trusted() -> None:
    """A bad key fails extra-forbidden rather than reaching the driver."""
    registry = AdapterRegistry(
        project=_NonFilesystemProject(),
        project_sources=ProjectSourcesConfig(
            sources={"lake": {"type": "lakecompute", "token": "t", "schema": "x"}}
        ),
    )
    resolver = LayeredSchemaResolver(
        cache=None, adapter_registry=registry, project=registry.project
    )
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        resolver.list_schemas("lake")
