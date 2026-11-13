"""Tests for dbt relation lineage tracking.

Covers:
- resolve_dbt_refs_with_provenance returns relation metadata
- QueryResult carries resolved_relations metadata
- Executor caches and exposes provenance per query

The former dev-vs-prod schema-status classification (ResolvedRelation.status,
classify_schema_status, the chart renderer's data-schema-status attribute)
was removed with the manifest.snapshot.json convention: it was the only
source that ever populated a "prod" manifest to compare against, so the
"changed"/"unchanged" states were unreachable from any production entry
point.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

from dbt_charts.core.column_types import literal_schema
from dbt_charts.core.dbt_manifest import LoadedManifest, RefIndex, ref_index
from dbt_charts.core.execute.dbt_utils import resolve_dbt_refs_with_provenance
from dbt_charts.core.execute.query_result import QueryResult, ResolvedRelation

# ---------------------------------------------------------------------------
# Provenance data model
# ---------------------------------------------------------------------------


class TestResolvedRelation:
    """ResolvedRelation carries per-ref lineage (ref_name, schema)."""

    def test_query_result_carries_relations(self) -> None:
        rel = ResolvedRelation(ref_name="orders", schema="analytics")
        result = QueryResult(
            data=[{"id": 1}],
            resolved_relations=[rel],
            schema=literal_schema([{"id": 1}]),
        )
        assert len(result.resolved_relations) == 1
        assert result.resolved_relations[0].schema == "analytics"


# ---------------------------------------------------------------------------
# resolve_dbt_refs_with_provenance
# ---------------------------------------------------------------------------


def _make_manifest(
    models: dict[str, dict[str, Any]] | None = None,
    sources: dict[str, dict[str, Any]] | None = None,
) -> RefIndex:
    """Build a minimal dbt manifest RefIndex for testing."""
    raw: dict[str, Any] = {}
    if models is not None:
        raw["nodes"] = {
            f"model.test.{name}": {
                "resource_type": "model",
                "name": name,
                "schema": info.get("schema", "main"),
                "alias": info.get("alias", name),
            }
            for name, info in models.items()
        }
    if sources is not None:
        raw["sources"] = {
            f"source.test.{name}": {
                "source_name": info["source_name"],
                "name": name,
                "schema": info.get("schema", "main"),
            }
            for name, info in sources.items()
        }
    return ref_index(
        LoadedManifest(raw=raw, relpath="target/manifest.json", version="v1")
    )


class TestResolveDbtRefsWithProvenance:
    """resolve_dbt_refs_with_provenance returns SQL + relation lineage."""

    def test_ref_resolved_with_lineage(self) -> None:
        sql = "SELECT * FROM {{ ref('orders') }}"
        manifest = _make_manifest(models={"orders": {"schema": "dev_dave"}})

        resolved_sql, relations = resolve_dbt_refs_with_provenance(sql, manifest)
        assert resolved_sql == "SELECT * FROM dev_dave.orders"
        assert len(relations) == 1
        assert relations[0].ref_name == "orders"
        assert relations[0].schema == "dev_dave"

    def test_source_resolved_with_lineage(self) -> None:
        sql = "SELECT * FROM {{ source('raw', 'payments') }}"
        manifest = _make_manifest(
            sources={
                "payments": {
                    "source_name": "raw",
                    "schema": "raw_dev",
                }
            }
        )

        resolved_sql, relations = resolve_dbt_refs_with_provenance(sql, manifest)
        assert resolved_sql == "SELECT * FROM raw_dev.payments"
        assert len(relations) == 1
        assert relations[0].ref_name == "raw.payments"
        assert relations[0].schema == "raw_dev"

    def test_multiple_refs(self) -> None:
        sql = "SELECT * FROM {{ ref('orders') }} JOIN {{ ref('customers') }}"
        manifest = _make_manifest(
            models={
                "orders": {"schema": "dev_dave"},
                "customers": {"schema": "analytics"},
            }
        )

        _, relations = resolve_dbt_refs_with_provenance(sql, manifest)
        assert len(relations) == 2
        schemas = {r.ref_name: r.schema for r in relations}
        assert schemas["orders"] == "dev_dave"
        assert schemas["customers"] == "analytics"

    def test_no_refs_returns_empty(self) -> None:
        sql = "SELECT 1"
        manifest = _make_manifest(models={})

        resolved, relations = resolve_dbt_refs_with_provenance(sql, manifest)
        assert resolved == "SELECT 1"
        assert relations == []


# ---------------------------------------------------------------------------
# Executor provenance plumbing
# ---------------------------------------------------------------------------


class TestExecutorProvenance:
    """Executor caches and exposes per-query provenance."""

    def test_provenance_after_execute(self, routes: Mock) -> None:
        """After executing a query, provenance is accessible."""
        from dbt_charts.core.compile import compile

        from .._run import Run
        from .._scripted_env import ScriptedBackend, scripted_env

        board = compile(
            """
queries:
  orders:
    sql: SELECT * FROM orders
    source: test_db
charts:
  c:
    query: orders
    type: table
rows:
  - c
"""
        ).board

        rel = ResolvedRelation(ref_name="orders", schema="dev_dave")
        mock_result = QueryResult(
            data=[{"id": 1}],
            resolved_relations=[rel],
            schema=literal_schema([{"id": 1}]),
        )

        routes.return_value = mock_result

        executor = Run(board, env=scripted_env(ScriptedBackend()))
        provenance = executor.results(["orders"], {}).provenance["orders"]
        assert len(provenance) == 1
        assert provenance[0].schema == "dev_dave"
