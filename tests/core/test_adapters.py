"""Tests for query adapters.

Tests the dbt_charts.core.execute.adapters module for query execution.

Uses the unified query interface with type-specific query classes.
"""

import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.query.normalized import (
    HttpQuery,
    SqlQuery,
    ValuesQuery,
)
from dbt_charts.core.compile.models.source import (
    DuckDBSourceConfig,
    PostgresSourceConfig,
)
from dbt_charts.core.dbt_manifest import LoadedManifest, RefIndex, ref_index
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.adapters import (
    BaseAdapter,
    DbtAdapter,
    DuckDBAdapter,
    HttpAdapter,
    QueryResult,
    SqlAdapter,
    ValuesAdapter,
    build_adapter_registry,
)
from dbt_charts.core.execute.adapters.dbt_utils import resolve_dbt_refs_with_provenance
from dbt_charts.core.execute.dbt_jinja import has_dbt_jinja
from dbt_charts.core.execute.duckdb_config import normalize_duckdb_config


def _ri(raw: dict) -> RefIndex:
    return ref_index(
        LoadedManifest(raw=raw, relpath="target/manifest.json", version="v1")
    )


def _sql(
    local_project: Callable[..., FilesystemProject], **kwargs: object
) -> SqlAdapter:
    """Create a SqlAdapter with required args defaulted to in-memory stubs."""
    kwargs.setdefault("project", local_project(Path("/tmp")))
    kwargs.setdefault("dbt_project_path", None)
    kwargs.setdefault("profile_type", "postgres")
    return SqlAdapter(**kwargs)  # type: ignore[arg-type]


class TestQueryResult:
    """Tests for QueryResult class."""

    def test_successful_result_and_columns_extraction(self):
        """Test successful query result with data content and column extraction."""
        data = [
            {"col1": 1, "col2": "a"},
            {"col1": 2, "col2": "b"},
        ]
        result = QueryResult(data=data)

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


class TestSqlAdapter:
    """Tests for SQL adapter."""

    def test_can_execute_sql(self, local_project: Callable[..., FilesystemProject]):
        """SqlAdapter claims SQL against a resolved warehouse source, but not the
        file engines (duckdb/sqlite) or a source-less query."""
        adapter = _sql(local_project)

        sql_query = SqlQuery(sql="SELECT * FROM users", source="test_profile")
        warehouse = PostgresSourceConfig(
            type="postgres", host="h", user="u", password="p", dbname="d"
        )
        assert adapter.can_execute(sql_query, warehouse)
        assert not adapter.can_execute(sql_query, DuckDBSourceConfig(type="duckdb"))
        assert not adapter.can_execute(sql_query, None)

    def test_cannot_execute_other_types(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test SQL adapter rejects non-SQL queries."""
        adapter = _sql(local_project)

        http_query = HttpQuery(url="http://example.com")
        assert not adapter.can_execute(http_query, None)

    def test_duckdb_source_query(self):
        """Test that DuckDB source config executes queries correctly."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(
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
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
        )
        source_config = DuckDBSourceConfig(type="duckdb", path=":memory:")

        query1 = SqlQuery(sql="SELECT 1 as value", source="test_profile")
        result1 = adapter.execute(query1, source_config=source_config)
        assert result1.is_success

        sources = adapter._tls.sources
        conns_after_first = len(sources)

        query2 = SqlQuery(sql="SELECT 2 as value", source="test_profile")
        result2 = adapter.execute(query2, source_config=source_config)
        assert result2.is_success
        assert result2.data[0]["value"] == 2

        # Same source config should not create additional connections
        assert len(adapter._tls.sources) == conns_after_first

    def test_cte_replaces_init_sql_pattern(self):
        """Test that CTEs serve as the replacement for init_sql views."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(
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

            adapter = SqlAdapter(
                project=local_project(project_path),
                dbt_project_path=str(project_path),
                profile_type="postgres",
            )

            resolved_sql, _ = adapter._dbt_refs.resolve(
                "SELECT * FROM {{ ref('orders') }}"
            )
            assert resolved_sql == "SELECT * FROM analytics.fct_orders"


class TestHttpAdapter:
    """Tests for HTTP adapter."""

    def test_can_execute_http(self):
        """Test HTTP adapter identifies HTTP queries."""
        adapter = HttpAdapter()

        http_query = HttpQuery(url="http://example.com")
        assert adapter.can_execute(http_query, None)

    def test_cannot_execute_other_types(self):
        """Test HTTP adapter rejects non-HTTP queries."""
        adapter = HttpAdapter()

        sql_query = SqlQuery(sql="SELECT 1", source="test_profile")
        assert not adapter.can_execute(sql_query, None)


class TestAdapterRegistry:
    """Tests for adapter registry."""

    def test_get_adapter_sql(self, local_project: Callable[..., FilesystemProject]):
        """Registry routes a resolved warehouse source to SqlAdapter, and a
        source-less SQL query to the DuckDB default engine."""
        registry = build_adapter_registry(local_project(Path("/tmp")))
        sql_query = SqlQuery(sql="SELECT 1", source="test_profile")

        warehouse = PostgresSourceConfig(
            type="postgres", host="h", user="u", password="p", dbname="d"
        )
        assert isinstance(registry.get_adapter(sql_query, warehouse), SqlAdapter)
        assert isinstance(registry.get_adapter(sql_query, None), DuckDBAdapter)

    def test_get_adapter_http(self, local_project: Callable[..., FilesystemProject]):
        """Test registry finds HTTP adapter."""
        registry = build_adapter_registry(local_project(Path("/tmp")))

        http_query = HttpQuery(url="http://example.com")
        adapter = registry.get_adapter(http_query, None)

        assert adapter is not None
        assert isinstance(adapter, HttpAdapter)

    def test_execute_returns_query_result(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test registry execute returns QueryResult."""
        registry = build_adapter_registry(local_project(Path("/tmp")))

        sql_query = SqlQuery(sql="SELECT 1", source="test_profile")
        result = registry.execute(sql_query)

        assert isinstance(result, QueryResult)

    def test_supported_types(self, local_project: Callable[..., FilesystemProject]):
        """Test registry reports all supported types."""
        registry = build_adapter_registry(local_project(Path("/tmp")))

        # Should have at least sql, values, http
        assert "sql" in registry.supported_types
        assert "values" in registry.supported_types
        assert "http" in registry.supported_types

    def test_register_custom_adapter(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test registering custom adapter."""
        registry = build_adapter_registry(local_project(Path("/tmp")))

        # Count adapters before
        count_before = len(registry._adapters)

        # Create mock adapter with proper unified interface implementation
        class CustomAdapter(BaseAdapter):
            @property
            def supported_types(self) -> set[str]:
                return {"custom"}

            def _execute(self, query, variables):
                return QueryResult(data=[])

        registry.register(CustomAdapter())

        assert len(registry._adapters) == count_before + 1
        assert "custom" in registry.supported_types


class TestDbtAdapter:
    """Tests for dbt adapter - especially manifest loading for ref() resolution."""

    def test_reads_do_not_open_a_transaction(
        self, local_project: Callable[..., FilesystemProject], tmp_path: Path
    ):
        """A transaction only ends when someone releases the connection. A read that
        never opens one cannot pin locks even if the connection outlives the query."""
        from unittest.mock import MagicMock

        adapter = DbtAdapter(
            project=local_project(tmp_path),
            dbt_project_path=tmp_path,
            target_name="dev",
        )
        dbt = MagicMock()
        dbt.execute.return_value = (None, MagicMock(column_names=["n"], rows=[(1,)]))
        adapter._adapter = dbt
        adapter._dialect = "postgres"

        result = adapter._execute(SqlQuery(sql="SELECT 1 AS n"))

        assert result.error is None
        assert dbt.execute.call_args.kwargs["auto_begin"] is False

    def test_ref_resolution_with_manifest_only(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test ref() resolution works with just the manifest (no dbt adapter).

        This simulates production where dbt-core may not be installed or
        fully configured, but we have a manifest for ref() resolution.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            # Create manifest with a model
            manifest = {
                "nodes": {
                    "model.project.customers": {
                        "resource_type": "model",
                        "name": "customers",
                        "schema": "public",
                        "alias": "customers",
                    },
                    "model.project.orders": {
                        "resource_type": "model",
                        "name": "orders",
                        "schema": "sales",
                        "alias": "fct_orders",
                    },
                },
                "sources": {},
            }
            (project_path / "target").mkdir()
            with Path(project_path / "target" / "manifest.json").open(
                "w", encoding="utf-8"
            ) as f:
                json.dump(manifest, f)

            adapter = DbtAdapter(
                project=local_project(project_path),
                dbt_project_path=project_path,
                target_name="dev",
            )

            # Test ref resolution
            sql = "SELECT * FROM {{ ref('customers') }}"
            resolved = adapter._resolve_dbt_sql(sql)
            assert resolved == "SELECT * FROM public.customers"

            # Test with alias
            sql2 = "SELECT * FROM {{ ref('orders') }}"
            resolved2 = adapter._resolve_dbt_sql(sql2)
            assert resolved2 == "SELECT * FROM sales.fct_orders"

    def test_error_when_ref_used_without_manifest(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test helpful error when ref() is used but no manifest exists.

        This catches the production bug where manifest isn't loaded and
        ref() fails with an unhelpful Jinja error.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            # No manifest files created

            adapter = DbtAdapter(
                project=local_project(project_path),
                dbt_project_path=project_path,
                target_name="dev",
            )
            sql = "SELECT * FROM {{ ref('my_model') }}"

            with pytest.raises(DbtChartsError, match=r"ref\(\)") as exc_info:
                adapter._resolve_dbt_sql(sql)

            # Also confirm the message references the manifest paths checked.
            assert "manifest" in str(exc_info.value).lower()

    def test_manifest_loads_before_dbt_init_fails(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Manifest is loaded even when dbt adapter construction raises.

        _get_dbt_adapter loads the manifest (doesn't require dbt) before
        attempting adapter construction. When the dbt package is missing the
        function now raises ImportError; the manifest is still populated so
        ref() resolution works after the caller catches the error.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            # Create manifest
            manifest = {
                "nodes": {
                    "model.test.users": {
                        "resource_type": "model",
                        "name": "users",
                        "schema": "main",
                        "alias": "users",
                    }
                },
                "sources": {},
            }
            (project_path / "target").mkdir()
            with Path(project_path / "target" / "manifest.json").open(
                "w", encoding="utf-8"
            ) as f:
                json.dump(manifest, f)

            adapter = DbtAdapter(
                project=local_project(project_path),
                dbt_project_path=project_path,
                target_name="dev",
            )

            # Mock dbt import to fail — _get_dbt_adapter now raises ImportError
            # but the manifest is loaded first (before the try block).
            with (
                patch.dict(
                    "sys.modules", {"dbt": None, "dbt.cli": None, "dbt.cli.main": None}
                ),
                pytest.raises((ImportError, FileNotFoundError)),
            ):
                adapter._get_dbt_adapter()

            # ref() should work
            sql = "SELECT * FROM {{ ref('users') }}"
            resolved = adapter._resolve_dbt_sql(sql)
            assert resolved == "SELECT * FROM main.users"

    def test_stray_snapshot_file_is_ignored_by_dbt_adapter(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """A leftover manifest.snapshot.json next to target/manifest.json is
        never consulted — target/ wins end-to-end through DbtAdapter, and a
        stray file at the old convention's path changes nothing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            target_manifest = {
                "nodes": {
                    "model.test.users": {
                        "resource_type": "model",
                        "name": "users",
                        "schema": "dev_schema",
                        "alias": "users",
                    }
                },
                "sources": {},
            }
            snapshot_manifest = {
                "nodes": {
                    "model.test.users": {
                        "resource_type": "model",
                        "name": "users",
                        "schema": "prod_schema",
                        "alias": "users",
                    }
                },
                "sources": {},
            }

            (project_path / "target").mkdir()
            with Path(project_path / "target" / "manifest.json").open(
                "w", encoding="utf-8"
            ) as f:
                json.dump(target_manifest, f)

            with Path(project_path / "manifest.snapshot.json").open(
                "w", encoding="utf-8"
            ) as f:
                json.dump(snapshot_manifest, f)

            adapter = DbtAdapter(
                project=local_project(project_path),
                dbt_project_path=project_path,
                target_name="dev",
            )

            sql = "SELECT * FROM {{ ref('users') }}"
            resolved = adapter._resolve_dbt_sql(sql)
            assert "dev_schema" in resolved
            assert "prod_schema" not in resolved


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

    def test_can_execute_values(self):
        """Test values adapter identifies values queries."""
        adapter = ValuesAdapter()
        query = ValuesQuery(rows=[{"a": 1}])
        assert adapter.can_execute(query, None)

    def test_cannot_execute_other_types(self):
        """Test values adapter rejects non-values queries."""
        adapter = ValuesAdapter()
        sql_query = SqlQuery(sql="SELECT 1", source="test_profile")
        assert not adapter.can_execute(sql_query, None)

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

    def test_registry_finds_values_adapter(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test adapter registry finds values adapter."""
        registry = build_adapter_registry(local_project(Path("/tmp")))
        query = ValuesQuery(rows=[{"a": 1}])
        adapter = registry.get_adapter(query, None)

        assert adapter is not None
        assert isinstance(adapter, ValuesAdapter)

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

    def test_registry_supported_types_includes_values(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test registry reports values in supported types."""
        registry = build_adapter_registry(local_project(Path("/tmp")))
        assert "values" in registry.supported_types


class TestBuildAdapterRegistry:
    """Tests for build_adapter_registry factory function."""

    def test_default_registers_all_types(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Default build registers all standard adapter types."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        registry = build_adapter_registry(local_project(Path("/tmp/test")))
        assert registry.supported_types >= {
            "sql",
            "values",
            "http",
        }

    def test_read_only_propagates_to_duckdb_adapter(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """read_only=True should propagate to DuckDBAdapter."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        registry = build_adapter_registry(
            local_project(Path("/tmp/test")), read_only=True
        )
        ddb_adapters = [a for a in registry.adapters if isinstance(a, DuckDBAdapter)]
        assert ddb_adapters, "Expected at least one DuckDBAdapter"
        assert all(a.read_only is True for a in ddb_adapters)

    def test_read_only_false_propagates(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """read_only=False should propagate to DuckDBAdapter."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        registry = build_adapter_registry(
            local_project(Path("/tmp/test")), read_only=False
        )
        ddb_adapters = [a for a in registry.adapters if isinstance(a, DuckDBAdapter)]
        assert ddb_adapters, "Expected at least one DuckDBAdapter"
        assert all(a.read_only is False for a in ddb_adapters)

    def test_allowed_types_restricts_adapters(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """allowed_types should restrict which adapters are registered."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        registry = build_adapter_registry(
            local_project(Path("/tmp/test")), allowed_types={"values"}
        )
        assert registry.supported_types == {"values"}

    def test_dbt_adapter_registered_via_sibling_discovery(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """DbtAdapter registered when dbt_project.yml sits next to dbt_charts.yml."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            # Sibling rule: dbt_project.yml must sit next to dbt_charts.yml.
            (project_dir / "dbt_charts.yml").write_text("# marker\n")
            (project_dir / "dbt_project.yml").write_text("name: test\n")

            registry = build_adapter_registry(local_project(project_dir))
            adapter_types = {type(a).__name__ for a in registry.adapters}
            assert "DbtAdapter" in adapter_types

    def test_dbt_adapter_not_registered_without_path(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """DbtAdapter not registered when no dbt_project.yml exists at the project root."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = build_adapter_registry(local_project(Path(tmpdir)))
            adapter_types = {type(a).__name__ for a in registry.adapters}
            assert "DbtAdapter" not in adapter_types

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


class TestDbtAdapterDirectInstantiation:
    """New _get_dbt_adapter() path: direct <Warehouse>Adapter(cfg, mp) instantiation.

    Validates the replacement of dbtRunner.invoke with direct adapter construction.
    Uses mock adapter classes for isolation from the live dbt-duckdb install.
    """

    def _make_profiles_yml(
        self, path: Path, profile_name: str = "test_profile"
    ) -> None:
        (path / "profiles.yml").write_text(
            f"""\
{profile_name}:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: ":memory:"
"""
        )

    def test_execute_via_direct_adapter_no_dbt_project_yml(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """DbtAdapter.execute() works with profiles.yml only — no dbt_project.yml needed.

        This is the key integration shape: a profiles.yml with a DuckDB :memory: target,
        no dbt_project.yml, no models/, no target/manifest.json. The adapter constructs
        the connection directly and returns real query results.
        """
        from contextlib import contextmanager
        from unittest.mock import MagicMock

        self._make_profiles_yml(tmp_path)

        # Build a fake agate-style table that mimics what dbt adapter.execute() returns
        fake_table = MagicMock()
        fake_table.column_names = ["x"]
        fake_table.rows = [(42,)]

        # connection_named must be a context manager
        @contextmanager
        def fake_connection_named(name):
            yield

        fake_adapter_instance = MagicMock()
        fake_adapter_instance.connection_named = fake_connection_named
        fake_adapter_instance.execute.return_value = (MagicMock(), fake_table)

        # Fake only adapter construction — the boundary this test is about is
        # DbtAdapter.execute()'s result shape. Profile resolution stays real, so
        # dbt reads the profiles.yml above and validates its duckdb target.
        from dbt_charts.core.execute.adapters import dbt_adapter_factory

        with patch.object(
            dbt_adapter_factory,
            "build_adapter",
            lambda *args, **kwargs: fake_adapter_instance,
        ):
            dbt_adapter = DbtAdapter(
                project=local_project(tmp_path),
                dbt_project_path=tmp_path,
                target_name="dev",
                profile_name="test_profile",
            )
            result = dbt_adapter.execute(
                SqlQuery(sql="SELECT 42 AS x", source="test_profile")
            )

        assert result.is_success, f"Execute failed: {result.error}"
        assert result.columns == ["x"]
        assert result.data == [{"x": 42}]

    def test_real_duckdb_adapter_end_to_end(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """_get_dbt_adapter() constructs a real DuckDBAdapter against :memory: and executes SQL.

        This test exercises the full production code path — real credentials class,
        real adapter constructor, real query — so that dbt API drift is caught before
        it reaches production.
        """
        try:
            import dbt.adapters.duckdb  # noqa: F401
        except Exception:  # noqa: BLE001
            pytest.skip("dbt-duckdb not importable in this environment")

        self._make_profiles_yml(tmp_path)

        dbt_adapter = DbtAdapter(
            project=local_project(tmp_path),
            dbt_project_path=tmp_path,
            target_name="dev",
            profile_name="test_profile",
        )
        adapter = dbt_adapter._get_dbt_adapter()
        assert adapter is not None, (
            "_get_dbt_adapter() returned None — DuckDBAdapter construction failed. "
            "Check that dbt-duckdb is installed and profiles.yml is valid."
        )

        with adapter.connection_named("dbt_charts_test"):
            _, table = adapter.execute("SELECT 42 AS x", fetch=True)

        columns = list(table.column_names)
        rows = [tuple(row) for row in table.rows]
        assert columns == ["x"], f"Unexpected columns: {columns}"
        assert rows == [(42,)], f"Unexpected rows: {rows}"

    def test_get_dbt_adapter_raises_on_no_profiles_yml(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """_get_dbt_adapter raises FileNotFoundError when profiles.yml is absent.

        No silent degrade — the caller should see a clear error so the user
        knows to configure profiles.yml rather than getting silent empty results.
        """
        # _read_profiles_yml falls back to ~/.dbt/profiles.yml when the project-local
        # one is missing. Redirect HOME so the developer's real config can't satisfy it.
        monkeypatch.setenv("HOME", str(tmp_path))
        adapter = DbtAdapter(
            project=local_project(tmp_path),
            dbt_project_path=tmp_path,
            target_name="dev",
            profile_name="myprofile",
        )
        with pytest.raises(FileNotFoundError, match="No profiles.yml found"):
            adapter._get_dbt_adapter()

    def test_get_dbt_adapter_raises_on_missing_profile_in_profiles_yml(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """_get_dbt_adapter raises ValueError when profiles.yml exists but lacks the profile.

        A typo in profile_name must be a hard error with an actionable message —
        not a silent degrade — so the user sees the problem and the available profiles.
        _read_target_dict raises ValueError before any dbt import, so no sys.modules
        mock is needed — the real production path is exercised directly.
        """
        self._make_profiles_yml(tmp_path, profile_name="correct_profile")

        adapter = DbtAdapter(
            project=local_project(tmp_path),
            dbt_project_path=tmp_path,
            target_name="dev",
            profile_name="typo_profile",
        )
        with pytest.raises(ValueError, match="typo_profile"):
            adapter._get_dbt_adapter()

    def _capture_build_adapter_target(
        self,
        tmp_path: Path,
        profiles_yml: str,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> dict[str, object]:
        """Run _get_dbt_adapter() against profiles_yml, returning the target
        dict it hands to build_adapter (mocked, so no real connection needed)."""
        from unittest.mock import MagicMock

        from dbt_charts.core.execute.adapters import dbt_adapter_factory

        # A machine exporting DBT_PROFILES_DIR outranks the project-local
        # profiles.yml this helper writes (_read_profiles_yml's resolution order).
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        (tmp_path / "profiles.yml").write_text(profiles_yml)
        captured: dict[str, object] = {}

        def fake_build_adapter(target_dict, **kwargs):
            captured.update(target_dict)
            return MagicMock()

        with patch.object(dbt_adapter_factory, "build_adapter", fake_build_adapter):
            dbt_adapter = DbtAdapter(
                project=local_project(tmp_path),
                dbt_project_path=tmp_path,
                target_name="dev",
                profile_name="test_profile",
            )
            dbt_adapter._get_dbt_adapter()
        return captured

    def test_env_var_in_target_is_rendered_before_reaching_build_adapter(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """_get_dbt_adapter() must render env_var() before build_adapter, or a
        field a driver opens as a file (Snowflake's private_key_path) reaches
        open() as the literal, unrendered Jinja string."""
        monkeypatch.setenv("DCT_TEST_SECRET_PATH", "/tmp/fake_key.p8")
        captured = self._capture_build_adapter_target(
            tmp_path,
            """\
test_profile:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: ":memory:"
      custom_secret_path: "{{ env_var('DCT_TEST_SECRET_PATH') }}"
""",
            local_project,
            monkeypatch,
        )
        assert captured["custom_secret_path"] == "/tmp/fake_key.p8"

    def test_env_var_value_containing_jinja_is_not_re_evaluated(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """Rendering happens once. A second pass would treat the first pass's
        output as a template — mirrors test_dbt_profile_routing.py's resolver-path
        coverage of the same one-render contract, for the _get_dbt_adapter path."""
        monkeypatch.setenv("DCT_TEST_SECRET", "{{ 1 + 1 }}")
        captured = self._capture_build_adapter_target(
            tmp_path,
            """\
test_profile:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: ":memory:"
      custom_secret: "{{ env_var('DCT_TEST_SECRET') }}"
""",
            local_project,
            monkeypatch,
        )
        assert captured["custom_secret"] == "{{ 1 + 1 }}"

    def test_absolute_templated_duckdb_path_is_not_anchored(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """The relative-DuckDB-path anchor in _read_target_dict must test the
        rendered path, not the literal Jinja — an unrendered `{{ env_var(...) }}`
        is never absolute, so it would join onto dbt_project_path as text and
        the adapter would open the wrong (or a nonexistent) file."""
        monkeypatch.setenv("DCT_TEST_DB_PATH", "/data/warehouse.duckdb")
        captured = self._capture_build_adapter_target(
            tmp_path,
            """\
test_profile:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: "{{ env_var('DCT_TEST_DB_PATH') }}"
""",
            local_project,
            monkeypatch,
        )
        assert captured["path"] == "/data/warehouse.duckdb"


class TestResolveDuckdbConfig:
    """DuckDBAdapter._resolve_duckdb_config falls back to adapter-level default."""

    def test_no_source_config_returns_adapter_default(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
            duckdb_config={"enable_external_access": True},
        )
        result = adapter._resolve_duckdb_config({})
        assert result == {"enable_external_access": True}

    def test_source_config_overrides_adapter_default(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
            duckdb_config={"enable_external_access": True},
        )
        result = adapter._resolve_duckdb_config(
            {"duckdb_config": {"enable_external_access": False}}
        )
        assert result == {"enable_external_access": False}

    def test_no_source_no_adapter_returns_none(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
        )
        result = adapter._resolve_duckdb_config({})
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

    def test_sql_adapter_claims_resolved_warehouse_regardless_of_jinja(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """SqlAdapter claims a resolved warehouse source; dbt-jinja in the SQL is
        irrelevant now that the resolved source type decides ownership. A
        source-less query is DuckDB's/Dbt's territory, not SqlAdapter's."""
        adapter = _sql(local_project)
        warehouse = PostgresSourceConfig(
            type="postgres", host="h", user="u", password="p", dbname="d"
        )
        ref_query = SqlQuery(
            sql="SELECT * FROM {{ ref('orders') }}", source="test_profile"
        )
        assert adapter._can_execute(ref_query, warehouse)
        assert not adapter._can_execute(ref_query, None)

    def test_dbt_adapter_claims_sourceless_dbt_jinja_only(
        self,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """DbtAdapter claims source-less SQL that uses dbt jinja; a plain
        source-less query defers to DuckDB, and a resolved source routes on its
        own type (see test_dbt_profile_routing.py for the dbt_profile path)."""
        adapter = DbtAdapter(
            project=local_project(Path("/tmp")),
            dbt_project_path=Path("/tmp"),
            target_name="dev",
        )
        jinja = SqlQuery(sql="SELECT * FROM {{- ref('orders') -}}", source=None)
        plain = SqlQuery(sql="SELECT 1", source=None)
        assert adapter._can_execute(jinja, None)
        assert not adapter._can_execute(plain, None)
        assert not adapter._can_execute(jinja, DuckDBSourceConfig(type="duckdb"))
