"""Cold-start `SchemaSource` composing a dbt-core adapter + lazy manifest.

The dbt-core adapter (built by ``build_adapter()`` with macros loaded per
the bootstrap-dbt-macro-manifest task) answers live questions:
``list_schemas``, ``list_relations``, ``get_columns_in_relation``,
``get_column_schema_from_query``. The manifest contributes only what is
*explicitly declared* — descriptions, declared types, listed tests, tags,
owner, and ``relationships:`` tests.

No inference. No naming heuristics. No FK guessing from ``<x>_id → <x>``.
If the manifest doesn't carry the field explicitly, it's absent — empty is
the honest answer.

Cross-table derivations (reverse-FK roll-up, M2M two-hop, lineage walks)
live in the Phase 4 resolver, not here.
"""

from __future__ import annotations

import re
from collections import deque
from datetime import datetime, timezone
from typing import Any

from dbt_common.exceptions.base import DbtRuntimeError

from dbt_charts.core.dbt_manifest import load_manifest
from dbt_charts.core.dialects import get_dialect
from dbt_charts.core.execute.adapters.dbt_adapter_factory import open_connection
from dbt_charts.core.execute.sql_literals import sql_string_literal
from dbt_charts.core.inspect.partition_types import (
    PartitionEntry,
    PartitionType,
    TablePartitions,
)
from dbt_charts.core.inspect.sources.duckdb_utils import duckdb_resolve_database
from dbt_charts.core.project import Project

# Stripped from manifest test kwargs because they're dbt's plumbing, not
# user-facing test config: every generic test carries them.
_INTERNAL_TEST_KWARGS: frozenset[str] = frozenset({"column_name", "model"})

# Unique-id prefixes that represent actual warehouse tables. Other prefixes
# (test.*, exposure.*, metric.*, semantic_model.*, unit_test.*, saved_query.*,
# analysis.*) are dbt metadata nodes, not tables, and must be filtered out
# before returning lineage neighbors.
_TABLE_UID_PREFIXES: tuple[str, ...] = ("model.", "source.", "seed.", "snapshot.")

# `kwargs.to` looks like "ref('users')" or "source('raw', 'users')". The
# resolver in Phase 4 owns lineage walks; here we only need the target
# table name to pair with `kwargs.field`.
_REF_RE = re.compile(r"ref\(\s*['\"]([^'\"]+)['\"]\s*\)")
_SOURCE_RE = re.compile(
    r"source\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)"
)
_SNOWFLAKE_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_BQ_TIME_PARTITION_TYPES = frozenset({"DATE", "DATETIME", "TIMESTAMP"})
# BigQuery integer-range partitioning only supports INTEGER/INT64 columns.
_BQ_RANGE_PARTITION_TYPES = frozenset({"INT64", "INTEGER"})


def _bq_identifier(value: str | None) -> str:
    if value is None or "`" in value:
        raise ValueError(f"Unsupported BigQuery identifier: {value!r}")
    return value


def _snowflake_identifier(value: str | None) -> str:
    if value is None or value == "" or "\x00" in value:
        raise ValueError(f"Unsupported Snowflake database identifier: {value!r}")
    if _SNOWFLAKE_IDENTIFIER_RE.fullmatch(value):
        return value
    return '"' + value.replace('"', '""') + '"'


def _json_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("last_modified must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _bq_partition_type(column: str, data_type: str | None) -> PartitionType:
    if data_type is None:
        raise ValueError(f"Missing BigQuery partition column type for {column!r}")
    normalized = data_type.upper()
    if normalized in _BQ_TIME_PARTITION_TYPES:
        return "time"
    if normalized in _BQ_RANGE_PARTITION_TYPES:
        return "range"
    raise ValueError(f"Unsupported BigQuery partition column type: {data_type!r}")


class DbtSchemaSource:
    """`SchemaSource` composing a live dbt adapter + manifest reader."""

    name = "dbt"
    generated_at: datetime | None = None  # live source — no cache build time

    def __init__(
        self,
        adapter: Any,
        project: Project,
        *,
        db_path: str | None = None,
    ) -> None:
        self._adapter = adapter
        self._project = project
        # db_path is the configured file path for DuckDB sources; None for
        # in-memory or non-DuckDB.  Used by _resolve_duckdb_database to read
        # PRAGMA database_list once and cache the attach name.
        self._db_path = db_path
        self._manifest_loaded = False
        self._manifest: dict[str, Any] | None = None
        # ``(schema_lower, table_lower) -> (uid, model_node)``. Built once
        # from the manifest so list_tables doesn't walk every node per
        # relation. ``None`` means "haven't built it yet."
        self._model_index: dict[tuple[str, str], tuple[str, dict[str, Any]]] | None = (
            None
        )
        # Per-schema relation cache. Avoids the N+1 in level-4 wildcard
        # queries: the resolver iterates expanded targets and each
        # ``profile_table(schema, t)`` would otherwise re-list the whole
        # schema. Instance-scoped — fresh on every resolver call cycle
        # (the resolver builds DbtSchemaSource per-source per-call).
        self._relations_by_schema: dict[str, list[Any]] = {}
        # Lazy (schema_lower, name_lower) → unique_id index for lineage
        # resolution. Covers models, seeds, snapshots from nodes and
        # sources from the sources section. None = not yet built.
        self._uid_index: dict[tuple[str, str], str] | None = None
        # Resolved DuckDB attach name — None means "not yet resolved".
        # Only populated for DuckDB adapters; stays None for all others.
        self._duckdb_database: str | None = None
        self._duckdb_database_resolved: bool = False

    @property
    def has_manifest(self) -> bool:
        """Is a manifest available with at least one node? (Not "did it
        contribute to this call.") Lazy: forces the load.

        Per-call manifest contribution is decided in the resolver by
        inspecting whether the response actually carries manifest-only keys
        (description / tags / declared_type / tests / relationships / etc.),
        because a request for a table that exists in the warehouse but is
        absent from the manifest must report ``sources_consulted=["dbt_adapter"]``,
        not ``["dbt_adapter", "dbt_manifest"]``.
        """
        manifest = self._load_manifest()
        return bool(manifest and manifest.get("nodes"))

    # ---- SchemaSource methods ---------------------------------------------

    def _credentials_database(self) -> str | None:
        """Return the database name from adapter credentials, or None if absent.

        Snowflake requires a database argument for list_schemas / list_relations.
        DuckDB uses a path-based attach (see _resolve_duckdb_database); its
        credentials carry no database field, so this returns None for DuckDB.
        """
        config = getattr(self._adapter, "config", None)
        creds = getattr(config, "credentials", None)
        return getattr(creds, "database", None)

    def list_schemas(self) -> dict[str, Any] | None:
        database = self._credentials_database()
        with open_connection(self._adapter, "dct_schema_list_schemas"):
            schemas = list(self._adapter.list_schemas(database))
        # De-duplicate while preserving first-seen order. dbt's list_schemas
        # may return duplicates across databases (e.g. DuckDB returns 'main'
        # multiple times when the same DB is attached more than once).
        seen: dict[str, None] = {}
        for s in schemas:
            if s and s not in seen:
                seen[s] = None
        if not seen:
            return None
        return {"schemas": {name: {} for name in seen}}

    def _resolve_duckdb_database(self) -> str | None:
        """Return the DuckDB attach name for this adapter, resolved once.

        Returns None for non-DuckDB adapters.  For DuckDB, reads
        ``PRAGMA database_list`` and caches the result — one round-trip
        per DbtSchemaSource instance, amortized across all ``_get_relations``
        calls in one resolver cycle.
        """
        if self._duckdb_database_resolved:
            return self._duckdb_database
        self._duckdb_database_resolved = True
        if self._adapter.type() != "duckdb":
            return None
        with open_connection(self._adapter, "dct_schema_resolve_db"):
            self._duckdb_database = duckdb_resolve_database(
                self._adapter, self._db_path
            )
        return self._duckdb_database

    def _get_relations(self, schema: str) -> list[Any]:
        """Return the relations in ``schema``; populate the per-schema cache.

        Single source of truth for the warehouse round-trip. Both
        ``list_tables`` and ``profile_table`` route through here so a
        wildcard level-4 walk only pays one ``list_relations`` per schema.

        For file-backed DuckDB the database argument must be the attach name
        (e.g. 'dundersign'), not None — passing None returns an empty list.
        ``_resolve_duckdb_database`` reads PRAGMA database_list once per
        source per cycle to get the correct name.
        """
        cached = self._relations_by_schema.get(schema)
        if cached is not None:
            return cached
        database = self._resolve_duckdb_database() or self._credentials_database()
        with open_connection(self._adapter, "dct_schema_list_relations"):
            relations = list(self._adapter.list_relations(database, schema))
        self._relations_by_schema[schema] = relations
        return relations

    def list_tables(self, schema: str) -> dict[str, Any] | None:
        relations = self._get_relations(schema)
        if not relations:
            return None
        out: dict[str, dict[str, Any]] = {}
        for rel in relations:
            name = rel.identifier
            summary: dict[str, Any] = {"kind": _relation_kind(rel)}
            entry = self._lookup_model(schema=schema, table=name)
            if entry is not None:
                self._apply_table_manifest(summary, entry[1])
            out[name] = summary
        return {"tables": out}

    def profile_table(
        self, schema: str, table: str, lineage_depth: int = 1
    ) -> dict[str, Any] | None:
        relations = self._get_relations(schema)
        relation = next((r for r in relations if r.identifier == table), None)
        if relation is None:
            return None
        with open_connection(self._adapter, "dct_schema_profile_table"):
            adapter_columns = list(self._adapter.get_columns_in_relation(relation))

        entry = self._lookup_model(schema=schema, table=table)
        node_uid = entry[0] if entry else None
        node = entry[1] if entry else None
        manifest_cols = (node or {}).get("columns") or {}
        tests_by_column, rels_by_column = (
            self._collect_column_tests(node_uid, node) if node else ({}, {})
        )

        cols_out: dict[str, dict[str, Any]] = {}
        for col in adapter_columns:
            col_name = col.name
            col_entry: dict[str, Any] = {"actual_type": _column_dtype(col)}
            manifest_col = manifest_cols.get(col_name)
            if isinstance(manifest_col, dict):
                _apply_column_manifest(col_entry, manifest_col)
            tests = tests_by_column.get(col_name)
            if tests:
                col_entry["tests"] = tests
            rels = rels_by_column.get(col_name)
            if rels:
                col_entry["relationships"] = rels
            cols_out[col_name] = col_entry

        # Resolve unique_id for lineage walks. Uses the broader uid index
        # (covers seeds/snapshots/sources) rather than the model-only index.
        table_uid = self._lookup_uid(schema=schema, table=table)
        upstream = self._lineage_neighbors(table_uid, "parent_map", lineage_depth)
        downstream = self._lineage_neighbors(table_uid, "child_map", lineage_depth)

        partitions = self._fetch_partitions(relation)
        last_modified = self._fetch_last_modified(relation)

        out: dict[str, Any] = {
            "kind": _relation_kind(relation),
            "table_exists": True,
            "columns": cols_out,
            "upstream": upstream,
            "downstream": downstream,
            "partitions": partitions.model_dump(mode="json", exclude_none=True),
        }
        if last_modified is not None:
            out["last_modified"] = _json_datetime(last_modified)
        if node is not None:
            self._apply_table_manifest(out, node)
        return out

    def _fetch_partitions(self, relation: Any) -> TablePartitions:
        adapter_type = self._adapter.type()
        if adapter_type == "bigquery":
            return self._fetch_bq_partitions(relation)
        if adapter_type == "snowflake":
            return self._fetch_snowflake_partitions(relation)
        return TablePartitions(type="none", supported=False)

    def _fetch_bq_partitions(self, relation: Any) -> TablePartitions:
        """BigQuery-specific partition fetch via INFORMATION_SCHEMA.

        get_partitions_metadata uses the legacy $__PARTITIONS_SUMMARY__ table
        which lacks total_rows, total_logical_bytes, and the partition column name.
        INFORMATION_SCHEMA.PARTITIONS has all three; INFORMATION_SCHEMA.COLUMNS
        WHERE is_partitioning_column='YES' gives the column name.
        """
        db = _bq_identifier(relation.database)
        schema = _bq_identifier(relation.schema)
        table = relation.identifier
        table_literal = sql_string_literal(table, get_dialect(self._adapter.type()))
        col_sql = (
            f"SELECT column_name, data_type"
            f" FROM `{db}`.`{schema}`.INFORMATION_SCHEMA.COLUMNS"
            f" WHERE table_name = {table_literal}"
            f" AND is_partitioning_column = 'YES'"
            f" LIMIT 1"
        )
        parts_sql = (
            f"SELECT partition_id, total_rows, total_logical_bytes, last_modified_time"
            f" FROM `{db}`.`{schema}`.INFORMATION_SCHEMA.PARTITIONS"
            f" WHERE table_name = {table_literal}"
            f" AND partition_id NOT IN ('__NULL__', '__STREAMING_UNPARTITIONED__')"
            f" ORDER BY partition_id DESC"
        )
        with open_connection(self._adapter, "dct_schema_bq_partitions"):
            _, col_result = self._adapter.execute(col_sql, fetch=True)
            partition_col = str(col_result.rows[0][0]) if col_result.rows else None
            partition_data_type = (
                str(col_result.rows[0][1]) if col_result.rows else None
            )
            _, parts_result = self._adapter.execute(parts_sql, fetch=True)
        col_names = list(parts_result.column_names)
        entries = []
        for row in parts_result.rows:
            row_dict = dict(zip(col_names, row, strict=False))
            pid = str(row_dict.get("partition_id", ""))
            rc = row_dict.get("total_rows")
            sb = row_dict.get("total_logical_bytes")
            lm = row_dict.get("last_modified_time")
            entries.append(
                PartitionEntry(
                    partition_id=pid,
                    row_count=int(rc) if rc is not None else None,
                    size_bytes=int(sb) if sb is not None else None,
                    last_modified=lm if isinstance(lm, datetime) else None,
                )
            )
        if partition_col is None and not entries:
            return TablePartitions(type="unpartitioned", entries=[], supported=True)
        if partition_col is None:
            p_type: PartitionType = "ingestion"
        else:
            p_type = _bq_partition_type(partition_col, partition_data_type)
        return TablePartitions(
            column=partition_col,
            type=p_type,
            entries=entries,
            supported=True,
        )

    def _fetch_snowflake_partitions(self, relation: Any) -> TablePartitions:
        """Snowflake clustering-key fetch via INFORMATION_SCHEMA.TABLES.

        Snowflake uses clustering keys (not partitions) for scan pruning.
        CLUSTERING_KEY is a string like "LINEAR(col1, col2)" or an expression.
        Surface the raw expression so nested commas and quoted identifiers do not
        get corrupted by best-effort parsing.
        """
        database = _snowflake_identifier(relation.database)
        schema = relation.schema
        table = relation.identifier
        dialect = get_dialect(self._adapter.type())
        sql = (
            f"SELECT CLUSTERING_KEY"
            f" FROM {database}.INFORMATION_SCHEMA.TABLES"
            f" WHERE TABLE_SCHEMA = {sql_string_literal(schema, dialect)}"
            f" AND TABLE_NAME = {sql_string_literal(table, dialect)}"
            f" LIMIT 1"
        )
        with open_connection(self._adapter, "dct_schema_snowflake_partitions"):
            _, result = self._adapter.execute(sql, fetch=True)
        if not result.rows:
            return TablePartitions(type="unpartitioned", supported=True)
        raw = result.rows[0][0]
        if not raw:
            return TablePartitions(type="unpartitioned", supported=True)
        key_str = str(raw).strip()
        return TablePartitions(
            type="clustering", column=key_str, entries=[], supported=True
        )

    def _fetch_last_modified(self, relation: Any) -> datetime | None:
        try:
            with open_connection(self._adapter, "dct_schema_freshness"):
                _, freshness = self._adapter.calculate_freshness_from_metadata(relation)
        except (NotImplementedError, DbtRuntimeError):
            # NotImplementedError: adapter explicitly declares no support.
            # DbtRuntimeError: dbt-duckdb raises it with "macro not implemented"
            # for get_relation_last_modified — same semantic: freshness not supported.
            return None
        max_loaded_at = freshness.get("max_loaded_at") if freshness else None
        if isinstance(max_loaded_at, datetime) and max_loaded_at.year == 1:
            return None
        return max_loaded_at if isinstance(max_loaded_at, datetime) else None

    def describe_query(self, sql: str) -> dict[str, Any] | None:
        with open_connection(self._adapter, "dct_schema_describe_query"):
            cols = list(self._adapter.get_column_schema_from_query(sql))
        if not cols:
            return None
        return {"columns": {c.name: {"actual_type": _column_dtype(c)} for c in cols}}

    # ---- Lineage helpers ---------------------------------------------------

    def _build_uid_index(self) -> dict[tuple[str, str], str]:
        """Build (schema_lower, name_lower) → unique_id for all table nodes.

        Covers models, seeds, snapshots from manifest["nodes"] and raw
        warehouse sources from manifest["sources"]. Sources use "identifier"
        (warehouse table name) rather than "name" (the source alias).
        """
        index: dict[tuple[str, str], str] = {}
        manifest = self._load_manifest()
        if not manifest:
            return index
        for uid, node in manifest.get("nodes", {}).items():
            rt = node.get("resource_type")
            if rt not in {"model", "seed", "snapshot"}:
                continue
            schema = (node.get("schema") or "").lower()
            name = (
                node.get("identifier") or node.get("alias") or node.get("name") or ""
            ).lower()
            if schema and name:
                index[(schema, name)] = uid
        for uid, node in manifest.get("sources", {}).items():
            schema = (node.get("schema") or "").lower()
            identifier = (node.get("identifier") or node.get("name") or "").lower()
            if schema and identifier:
                index[(schema, identifier)] = uid
        return index

    def _lookup_uid(self, schema: str, table: str) -> str | None:
        if self._uid_index is None:
            self._uid_index = self._build_uid_index()
        return self._uid_index.get((schema.lower(), table.lower()))

    def _resolve_uid_ref(self, uid: str) -> dict[str, Any] | None:
        """Resolve a unique_id to a lineage ref dict {model_name, schema, table, kind}.

        ``model_name`` is the dbt symbol (what appears in ``ref()`` /
        ``source()``). ``table`` is the warehouse table name, which may differ
        when a model sets ``alias`` / ``identifier`` or a source sets
        ``identifier``.
        """
        manifest = self._load_manifest()
        if not manifest:
            return None
        if uid.startswith("source."):
            node = manifest.get("sources", {}).get(uid)
            if not isinstance(node, dict):
                return None
            schema = node.get("schema") or ""
            model_name = node.get("name") or ""
            table = node.get("identifier") or model_name
            kind = "source"
        else:
            node = manifest.get("nodes", {}).get(uid)
            if not isinstance(node, dict):
                return None
            schema = node.get("schema") or ""
            model_name = node.get("name") or ""
            table = node.get("identifier") or node.get("alias") or model_name
            kind = "ref"
        if not schema or not model_name:
            return None
        return {
            "model_name": model_name,
            "schema": schema,
            "table": table,
            "kind": kind,
        }

    def _lineage_neighbors(
        self,
        uid: str | None,
        edge_map_name: str,
        depth: int,
    ) -> list[dict[str, Any]]:
        """BFS over parent_map or child_map up to ``depth`` hops.

        Filters out non-table uid prefixes (test.*, exposure.*, metric.*,
        semantic_model.*, unit_test.*, saved_query.*, analysis.*). Returns
        a list of resolved lineage ref dicts, deduped via visited set.
        """
        if uid is None:
            return []
        manifest = self._load_manifest()
        if not manifest:
            return []
        edge_map: dict[str, list[str]] = manifest.get(edge_map_name, {})
        result: list[dict[str, Any]] = []
        visited: set[str] = {uid}
        queue: deque[tuple[str, int]] = deque([(uid, 0)])
        while queue:
            current_uid, d = queue.popleft()
            for neighbor_uid in edge_map.get(current_uid, []):
                if neighbor_uid in visited:
                    continue
                visited.add(neighbor_uid)
                if not any(neighbor_uid.startswith(p) for p in _TABLE_UID_PREFIXES):
                    continue
                ref = self._resolve_uid_ref(neighbor_uid)
                if ref is not None:
                    result.append(ref)
                if d + 1 < depth:
                    queue.append((neighbor_uid, d + 1))
        return result

    # ---- Manifest helpers --------------------------------------------------

    def _load_manifest(self) -> dict[str, Any] | None:
        if not self._manifest_loaded:
            loaded = load_manifest(self._project, optional=True)
            self._manifest = loaded.raw if loaded else None
            self._manifest_loaded = True
        return self._manifest

    def _build_model_index(self) -> dict[tuple[str, str], tuple[str, dict[str, Any]]]:
        index: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
        manifest = self._load_manifest()
        if not manifest:
            return index
        for uid, node in manifest.get("nodes", {}).items():
            if node.get("resource_type") != "model":
                continue
            schema = (node.get("schema") or "").lower()
            table = (node.get("alias") or node.get("name") or "").lower()
            if not schema or not table:
                continue
            index[(schema, table)] = (uid, node)
        return index

    def _lookup_model(
        self, schema: str, table: str
    ) -> tuple[str, dict[str, Any]] | None:
        if self._model_index is None:
            self._model_index = self._build_model_index()
        return self._model_index.get((schema.lower(), table.lower()))

    def _collect_column_tests(
        self,
        model_uid: str | None,
        model_node: dict[str, Any],
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, str]]]]:
        """Return (tests_by_column, relationships_by_column).

        Walks ``manifest.nodes`` for generic-test entries attached to the
        given model and groups them per column. ``relationships:`` tests
        are promoted into the structured relationships list and *not*
        also echoed in ``tests:`` — one canonical surfacing.
        """
        tests_by_column: dict[str, list[dict[str, Any]]] = {}
        rels_by_column: dict[str, list[dict[str, str]]] = {}
        manifest = self._load_manifest()
        if not manifest:
            return tests_by_column, rels_by_column

        model_name = model_node.get("name", "")
        file_key_target = f"models.{model_name}"

        for node in manifest.get("nodes", {}).values():
            if node.get("resource_type") != "test":
                continue
            attached = node.get("attached_node")
            file_key = node.get("file_key_name")
            if not (
                (
                    attached is not None
                    and model_uid is not None
                    and attached == model_uid
                )
                or (file_key == file_key_target and model_name)
            ):
                continue

            metadata = node.get("test_metadata") or {}
            test_name = metadata.get("name")
            if not test_name:
                continue
            raw_kwargs = metadata.get("kwargs") or {}
            column_name = node.get("column_name") or raw_kwargs.get("column_name")
            if not column_name:
                # Table-level tests aren't surfaced in this Phase 2 — they
                # have no clear column to attach to. Phase 3 may add them
                # to a table-level `tests` field.
                continue

            if test_name == "relationships":
                rel = _parse_relationships_target(raw_kwargs)
                if rel is not None:
                    rels_by_column.setdefault(column_name, []).append(rel)
                continue

            clean_kwargs = {
                k: v for k, v in raw_kwargs.items() if k not in _INTERNAL_TEST_KWARGS
            }
            tests_by_column.setdefault(column_name, []).append(
                {"name": test_name, "kwargs": clean_kwargs}
            )

        return tests_by_column, rels_by_column

    @staticmethod
    def _apply_table_manifest(out: dict[str, Any], node: dict[str, Any]) -> None:
        description = node.get("description")
        if isinstance(description, str) and description:
            out["description"] = description
        tags = node.get("tags")
        if isinstance(tags, list) and tags:
            out["tags"] = list(tags)
        meta = node.get("meta") or {}
        owner = meta.get("owner")
        if owner:
            out["owner"] = owner


def _column_dtype(col: Any) -> str:
    """Read a dbt-core Column's canonical ``dtype`` attribute."""
    return str(col.dtype)


def _relation_kind(relation: Any) -> str:
    kind = getattr(relation, "type", None)
    return str(kind) if kind else "table"


def _apply_column_manifest(entry: dict[str, Any], manifest_col: dict[str, Any]) -> None:
    description = manifest_col.get("description")
    if isinstance(description, str) and description:
        entry["description"] = description
    declared_type = manifest_col.get("data_type")
    if declared_type:
        entry["declared_type"] = declared_type
    tags = manifest_col.get("tags")
    if isinstance(tags, list) and tags:
        entry["tags"] = list(tags)
    granularity = manifest_col.get("granularity")
    if granularity:
        entry["granularity"] = granularity


def extract_all_relationships(
    manifest: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """Every forward FK declared by a ``relationships:`` test in the manifest.

    Returns a list of ``{from_table, from_column, to_table, to_column}``
    dicts — one per test that names a valid target. Empty when no manifest.
    Pure: takes a manifest dict and returns a list. The resolver uses this
    directly (without building a dbt adapter) so cache-hit short-circuits
    can still surface ``referenced_by`` / ``linked_via`` without paying
    for a warehouse-connection-bearing adapter build.
    """
    if not manifest:
        return []
    # Build uid → model name once so we can resolve ``attached_node`` to the
    # source table. Tests without ``attached_node`` fall back to
    # ``file_key_name = "models.<name>"``.
    uid_to_name: dict[str, str] = {}
    for uid, node in manifest.get("nodes", {}).items():
        if node.get("resource_type") == "model":
            name = node.get("alias") or node.get("name")
            if name:
                uid_to_name[uid] = name

    out: list[dict[str, str]] = []
    for node in manifest.get("nodes", {}).values():
        if node.get("resource_type") != "test":
            continue
        metadata = node.get("test_metadata") or {}
        if metadata.get("name") != "relationships":
            continue
        kwargs = metadata.get("kwargs") or {}
        target = _parse_relationships_target(kwargs)
        if target is None:
            continue
        from_column = node.get("column_name") or kwargs.get("column_name")
        if not from_column:
            continue
        from_table = _resolve_attached_table(node, uid_to_name)
        if not from_table:
            continue
        out.append(
            {
                "from_table": from_table,
                "from_column": from_column,
                "to_table": target["to_table"],
                "to_column": target["to_column"],
            }
        )
    return out


def _resolve_attached_table(
    test_node: dict[str, Any], uid_to_name: dict[str, str]
) -> str | None:
    """Return the source-table name for a generic-test node.

    Tests carry either ``attached_node`` (a model uid) or
    ``file_key_name`` (``"models.<name>"``). We prefer ``attached_node``
    because it survives renames; ``file_key_name`` is the fallback for
    older manifest shapes that don't stamp it.
    """
    attached = test_node.get("attached_node")
    if attached and attached in uid_to_name:
        return uid_to_name[attached]
    file_key = test_node.get("file_key_name")
    if isinstance(file_key, str) and file_key.startswith("models."):
        return file_key[len("models.") :]
    return None


def _parse_relationships_target(kwargs: dict[str, Any]) -> dict[str, str] | None:
    """Extract ``{to_table, to_column}`` from a relationships-test kwargs.

    ``kwargs.to`` is the Jinja string a user wrote (``ref('users')`` or
    ``source('raw', 'users')``); ``kwargs.field`` is the target column.
    Returns ``None`` if the kwargs aren't shaped like an explicit FK
    declaration — we don't guess.
    """
    to_str = kwargs.get("to")
    field = kwargs.get("field")
    if not isinstance(to_str, str) or not isinstance(field, str) or not field:
        return None
    m = _REF_RE.match(to_str.strip())
    if m:
        return {"to_table": m.group(1), "to_column": field}
    m = _SOURCE_RE.match(to_str.strip())
    if m:
        return {"to_table": m.group(2), "to_column": field}
    return None
