"""Tests for DbtSchemaSource — the cold-start plugin-boundary source.

DbtSchemaSource is the honest path. It surfaces only what's directly
available from the dbt-core adapter (live column types) and the
target/manifest.json (declared types, descriptions, listed tests, declared
relationships). NO inference, NO naming heuristics, NO synthesis.

Critical contract per Dave:
  - Surface manifest `relationships:` tests verbatim. That's the one
    explicit FK declaration channel.
  - Don't naming-heuristic FKs from `<x>_id → <x>`. Empty is honest.
  - List manifest tests verbatim with name + kwargs.
  - Manifest absent → no manifest contribution. Adapter still works.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.inspect.sources.dbt import (
    DbtSchemaSource,
    extract_all_relationships,
)

from ..._paths import DBT_CHARTS_DIR

# ---------------------------------------------------------------------------
# Fake dbt-core adapter — duck-types BaseAdapter.list_schemas /
# list_relations / get_columns_in_relation.
# ---------------------------------------------------------------------------


@dataclass
class _FakeColumn:
    name: str
    dtype: str


@dataclass
class _FakeRelation:
    identifier: str
    schema: str
    type: str = "table"
    database: str | None = None


@dataclass
class FakeAdapter:
    """Minimal duck of dbt-core BaseAdapter for unit tests."""

    schemas: dict[str, list[_FakeRelation]] = field(default_factory=dict)
    columns_by_relation: dict[tuple[str, str], list[_FakeColumn]] = field(
        default_factory=dict
    )

    def add_table(
        self,
        schema: str,
        table: str,
        columns: list[tuple[str, str]],
        kind: str = "table",
    ) -> None:
        self.schemas.setdefault(schema, []).append(
            _FakeRelation(identifier=table, schema=schema, type=kind)
        )
        self.columns_by_relation[(schema, table)] = [
            _FakeColumn(name=n, dtype=t) for n, t in columns
        ]

    # dbt-core BaseAdapter methods we forward to:

    def list_schemas(self, database: str | None) -> list[str]:
        return list(self.schemas.keys())

    def list_relations(self, database: str | None, schema: str) -> list[_FakeRelation]:
        self.list_relations_calls = getattr(self, "list_relations_calls", 0) + 1
        return list(self.schemas.get(schema, []))

    def get_columns_in_relation(self, relation: _FakeRelation) -> list[_FakeColumn]:
        return list(
            self.columns_by_relation.get((relation.schema, relation.identifier), [])
        )

    @classmethod
    def type(cls) -> str:  # noqa: A003 — mirrors dbt adapter classmethod name
        return "duckdb"

    def calculate_freshness_from_metadata(
        self, source: Any, _macro_resolver: Any = None
    ) -> Any:
        raise NotImplementedError

    connections = SimpleNamespace(
        get_thread_connection=lambda: SimpleNamespace(handle=object())
    )

    @contextmanager
    def connection_named(self, name: str) -> Any:
        yield self


@pytest.fixture
def adapter_with_orders() -> FakeAdapter:
    a = FakeAdapter()
    a.add_table(
        "public",
        "orders",
        [("id", "BIGINT"), ("user_id", "BIGINT"), ("status", "VARCHAR")],
    )
    return a


# ---------------------------------------------------------------------------
# Manifest writers — produce manifest.json shapes that mirror dbt-core.
# ---------------------------------------------------------------------------


def _write_manifest(project_root: Path, nodes: dict[str, dict[str, Any]]) -> None:
    target = project_root / "target"
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(json.dumps({"nodes": nodes, "sources": {}}))


def _model_node(
    name: str,
    schema: str,
    columns: dict[str, dict[str, Any]] | None = None,
    tags: list[str] | None = None,
    owner: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "resource_type": "model",
        "name": name,
        "schema": schema,
        "alias": name,
        "columns": columns or {},
    }
    if tags is not None:
        node["tags"] = tags
    if description is not None:
        node["description"] = description
    if owner is not None:
        node["meta"] = {"owner": owner}
    return node


def _generic_test_node(
    name: str,
    column_name: str,
    file_key_name: str,
    kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a generic-test node like dbt-core writes for column-level tests."""
    test_kwargs = {"column_name": column_name, "model": "{{ ref('x') }}"}
    if kwargs:
        test_kwargs.update(kwargs)
    return {
        "resource_type": "test",
        "test_metadata": {"name": name, "kwargs": test_kwargs},
        "column_name": column_name,
        "attached_node": None,
        "file_key_name": file_key_name,
        "depends_on": {"macros": [], "nodes": []},
    }


# ---------------------------------------------------------------------------
# Source-level metadata
# ---------------------------------------------------------------------------


class TestSourceIdentity:
    def test_name_is_dbt(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        src = DbtSchemaSource(adapter=FakeAdapter(), project=local_project(tmp_path))
        assert src.name == "dbt"

    def test_generated_at_is_none(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """DbtSchemaSource is a live source — no cache build time. The
        resolver stamps _meta.retrieved_at instead."""
        src = DbtSchemaSource(adapter=FakeAdapter(), project=local_project(tmp_path))
        assert src.generated_at is None


# ---------------------------------------------------------------------------
# profile_table — adapter only, no manifest
# ---------------------------------------------------------------------------


class TestProfileTableAdapterOnly:
    """Manifest absent. Adapter still answers: column names + actual types.
    Honest cold-start — no description, no declared_type, no tests, no
    relationships."""

    def test_returns_columns_with_actual_type_from_adapter(
        self,
        adapter_with_orders: FakeAdapter,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        src = DbtSchemaSource(
            adapter=adapter_with_orders, project=local_project(tmp_path)
        )
        profile = src.profile_table(schema="public", table="orders")
        assert profile is not None
        assert profile["kind"] == "table"
        assert profile["table_exists"] is True
        # Columns are a named-dict; per-column has actual_type only:
        cols = profile["columns"]
        assert cols["id"]["actual_type"] == "BIGINT"
        assert cols["user_id"]["actual_type"] == "BIGINT"

    def test_omits_manifest_only_fields_when_no_manifest(
        self,
        adapter_with_orders: FakeAdapter,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        src = DbtSchemaSource(
            adapter=adapter_with_orders, project=local_project(tmp_path)
        )
        profile = src.profile_table(schema="public", table="orders")
        col = profile["columns"]["id"]
        # Honest: these only come from manifest, which isn't present.
        assert "description" not in col
        assert "declared_type" not in col
        assert "tests" not in col
        assert "relationships" not in col
        # Same at table level:
        assert "description" not in profile
        assert "tags" not in profile
        assert "owner" not in profile

    def test_unknown_table_returns_none(
        self,
        adapter_with_orders: FakeAdapter,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        src = DbtSchemaSource(
            adapter=adapter_with_orders, project=local_project(tmp_path)
        )
        assert src.profile_table(schema="public", table="missing") is None


# ---------------------------------------------------------------------------
# profile_table — adapter + manifest
# ---------------------------------------------------------------------------


class TestProfileTableWithManifest:
    """Manifest contributes descriptions, declared types, tags, owner, tests,
    and explicit relationships. Adapter contributes actual_type + table_exists."""

    def test_surfaces_descriptions_and_declared_types_verbatim(
        self,
        adapter_with_orders: FakeAdapter,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        _write_manifest(
            tmp_path,
            {
                "model.test.orders": _model_node(
                    "orders",
                    "public",
                    columns={
                        "id": {
                            "name": "id",
                            "description": "Primary key for the orders table",
                            "data_type": "INTEGER",  # drift vs adapter's BIGINT
                        }
                    },
                    description="One row per customer order",
                )
            },
        )
        src = DbtSchemaSource(
            adapter=adapter_with_orders, project=local_project(tmp_path)
        )
        profile = src.profile_table(schema="public", table="orders")
        # Table-level:
        assert profile["description"] == "One row per customer order"
        # Column-level: declared_type from manifest, actual_type from adapter —
        # drift is visible because both fields surface.
        id_col = profile["columns"]["id"]
        assert id_col["actual_type"] == "BIGINT"
        assert id_col["declared_type"] == "INTEGER"
        assert id_col["description"] == "Primary key for the orders table"

    def test_surfaces_tags_and_owner_from_manifest(
        self,
        adapter_with_orders: FakeAdapter,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        _write_manifest(
            tmp_path,
            {
                "model.test.orders": _model_node(
                    "orders",
                    "public",
                    tags=["mart", "core"],
                    owner="data-team",
                )
            },
        )
        src = DbtSchemaSource(
            adapter=adapter_with_orders, project=local_project(tmp_path)
        )
        profile = src.profile_table(schema="public", table="orders")
        assert profile["tags"] == ["mart", "core"]
        assert profile["owner"] == "data-team"

    def test_surfaces_explicit_relationships_test_verbatim(
        self,
        adapter_with_orders: FakeAdapter,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """A `relationships:` test is the *one* explicit FK channel.
        Surface as `{to_table, to_column}`. No naming-heuristic invention."""
        _write_manifest(
            tmp_path,
            {
                "model.test.orders": _model_node("orders", "public"),
                "model.test.users": _model_node("users", "public"),
                "test.test.relationships_orders_user_id": _generic_test_node(
                    name="relationships",
                    column_name="user_id",
                    file_key_name="models.orders",
                    kwargs={"to": "ref('users')", "field": "id"},
                ),
            },
        )
        src = DbtSchemaSource(
            adapter=adapter_with_orders, project=local_project(tmp_path)
        )
        profile = src.profile_table(schema="public", table="orders")
        rels = profile["columns"]["user_id"]["relationships"]
        assert rels == [{"to_table": "users", "to_column": "id"}]

    def test_does_not_invent_fk_from_column_naming(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Column named `customer_id`, no `relationships:` test → field absent.
        No magic. No naming-heuristic FK synthesis."""
        adapter = FakeAdapter()
        adapter.add_table("public", "orders", [("customer_id", "BIGINT")])
        _write_manifest(
            tmp_path,
            {
                "model.test.orders": _model_node(
                    "orders",
                    "public",
                    columns={"customer_id": {"name": "customer_id"}},
                )
            },
        )
        src = DbtSchemaSource(adapter=adapter, project=local_project(tmp_path))
        profile = src.profile_table(schema="public", table="orders")
        col = profile["columns"]["customer_id"]
        assert "relationships" not in col

    def test_lists_tests_verbatim_with_clean_kwargs(
        self,
        adapter_with_orders: FakeAdapter,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """Surface tests as listed: name + kwargs. dbt-internal kwargs
        (`column_name`, `model`) are stripped — they're test-engine plumbing,
        not user-facing test config."""
        _write_manifest(
            tmp_path,
            {
                "model.test.orders": _model_node("orders", "public"),
                "test.test.unique_orders_id": _generic_test_node(
                    name="unique",
                    column_name="id",
                    file_key_name="models.orders",
                ),
                "test.test.not_null_orders_id": _generic_test_node(
                    name="not_null",
                    column_name="id",
                    file_key_name="models.orders",
                ),
            },
        )
        src = DbtSchemaSource(
            adapter=adapter_with_orders, project=local_project(tmp_path)
        )
        profile = src.profile_table(schema="public", table="orders")
        tests = profile["columns"]["id"]["tests"]
        names = sorted(t["name"] for t in tests)
        assert names == ["not_null", "unique"]
        for t in tests:
            assert t["kwargs"] == {}

    def test_accepted_values_test_surfaces_values_kwarg(
        self,
        adapter_with_orders: FakeAdapter,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """User-defined kwargs (e.g. `values` on accepted_values) are kept;
        only the dbt-internal `column_name`/`model` are stripped."""
        _write_manifest(
            tmp_path,
            {
                "model.test.orders": _model_node("orders", "public"),
                "test.test.accepted_values_orders_status": _generic_test_node(
                    name="accepted_values",
                    column_name="status",
                    file_key_name="models.orders",
                    kwargs={"values": ["pending", "shipped", "delivered"]},
                ),
            },
        )
        src = DbtSchemaSource(
            adapter=adapter_with_orders, project=local_project(tmp_path)
        )
        profile = src.profile_table(schema="public", table="orders")
        tests = profile["columns"]["status"]["tests"]
        assert tests == [
            {
                "name": "accepted_values",
                "kwargs": {"values": ["pending", "shipped", "delivered"]},
            }
        ]

    def test_relationships_test_not_double_listed_as_test(
        self,
        adapter_with_orders: FakeAdapter,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """When we promote a `relationships:` test into the structured
        `relationships:` field, we don't *also* echo it under `tests:`. One
        canonical surfacing — the structured form wins."""
        _write_manifest(
            tmp_path,
            {
                "model.test.orders": _model_node("orders", "public"),
                "test.test.relationships_orders_user_id": _generic_test_node(
                    name="relationships",
                    column_name="user_id",
                    file_key_name="models.orders",
                    kwargs={"to": "ref('users')", "field": "id"},
                ),
                "test.test.not_null_orders_user_id": _generic_test_node(
                    name="not_null",
                    column_name="user_id",
                    file_key_name="models.orders",
                ),
            },
        )
        src = DbtSchemaSource(
            adapter=adapter_with_orders, project=local_project(tmp_path)
        )
        profile = src.profile_table(schema="public", table="orders")
        col = profile["columns"]["user_id"]
        # `tests:` lists the not_null only; relationships is in its own field.
        assert col["tests"] == [{"name": "not_null", "kwargs": {}}]
        assert col["relationships"] == [{"to_table": "users", "to_column": "id"}]


# ---------------------------------------------------------------------------
# list_schemas / list_tables
# ---------------------------------------------------------------------------


class TestListSchemas:
    def test_forwards_to_adapter_list_schemas(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        adapter = FakeAdapter()
        adapter.add_table("public", "orders", [("id", "BIGINT")])
        adapter.add_table("raw", "events", [("id", "BIGINT")])
        src = DbtSchemaSource(adapter=adapter, project=local_project(tmp_path))
        out = src.list_schemas()
        assert out is not None
        assert "public" in out["schemas"]
        assert "raw" in out["schemas"]
        # Cold-start adapter exposes names only (no table_count — that
        # would require list_relations on every schema, expensive).
        assert out["schemas"]["public"] == {}


class TestListTables:
    def test_kind_from_adapter_relation_type(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        adapter = FakeAdapter()
        adapter.add_table("public", "orders", [("id", "BIGINT")], kind="table")
        adapter.add_table("public", "top_customers", [("id", "BIGINT")], kind="view")
        src = DbtSchemaSource(adapter=adapter, project=local_project(tmp_path))
        out = src.list_tables(schema="public")
        assert out is not None
        assert out["tables"]["orders"]["kind"] == "table"
        assert out["tables"]["top_customers"]["kind"] == "view"

    def test_manifest_metadata_layered_on_table_summaries(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        adapter = FakeAdapter()
        adapter.add_table("public", "orders", [("id", "BIGINT")])
        _write_manifest(
            tmp_path,
            {
                "model.test.orders": _model_node(
                    "orders",
                    "public",
                    description="One row per customer order",
                    tags=["mart", "core"],
                    owner="data-team",
                )
            },
        )
        src = DbtSchemaSource(adapter=adapter, project=local_project(tmp_path))
        out = src.list_tables(schema="public")
        summary = out["tables"]["orders"]
        assert summary["kind"] == "table"
        assert summary["description"] == "One row per customer order"
        assert summary["tags"] == ["mart", "core"]
        assert summary["owner"] == "data-team"

    def test_empty_schema_returns_none(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        adapter = FakeAdapter()
        # No tables in the queried schema.
        adapter.add_table("other", "x", [("id", "BIGINT")])
        src = DbtSchemaSource(adapter=adapter, project=local_project(tmp_path))
        assert src.list_tables(schema="public") is None


# ---------------------------------------------------------------------------
# Per-schema relation cache (no N+1 on level-4 wildcards)
# ---------------------------------------------------------------------------


class TestRelationsPerSchemaCache:
    def test_profile_table_after_list_tables_does_not_relist(
        self,
        adapter_with_orders: FakeAdapter,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """Regression: level-4 wildcard expansion calls list_tables once
        then profile_table per match. Each profile_table call MUST NOT
        re-issue list_relations against the warehouse — the cache built
        by list_tables serves the whole walk."""
        src = DbtSchemaSource(
            adapter=adapter_with_orders, project=local_project(tmp_path)
        )
        src.list_tables(schema="public")
        baseline = adapter_with_orders.list_relations_calls
        # Three profile_table calls in the same instance (mimics wildcard expansion).
        src.profile_table(schema="public", table="orders")
        src.profile_table(schema="public", table="orders")
        src.profile_table(schema="public", table="orders")
        assert adapter_with_orders.list_relations_calls == baseline

    def test_first_profile_table_lists_once_then_caches(
        self,
        adapter_with_orders: FakeAdapter,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """Even without a prior list_tables call, the first profile_table
        warms the cache — subsequent profile_table calls in the same
        schema don't re-list."""
        src = DbtSchemaSource(
            adapter=adapter_with_orders, project=local_project(tmp_path)
        )
        src.profile_table(schema="public", table="orders")
        after_first = adapter_with_orders.list_relations_calls
        src.profile_table(schema="public", table="orders")
        assert adapter_with_orders.list_relations_calls == after_first
        assert after_first == 1


class TestDbtSchemaSourceDatabase:
    """Regression: list_schemas and _get_relations must pass the database from
    adapter credentials, not a hardcoded None. Snowflake's list_schemas macro
    runs SHOW TERSE SCHEMAS IN DATABASE {database} and fails on None."""

    def _make_source(
        self,
        database: str | None,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> DbtSchemaSource:
        from unittest.mock import MagicMock

        mock_adapter = MagicMock()
        mock_adapter.type.return_value = "snowflake"
        mock_adapter.config.credentials.database = database
        mock_adapter.list_schemas.return_value = ["PUBLIC", "RAW"]
        mock_adapter.list_relations.return_value = []
        mock_adapter.connection_named.return_value.__enter__ = lambda _mock_conn: None
        mock_adapter.connection_named.return_value.__exit__ = MagicMock(
            return_value=False
        )
        return DbtSchemaSource(mock_adapter, local_project(tmp_path))

    def test_list_schemas_passes_credentials_database(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        source = self._make_source("MY_DATABASE", tmp_path, local_project)
        source.list_schemas()
        source._adapter.list_schemas.assert_called_once_with("MY_DATABASE")

    def test_list_schemas_none_when_no_database_in_credentials(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        source = self._make_source(None, tmp_path, local_project)
        source.list_schemas()
        source._adapter.list_schemas.assert_called_once_with(None)

    def test_get_relations_passes_credentials_database_for_non_duckdb(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        source = self._make_source("MY_DATABASE", tmp_path, local_project)
        source._get_relations("PUBLIC")
        source._adapter.list_relations.assert_called_once_with("MY_DATABASE", "PUBLIC")


# ---------------------------------------------------------------------------
# dbt v2-emitted manifest.json — real fixture, not a hand-built dbt-core shape.
#
# `dbt-charts/tests/fixtures/fusion_manifest/manifest.json` was produced by
# `dbt parse` with `dbt-fusion 2.0.0-preview.193` against the `jaffle-shop`
# jaffle project (Snowflake target), with the `macros` key stripped (unused
# by any dbt charts reader; it was 828KB of the 934KB raw file). Every other
# key is byte-for-byte what dbt v2 wrote.
# ---------------------------------------------------------------------------

_FUSION_FIXTURES = DBT_CHARTS_DIR / "tests" / "fixtures" / "fusion_manifest"
_FUSION_MANIFEST_JSON = (_FUSION_FIXTURES / "manifest.json").read_text()


def _fusion_project(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> FilesystemProject:
    target = tmp_path / "target"
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(_FUSION_MANIFEST_JSON)
    return local_project(tmp_path)


class TestDbtSchemaSourceFusionManifest:
    """Every field DbtSchemaSource reads, exercised against a real dbt v2
    manifest.json rather than a hand-built dbt-core-shaped dict."""

    def test_profile_table_surfaces_relation_and_column_descriptions(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        adapter = FakeAdapter()
        adapter.add_table(
            "webbeard",
            "customers",
            [("customer_id", "NUMBER"), ("customer_name", "VARCHAR")],
        )
        src = DbtSchemaSource(
            adapter=adapter, project=_fusion_project(tmp_path, local_project)
        )
        profile = src.profile_table(schema="webbeard", table="customers")
        assert profile is not None
        assert (
            profile["description"]
            == "Customer overview data mart, offering key details for each "
            "unique customer. One row per customer."
        )
        col = profile["columns"]["customer_id"]
        assert col["description"] == "The unique key of the orders mart."
        # dbt v2 writes `"meta": {}` verbatim, same as dbt v1, for a model
        # with no declared owner — honest absence, not a missing key.
        assert "owner" not in profile

    def test_lineage_walks_real_parent_child_maps_filtering_non_table_uids(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """`orders`' child_map mixes model/test/unit_test uid prefixes —
        only the `customers` model should survive the table-uid filter."""
        adapter = FakeAdapter()
        adapter.add_table("webbeard", "orders", [("order_id", "NUMBER")])
        src = DbtSchemaSource(
            adapter=adapter, project=_fusion_project(tmp_path, local_project)
        )
        profile = src.profile_table(schema="webbeard", table="orders")
        assert profile is not None
        upstream_names = {ref["model_name"] for ref in profile["upstream"]}
        downstream_names = {ref["model_name"] for ref in profile["downstream"]}
        assert upstream_names == {"order_items", "stg_orders"}
        assert downstream_names == {"customers"}

    def test_extract_all_relationships_reads_real_relationships_tests(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        manifest = json.loads(_FUSION_MANIFEST_JSON)
        edges = extract_all_relationships(manifest)
        assert {
            (e["from_table"], e["from_column"], e["to_table"], e["to_column"])
            for e in edges
        } == {
            ("order_items", "order_id", "orders", "order_id"),
            ("orders", "customer_id", "stg_customers", "customer_id"),
            ("stg_order_items", "order_id", "stg_orders", "order_id"),
        }

    def test_has_manifest_true_for_fusion_output(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        src = DbtSchemaSource(
            adapter=FakeAdapter(), project=_fusion_project(tmp_path, local_project)
        )
        assert src.has_manifest is True


# ---------------------------------------------------------------------------
# Partition-fetch SQL literal escaping
# ---------------------------------------------------------------------------


@dataclass
class _PartitionResult:
    rows: list[Any] = field(default_factory=list)
    column_names: list[str] = field(default_factory=list)


@dataclass
class _PartitionCaptureAdapter:
    """Duck of BaseAdapter for the partition-fetch path.

    Records every SQL string handed to ``execute`` so a test can assert how the
    inlined table/schema literal was escaped, and returns empty result sets.
    """

    adapter_type: str
    executed: list[str] = field(default_factory=list)

    def type(self) -> str:  # noqa: A003 — mirrors dbt adapter method name
        return self.adapter_type

    connections = SimpleNamespace(
        get_thread_connection=lambda: SimpleNamespace(handle=object())
    )

    @contextmanager
    def connection_named(self, name: str) -> Any:
        yield self

    def execute(self, sql: str, fetch: bool = False) -> tuple[None, _PartitionResult]:
        self.executed.append(sql)
        return None, _PartitionResult()


class TestPartitionFetchLiteralEscaping:
    """Table/schema names inlined into partition-fetch SQL must be escaped for
    the engine that parses the literal. BigQuery and Snowflake both
    backslash-escape, so ``O'Brien`` arrives as ``'O\\'Brien'`` — never
    ``'O''Brien'``, which those engines read as the concatenation ``OBrien``,
    and never a value that can close its own literal and reach code position.
    """

    def test_bq_partition_fetch_backslash_escapes_quote(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        adapter = _PartitionCaptureAdapter(adapter_type="bigquery")
        src = DbtSchemaSource(adapter=adapter, project=local_project(tmp_path))
        relation = _FakeRelation(identifier="O'Brien", schema="ds", database="proj")
        src._fetch_bq_partitions(relation)
        assert len(adapter.executed) == 2  # col_sql + parts_sql
        for sql in adapter.executed:
            assert r"'O\'Brien'" in sql
            assert "'O''Brien'" not in sql

    def test_snowflake_partition_fetch_backslash_escapes_quote(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        adapter = _PartitionCaptureAdapter(adapter_type="snowflake")
        src = DbtSchemaSource(adapter=adapter, project=local_project(tmp_path))
        relation = _FakeRelation(identifier="O'Brien", schema="s'x", database="DB")
        src._fetch_snowflake_partitions(relation)
        assert adapter.executed
        for sql in adapter.executed:
            assert r"'O\'Brien'" in sql
            assert r"'s\'x'" in sql
            assert "'O''Brien'" not in sql
            assert "'s''x'" not in sql

    def test_bq_injection_cannot_break_out_of_literal(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A backslash before the quote defeats bare quote-doubling on BigQuery;
        the shared escaper doubles the backslash and backslash-escapes the quote,
        so the payload stays inside its literal."""
        adapter = _PartitionCaptureAdapter(adapter_type="bigquery")
        src = DbtSchemaSource(adapter=adapter, project=local_project(tmp_path))
        payload = "\\' UNION ALL SELECT 1 --"
        relation = _FakeRelation(identifier=payload, schema="ds", database="proj")
        src._fetch_bq_partitions(relation)
        assert adapter.executed
        for sql in adapter.executed:
            assert r"'\\\' UNION ALL SELECT 1 --'" in sql
