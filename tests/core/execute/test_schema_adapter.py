"""Tests for the schema route handler."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError as PydanticValidationError

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.column_types import ColumnType
from dbt_charts.core.compile.models.query.normalized import SchemaQuery
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.env import ExecEnv, make_exec_env
from dbt_charts.core.execute.query_result import QueryResult
from dbt_charts.core.execute.schema_adapter import schema_rows

FAKE_SCHEMAS_ENVELOPE = {
    "sources": {
        "warehouse": {
            "schemas": {
                "analytics": {"table_count": 3},
                "raw": {"table_count": 1},
            }
        }
    },
    "_meta": {"retrieved_at": "2024-01-01", "sources_consulted": ["super_schema"]},
}

FAKE_TABLES_ENVELOPE = {
    "sources": {
        "warehouse": {
            "schemas": {
                "analytics": {
                    "tables": {
                        "orders": {"row_count": 1000, "description": "Order facts"},
                        "customers": {"row_count": 500, "description": "Customers"},
                    }
                }
            }
        }
    },
    "_meta": {"retrieved_at": "2024-01-01", "sources_consulted": ["super_schema"]},
}

# The resolver drops the schemas key entirely when the requested target is not
# found — whether the schema, table, or column is missing.  All error-path
# tests use this single canonical shape.
EMPTY_RESULT_ENVELOPE = {
    "sources": {"warehouse": {}},
    "_meta": {"retrieved_at": "2024-01-01", "sources_consulted": []},
}

FAKE_TABLE_PROFILE_ENVELOPE = {
    "sources": {
        "warehouse": {
            "schemas": {
                "analytics": {
                    "tables": {
                        "orders": {
                            "row_count": 1000,
                            "columns": {
                                "id": {
                                    "actual_type": "BIGINT",
                                    "role": "pk",
                                    "min_value": 1,
                                },
                                "customer_id": {"actual_type": "BIGINT", "role": "fk"},
                            },
                        }
                    }
                }
            }
        }
    },
    "_meta": {"retrieved_at": "2024-01-01", "sources_consulted": ["super_schema"]},
}

FAKE_COLUMN_PROFILE_ENVELOPE = {
    "sources": {
        "warehouse": {
            "schemas": {
                "analytics": {
                    "tables": {
                        "orders": {
                            "row_count": 1000,
                            "columns": {
                                "id": {
                                    "actual_type": "BIGINT",
                                    "role": "pk",
                                    "min_value": 1,
                                },
                            },
                        }
                    }
                }
            }
        }
    },
    "_meta": {"retrieved_at": "2024-01-01", "sources_consulted": ["super_schema"]},
}


@pytest.fixture
def registry(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> ExecEnv:
    return make_exec_env(project=local_project(tmp_path))


@pytest.fixture
def adapter(registry: ExecEnv) -> Callable[[SchemaQuery], QueryResult]:
    return partial(schema_rows, registry)


def _mock_resolver(**method_returns: dict) -> MagicMock:
    resolver = MagicMock()
    for method_name, return_value in method_returns.items():
        getattr(resolver, method_name).return_value = return_value
    return resolver


class TestSchemaAdapterColumnSchema:
    @staticmethod
    def _run(
        adapter: Callable[[SchemaQuery], QueryResult],
        query: SchemaQuery,
        **envelope: dict,
    ) -> QueryResult:
        with patch(
            "dbt_charts.core.inspect.cache_factory.build_resolver",
            return_value=_mock_resolver(**envelope),
        ):
            return adapter(query)

    def test_profile_table_types_known_kinds_and_json_for_varying(
        self, adapter: Callable[[SchemaQuery], QueryResult]
    ) -> None:
        result = self._run(
            adapter,
            SchemaQuery(source="warehouse", schema="analytics", table="orders"),
            profile_table=FAKE_TABLE_PROFILE_ENVELOPE,
        )
        assert result.schema["name"] == ColumnType(kind="string")
        assert result.schema["min_value"] == ColumnType(kind="json")
        assert (
            list(result.schema)
            == result.columns
            == [
                "name",
                "actual_type",
                "role",
                "min_value",
            ]
        )

    def test_projection_keeps_schema_in_projected_order(
        self, adapter: Callable[[SchemaQuery], QueryResult]
    ) -> None:
        result = self._run(
            adapter,
            SchemaQuery(
                source="warehouse", schema="analytics", fields=["row_count", "name"]
            ),
            list_tables=FAKE_TABLES_ENVELOPE,
        )
        assert list(result.schema) == ["row_count", "name"]
        assert result.schema["row_count"] == ColumnType(kind="integer")

    def test_a_key_outside_the_declared_set_raises(
        self, adapter: Callable[[SchemaQuery], QueryResult]
    ) -> None:
        envelope = {
            "sources": {
                "warehouse": {"schemas": {"raw": {"surprise": 1}}},
            },
            "_meta": {},
        }
        with pytest.raises(ExecutionError, match="surprise"):
            self._run(adapter, SchemaQuery(source="warehouse"), list_schemas=envelope)


class TestSchemaAdapterListSchemas:
    def test_list_schemas_returns_schema_rows(
        self, adapter: Callable[[SchemaQuery], QueryResult]
    ) -> None:
        resolver = _mock_resolver(list_schemas=FAKE_SCHEMAS_ENVELOPE)
        query = SchemaQuery(source="warehouse")
        with patch(
            "dbt_charts.core.inspect.cache_factory.build_resolver",
            return_value=resolver,
        ):
            result = adapter(query)
        assert result.error is None
        names = {r["name"] for r in result.data}
        assert names == {"analytics", "raw"}
        analytics_row = next(r for r in result.data if r["name"] == "analytics")
        assert analytics_row["table_count"] == 3

    def test_list_schemas_calls_list_schemas_with_source(
        self, adapter: Callable[[SchemaQuery], QueryResult]
    ) -> None:
        resolver = _mock_resolver(list_schemas=FAKE_SCHEMAS_ENVELOPE)
        query = SchemaQuery(source="warehouse")
        with patch(
            "dbt_charts.core.inspect.cache_factory.build_resolver",
            return_value=resolver,
        ):
            adapter(query)
        resolver.list_schemas.assert_called_once_with("warehouse")

    def test_list_schemas_respects_limit(
        self, adapter: Callable[[SchemaQuery], QueryResult]
    ) -> None:
        resolver = _mock_resolver(list_schemas=FAKE_SCHEMAS_ENVELOPE)
        query = SchemaQuery(source="warehouse", limit=1)
        with patch(
            "dbt_charts.core.inspect.cache_factory.build_resolver",
            return_value=resolver,
        ):
            result = adapter(query)
        assert result.error is None
        assert len(result.data) == 1


class TestSchemaAdapterListTables:
    def test_list_tables_returns_table_rows(
        self, adapter: Callable[[SchemaQuery], QueryResult]
    ) -> None:
        resolver = _mock_resolver(list_tables=FAKE_TABLES_ENVELOPE)
        query = SchemaQuery(source="warehouse", schema="analytics")
        with patch(
            "dbt_charts.core.inspect.cache_factory.build_resolver",
            return_value=resolver,
        ):
            result = adapter(query)
        assert result.error is None
        names = {r["name"] for r in result.data}
        assert names == {"orders", "customers"}
        orders_row = next(r for r in result.data if r["name"] == "orders")
        assert orders_row["row_count"] == 1000

    def test_list_tables_calls_list_tables_with_source_and_schema(
        self, adapter: Callable[[SchemaQuery], QueryResult]
    ) -> None:
        resolver = _mock_resolver(list_tables=FAKE_TABLES_ENVELOPE)
        query = SchemaQuery(source="warehouse", schema="analytics")
        with patch(
            "dbt_charts.core.inspect.cache_factory.build_resolver",
            return_value=resolver,
        ):
            adapter(query)
        resolver.list_tables.assert_called_once_with("warehouse", "analytics")

    def test_list_tables_unknown_schema_returns_error(
        self, adapter: Callable[[SchemaQuery], QueryResult]
    ) -> None:
        resolver = _mock_resolver(list_tables=EMPTY_RESULT_ENVELOPE)
        query = SchemaQuery(source="warehouse", schema="typo_schema")
        with patch(
            "dbt_charts.core.inspect.cache_factory.build_resolver",
            return_value=resolver,
        ):
            result = adapter(query)
        assert result.error is not None
        assert "typo_schema" in str(result.error)
        assert result.data == []

    def test_list_tables_respects_limit(
        self, adapter: Callable[[SchemaQuery], QueryResult]
    ) -> None:
        resolver = _mock_resolver(list_tables=FAKE_TABLES_ENVELOPE)
        query = SchemaQuery(source="warehouse", schema="analytics", limit=1)
        with patch(
            "dbt_charts.core.inspect.cache_factory.build_resolver",
            return_value=resolver,
        ):
            result = adapter(query)
        assert result.error is None
        assert len(result.data) == 1


class TestSchemaAdapterProfileTable:
    def test_profile_table_returns_column_rows(
        self, adapter: Callable[[SchemaQuery], QueryResult]
    ) -> None:
        resolver = _mock_resolver(profile_table=FAKE_TABLE_PROFILE_ENVELOPE)
        query = SchemaQuery(source="warehouse", schema="analytics", table="orders")
        with patch(
            "dbt_charts.core.inspect.cache_factory.build_resolver",
            return_value=resolver,
        ):
            result = adapter(query)
        assert result.error is None
        names = {r["name"] for r in result.data}
        assert names == {"id", "customer_id"}
        id_row = next(r for r in result.data if r["name"] == "id")
        assert id_row["role"] == "pk"

    def test_profile_table_not_found_returns_error(
        self, adapter: Callable[[SchemaQuery], QueryResult]
    ) -> None:
        resolver = _mock_resolver(profile_table=EMPTY_RESULT_ENVELOPE)
        query = SchemaQuery(
            source="warehouse", schema="analytics", table="missing_table"
        )
        with patch(
            "dbt_charts.core.inspect.cache_factory.build_resolver",
            return_value=resolver,
        ):
            result = adapter(query)
        assert result.error is not None
        assert "missing_table" in str(result.error)
        assert result.data == []


class TestSchemaAdapterProfileColumn:
    def test_profile_column_returns_single_row(
        self, adapter: Callable[[SchemaQuery], QueryResult]
    ) -> None:
        resolver = _mock_resolver(profile_column=FAKE_COLUMN_PROFILE_ENVELOPE)
        query = SchemaQuery(
            source="warehouse", schema="analytics", table="orders", column="id"
        )
        with patch(
            "dbt_charts.core.inspect.cache_factory.build_resolver",
            return_value=resolver,
        ):
            result = adapter(query)
        assert result.error is None
        assert len(result.data) == 1
        assert result.data[0]["name"] == "id"
        assert result.data[0]["role"] == "pk"

    def test_profile_column_table_missing_returns_error(
        self, adapter: Callable[[SchemaQuery], QueryResult]
    ) -> None:
        # Both profile_column and the fallback profile_table probe return empty —
        # the resolver drops the schemas key entirely when the table is missing.
        resolver = _mock_resolver(
            profile_column=EMPTY_RESULT_ENVELOPE, profile_table=EMPTY_RESULT_ENVELOPE
        )
        query = SchemaQuery(
            source="warehouse", schema="analytics", table="missing_table", column="id"
        )
        with patch(
            "dbt_charts.core.inspect.cache_factory.build_resolver",
            return_value=resolver,
        ):
            result = adapter(query)
        assert result.error is not None
        assert "missing_table" in str(result.error)
        assert result.data == []

    def test_profile_column_not_found_returns_error(
        self, adapter: Callable[[SchemaQuery], QueryResult]
    ) -> None:
        # profile_column returns empty; profile_table confirms the table exists —
        # so the column itself is missing.
        resolver = _mock_resolver(
            profile_column=EMPTY_RESULT_ENVELOPE,
            profile_table=FAKE_TABLE_PROFILE_ENVELOPE,
        )
        query = SchemaQuery(
            source="warehouse", schema="analytics", table="orders", column="nonexistent"
        )
        with patch(
            "dbt_charts.core.inspect.cache_factory.build_resolver",
            return_value=resolver,
        ):
            result = adapter(query)
        assert result.error is not None
        assert "nonexistent" in str(result.error)
        assert result.data == []


class TestFieldsProjection:
    """`fields:` projects (and orders) the returned row keys — the schema-query
    counterpart of a SQL SELECT list. Metadata key sets vary with profiling
    depth, so a listing view declares the columns it wants at the query."""

    def test_fields_projects_and_orders_rows(
        self, adapter: Callable[[SchemaQuery], QueryResult]
    ) -> None:
        resolver = _mock_resolver(list_tables=FAKE_TABLES_ENVELOPE)
        query = SchemaQuery(
            source="warehouse", schema="analytics", fields=["name", "row_count"]
        )
        with patch(
            "dbt_charts.core.inspect.cache_factory.build_resolver",
            return_value=resolver,
        ):
            result = adapter(query)
        assert result.error is None
        assert all(list(r.keys()) == ["name", "row_count"] for r in result.data)
        orders_row = next(r for r in result.data if r["name"] == "orders")
        assert orders_row["row_count"] == 1000
        assert "description" not in orders_row

    def test_fields_absent_key_projects_none(
        self, adapter: Callable[[SchemaQuery], QueryResult]
    ) -> None:
        """A projected field a row lacks yields None — a stable column set even
        when profiling depth varies row to row, never a silently absent key."""
        resolver = _mock_resolver(list_tables=FAKE_TABLES_ENVELOPE)
        query = SchemaQuery(
            source="warehouse", schema="analytics", fields=["name", "grain"]
        )
        with patch(
            "dbt_charts.core.inspect.cache_factory.build_resolver",
            return_value=resolver,
        ):
            result = adapter(query)
        assert result.error is None
        assert all(r["grain"] is None for r in result.data)

    def test_fields_applies_to_source_listing(
        self, adapter: Callable[[SchemaQuery], QueryResult]
    ) -> None:
        registry = MagicMock()
        registry.sources.all.return_value = {
            "wh": {"type": "duckdb", "path": "/tmp/x.db"}
        }
        result = schema_rows(registry, SchemaQuery(fields=["name"]))
        assert result.error is None
        assert result.data == [{"name": "wh"}]

    def test_empty_fields_list_is_rejected(self) -> None:
        """An empty projection is meaningless — reject it at the model."""
        with pytest.raises(PydanticValidationError):
            SchemaQuery(source="warehouse", schema="analytics", fields=[])

    def test_jinja_in_fields_is_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            SchemaQuery(source="warehouse", fields=["name", "{{ col }}"])
