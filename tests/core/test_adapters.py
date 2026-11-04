"""Tests for query adapters.

Tests the dbt_charts.core.execute.adapters module for query execution.

Uses the unified query interface with type-specific query classes.
"""

import json
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.column_types import literal_schema
from dbt_charts.core.compile.models.query.normalized import (
    SqlQuery,
    ValuesQuery,
)
from dbt_charts.core.compile.models.source import (
    DuckDBSourceConfig,
)
from dbt_charts.core.dbt_manifest import LoadedManifest, RefIndex, ref_index
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.adapters import (
    QueryResult,
    ValuesAdapter,
    build_adapter_registry,
)
from dbt_charts.core.execute.adapters.dbt_utils import (
    DbtRefResolver,
    resolve_dbt_refs_with_provenance,
)
from dbt_charts.core.execute.dbt_jinja import has_dbt_jinja
from dbt_charts.core.execute.duckdb_config import normalize_duckdb_config

from .._backend_runner import DuckDBRunner

pytestmark = pytest.mark.usefixtures("typed_via_execute")


def _ri(raw: dict) -> RefIndex:
    return ref_index(
        LoadedManifest(raw=raw, relpath="target/manifest.json", version="v1")
    )


class TestQueryResult:
    """Tests for QueryResult class."""

    def test_successful_result_and_columns_extraction(self):
        """Test successful query result with data content and column extraction."""
        data = [
            {"col1": 1, "col2": "a"},
            {"col1": 2, "col2": "b"},
        ]
        result = QueryResult(data=data, schema=literal_schema(data))

        assert result.is_success
        assert result.error is None
        assert len(result.data) == 2
        assert result.data[0]["col1"] == 1
        assert result.data[0]["col2"] == "a"
        assert "col1" in result.columns
        assert "col2" in result.columns

    def test_error_result(self):
        """Test error query result."""
        result = QueryResult(data=[], error=QueryError("Query failed"))

        assert not result.is_success
        assert str(result.error) == "Query failed"


class TestSqlExecution:
    """Tests for SQL execution."""

    def test_duckdb_source_query(self):
        """Test that DuckDB source config executes queries correctly."""

        adapter = DuckDBRunner(
            source_config=DuckDBSourceConfig(type="duckdb"),
        )

        query = SqlQuery(sql="SELECT 1 as value, 'test' as name", source="test_profile")

        result = adapter.execute(
            query, source_config=DuckDBSourceConfig(type="duckdb", path=":memory:")
        )

        assert result.is_success, f"Query failed: {result.error}"
        assert len(result.data) == 1
        assert result.data[0]["value"] == 1
        assert result.data[0]["name"] == "test"

    def test_source_connection_reused(self):
        """Test that source connections are cached across queries."""
        from dbt_charts.core.compile.models.source import DuckDBSourceConfig

        adapter = DuckDBRunner(
            source_config=DuckDBSourceConfig(type="duckdb"),
        )
        source_config = DuckDBSourceConfig(type="duckdb", path=":memory:")

        query1 = SqlQuery(sql="SELECT 1 as value", source="test_profile")
        result1 = adapter.execute(query1, source_config=source_config)
        assert result1.is_success

        sources = adapter.backend._tls.sources
        conns_after_first = len(sources)

        query2 = SqlQuery(sql="SELECT 2 as value", source="test_profile")
        result2 = adapter.execute(query2, source_config=source_config)
        assert result2.is_success
        assert result2.data[0]["value"] == 2

        # Same source config should not create additional connections
        assert len(adapter.backend._tls.sources) == conns_after_first

    def test_cte_replaces_init_sql_pattern(self):
        """Test that CTEs serve as the replacement for init_sql views."""

        adapter = DuckDBRunner(
            source_config=DuckDBSourceConfig(type="duckdb"),
        )

        query = SqlQuery(
            sql="""
                WITH view_a AS (SELECT 'a' as letter),
                     view_b AS (SELECT 'b' as letter)
                SELECT * FROM view_a UNION ALL SELECT * FROM view_b ORDER BY letter
            """,
            source="test_profile",
        )

        result = adapter.execute(
            query, source_config=DuckDBSourceConfig(type="duckdb", path=":memory:")
        )
        assert result.is_success
        assert len(result.data) == 2
        assert result.data[0]["letter"] == "a"
        assert result.data[1]["letter"] == "b"

    def test_ref_resolution_works_via_target_manifest(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """target/manifest.json enables ref() resolution for the SQL adapter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            manifest = {
                "nodes": {
                    "model.project.orders": {
                        "resource_type": "model",
                        "name": "orders",
                        "schema": "analytics",
                        "alias": "fct_orders",
                    }
                },
                "sources": {},
            }
            (project_path / "target").mkdir()
            with Path(project_path / "target" / "manifest.json").open(
                "w", encoding="utf-8"
            ) as f:
                json.dump(manifest, f)

            refs = DbtRefResolver(local_project(project_path))

            resolved_sql, _ = refs.resolve("SELECT * FROM {{ ref('orders') }}")
            assert resolved_sql == "SELECT * FROM analytics.fct_orders"


class TestAdapterRegistry:
    """Tests for adapter registry."""

    def test_execute_returns_query_result(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test registry execute returns QueryResult."""
        registry = build_adapter_registry(local_project(Path("/tmp")))

        sql_query = SqlQuery(sql="SELECT 1", source="test_profile")
        result = registry.execute(sql_query)

        assert isinstance(result, QueryResult)


class TestResolveDbtRefs:
    """Ref/source substitution — relation_name preference and BigQuery safety."""

    def test_ref_prefers_relation_name(self):
        """relation_name from manifest should be used when present."""
        manifest = _ri(
            {
                "nodes": {
                    "model.analytics.customers": {
                        "resource_type": "model",
                        "name": "customers",
                        "schema": "analytics",
                        "alias": "customers",
                        "relation_name": "`my-project`.`analytics`.`customers`",
                    }
                },
                "sources": {},
            }
        )
        sql = "SELECT * FROM {{ ref('customers') }}"
        assert resolve_dbt_refs_with_provenance(sql, manifest)[0] == (
            "SELECT * FROM `my-project`.`analytics`.`customers`"
        )

    def test_source_prefers_relation_name(self):
        """source() should use relation_name when present."""
        manifest = _ri(
            {
                "nodes": {},
                "sources": {
                    "source.analytics.stripe.payments": {
                        "source_name": "stripe",
                        "name": "payments",
                        "schema": "raw_stripe",
                        "relation_name": "`my-project`.`raw_stripe`.`payments`",
                    }
                },
            }
        )
        sql = "SELECT * FROM {{ source('stripe', 'payments') }}"
        assert resolve_dbt_refs_with_provenance(sql, manifest)[0] == (
            "SELECT * FROM `my-project`.`raw_stripe`.`payments`"
        )

    def test_ref_falls_back_to_schema_alias(self):
        """Without relation_name, ref() should use schema.alias as before."""
        manifest = _ri(
            {
                "nodes": {
                    "model.project.orders": {
                        "resource_type": "model",
                        "name": "orders",
                        "schema": "sales",
                        "alias": "fct_orders",
                    }
                },
                "sources": {},
            }
        )
        sql = "SELECT * FROM {{ ref('orders') }}"
        assert (
            resolve_dbt_refs_with_provenance(sql, manifest)[0]
            == "SELECT * FROM sales.fct_orders"
        )

    def test_source_falls_back_to_schema_table(self):
        """Without relation_name, source() should use schema.table as before."""
        manifest = _ri(
            {
                "nodes": {},
                "sources": {
                    "source.proj.raw.events": {
                        "source_name": "raw",
                        "name": "events",
                        "schema": "raw_data",
                    }
                },
            }
        )
        sql = "SELECT * FROM {{ source('raw', 'events') }}"
        assert (
            resolve_dbt_refs_with_provenance(sql, manifest)[0]
            == "SELECT * FROM raw_data.events"
        )

    def test_bigquery_three_part_relation_name_preserved(self):
        """BigQuery project.dataset.table relation_name must pass through intact."""
        manifest = _ri(
            {
                "nodes": {
                    "model.ft.dim_accounts": {
                        "resource_type": "model",
                        "name": "dim_accounts",
                        "schema": "dbt_prod",
                        "alias": "dim_accounts",
                        "relation_name": "`fivetran-prod`.`dbt_prod`.`dim_accounts`",
                    }
                },
                "sources": {},
            }
        )
        sql = "SELECT * FROM {{ ref('dim_accounts') }}"
        assert resolve_dbt_refs_with_provenance(sql, manifest)[0] == (
            "SELECT * FROM `fivetran-prod`.`dbt_prod`.`dim_accounts`"
        )

    def test_generate_schema_name_override_respected_via_relation_name(self):
        """When generate_schema_name changes the schema, relation_name carries that."""
        manifest = _ri(
            {
                "nodes": {
                    "model.ft.stg_salesforce": {
                        "resource_type": "model",
                        "name": "stg_salesforce",
                        "schema": "staging",
                        "alias": "stg_salesforce",
                        "relation_name": "`fivetran-prod`.`custom_staging_schema`.`stg_salesforce`",
                    }
                },
                "sources": {},
            }
        )
        sql = "SELECT * FROM {{ ref('stg_salesforce') }}"
        resolved = resolve_dbt_refs_with_provenance(sql, manifest)[0]
        # Must use custom_staging_schema from relation_name, not "staging" from schema field
        assert "custom_staging_schema" in resolved
        assert "staging." not in resolved


class TestValuesAdapter:
    """Tests for values adapter."""

    def test_execute_returns_rows(self):
        """Test values adapter returns inline rows as data."""
        adapter = ValuesAdapter()
        rows = [
            {"month": "Jan", "revenue": 100},
            {"month": "Feb", "revenue": 140},
            {"month": "Mar", "revenue": 180},
        ]
        query = ValuesQuery(rows=rows)
        result = adapter.execute(query)

        assert result.is_success
        assert result.data == rows
        assert len(result.data) == 3

    def test_execute_empty_rows(self):
        """Test values adapter handles empty rows."""
        adapter = ValuesAdapter()
        query = ValuesQuery(rows=[])
        result = adapter.execute(query)

        assert result.is_success
        assert result.data == []

    def test_execute_with_limit(self):
        """Test values adapter respects limit."""
        adapter = ValuesAdapter()
        rows = [{"a": i} for i in range(10)]
        query = ValuesQuery(rows=rows, limit=3)
        result = adapter.execute(query)

        assert result.is_success
        assert len(result.data) == 3
        assert result.data == [{"a": 0}, {"a": 1}, {"a": 2}]

    def test_registry_executes_values_query(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test adapter registry can execute values queries end-to-end."""
        registry = build_adapter_registry(local_project(Path("/tmp")))
        rows = [{"x": 1, "y": 2}, {"x": 3, "y": 4}]
        query = ValuesQuery(rows=rows)
        result = registry.execute(query)

        assert result.is_success
        assert result.data == rows


class TestBuildAdapterRegistry:
    """Tests for build_adapter_registry factory function."""

    def test_read_only_propagates_to_duckdb_backend(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """read_only=True should propagate to the DuckDB backend."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        registry = build_adapter_registry(
            local_project(Path("/tmp/test")), read_only=True
        )
        backend = registry._backends.for_source(  # noqa: SLF001
            DuckDBSourceConfig(type="duckdb")
        )
        assert backend.read_only is True

    def test_read_only_false_propagates(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """read_only=False should propagate to the DuckDB backend."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        registry = build_adapter_registry(
            local_project(Path("/tmp/test")), read_only=False
        )
        backend = registry._backends.for_source(  # noqa: SLF001
            DuckDBSourceConfig(type="duckdb")
        )
        assert backend.read_only is False

    def test_dbt_project_linked_via_sibling_discovery(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """The dbt project is linked when dbt_project.yml sits next to dbt_charts.yml."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            # Sibling rule: dbt_project.yml must sit next to dbt_charts.yml.
            (project_dir / "dbt_charts.yml").write_text("# marker\n")
            (project_dir / "dbt_project.yml").write_text("name: test\n")

            registry = build_adapter_registry(local_project(project_dir))
            assert registry.dbt_context is not None

    def test_no_dbt_project_linked_without_path(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """No dbt project is linked when no dbt_project.yml exists at the project root."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = build_adapter_registry(local_project(Path(tmpdir)))
            assert registry.dbt_context is None

    def test_project_set_on_registry(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """project should be set on the returned registry."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        root = Path("/tmp/test").resolve()
        registry = build_adapter_registry(local_project(root))
        assert registry.project.root == root


class TestResolveDuckdbConfig:
    """DuckDBBackend._resolve_duckdb_config falls back to the backend-level default."""

    def test_no_source_config_returns_adapter_default(self) -> None:

        adapter = DuckDBRunner(
            source_config=DuckDBSourceConfig(type="duckdb"),
            duckdb_config={"enable_external_access": True},
        )
        result = adapter.backend._resolve_duckdb_config({})
        assert result == {"enable_external_access": True}

    def test_source_config_overrides_adapter_default(self) -> None:

        adapter = DuckDBRunner(
            source_config=DuckDBSourceConfig(type="duckdb"),
            duckdb_config={"enable_external_access": True},
        )
        result = adapter.backend._resolve_duckdb_config(
            {"duckdb_config": {"enable_external_access": False}}
        )
        assert result == {"enable_external_access": False}

    def test_no_source_no_adapter_returns_none(self) -> None:

        adapter = DuckDBRunner(
            source_config=DuckDBSourceConfig(type="duckdb"),
        )
        result = adapter.backend._resolve_duckdb_config({})
        assert result is None

    def test_unknown_key_raises_value_error(self) -> None:
        """Unknown duckdb_config keys raise ValueError with the key named — no silent drop."""
        with pytest.raises(ValueError, match="threads"):
            normalize_duckdb_config({"duckdb_config": {"threads": 4}})

    def test_unknown_key_names_allowed_keys_in_error(self) -> None:
        """Error message lists the allowed keys so the user knows what to use."""
        with pytest.raises(ValueError, match="enable_external_access"):
            normalize_duckdb_config(
                {"duckdb_config": {"threads": 4, "memory_limit": "4GB"}}
            )


class TestHasDbtJinja:
    """H2+H6: shared dbt Jinja detection — single source of truth for both adapters."""

    def test_ref_standard_spaces(self) -> None:
        assert has_dbt_jinja("SELECT * FROM {{ ref('orders') }}")

    def test_ref_no_spaces(self) -> None:
        assert has_dbt_jinja("SELECT * FROM {{ref('orders')}}")

    def test_source_standard_spaces(self) -> None:
        assert has_dbt_jinja("SELECT * FROM {{ source('raw', 'events') }}")

    def test_source_no_spaces(self) -> None:
        assert has_dbt_jinja("SELECT * FROM {{source('raw','events')}}")

    # H6 — whitespace-trim hyphen variants
    def test_ref_whitespace_trim_left(self) -> None:
        """{{- ref( must be detected — whitespace-trim variant."""
        assert has_dbt_jinja("SELECT * FROM {{- ref('orders') -}}")

    def test_source_whitespace_trim_left(self) -> None:
        """{{- source( must be detected — whitespace-trim variant."""
        assert has_dbt_jinja("SELECT * FROM {{- source('raw', 'events') -}}")

    def test_plain_jinja_not_detected(self) -> None:
        assert not has_dbt_jinja("SELECT {{ my_var }} FROM foo")

    def test_empty_sql_not_detected(self) -> None:
        assert not has_dbt_jinja("")
