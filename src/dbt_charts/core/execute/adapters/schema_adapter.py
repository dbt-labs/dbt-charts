"""Schema adapter — queries the LayeredSchemaResolver in-process.

Stage: EXECUTE
Purpose: Dispatch board queries to the LayeredSchemaResolver without a subprocess.

Routing by field population:
- (no source)        → list_sql_sources via adapter registry (source name rows)
- source only        → list_schemas(source); returns QueryResult(error=...) on unknown source
- source + schema    → list_tables(source, schema)
- source + schema + table        → profile_table → column rows
- source + schema + table + column → profile_column → single column row

Field-prerequisite and Jinja-rejection invariants are enforced at compile time
by SchemaQuery's Pydantic validators — the adapter trusts them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.source import ResolvedSourceConfig

from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    is_schema_query,
)
from dbt_charts.core.execute.adapters.base import (
    BaseAdapter,
    QueryParams,
    QueryResult,
    plain_error,
)

if TYPE_CHECKING:
    from dbt_charts.core.execute.adapters import AdapterRegistry


class SchemaAdapter(BaseAdapter):
    """Adapter for in-process schema resolver queries.

    Supported query types: schema

    Dispatches to list_schemas / list_tables / profile_table / profile_column
    on the LayeredSchemaResolver based on which fields are populated.
    """

    def __init__(self, adapter_registry: AdapterRegistry) -> None:
        self._registry = adapter_registry

    @property
    def supported_types(self) -> set[str]:
        return {"schema"}

    def _execute(
        self,
        query: AnyQuery,
        variables: VariableValues | None = None,
        params: QueryParams = None,
        source_config: ResolvedSourceConfig | None = None,
    ) -> QueryResult:
        if not is_schema_query(query):
            return plain_error(f"Expected schema query, got {query.query_type}")

        source = query.source
        schema = query.schema_name
        table = query.table
        column = query.column

        if source is None:
            # No source specified: list all configured sources via the registry.
            source_rows = [dict(entry) for entry in self._registry.list_sql_sources()]
            return QueryResult(data=query.project(query.apply_limit(source_rows)))

        from dbt_charts.core.inspect.cache_factory import (
            build_resolver,  # noqa: PLC0415
        )

        resolver = build_resolver(self._registry)

        if schema is None:
            # An unknown source raises DbtChartsError(ERR-SOURCE-NOT-FOUND)
            # here; that propagates through the executor and renders as a
            # structured error page. No catch needed — fail loud by propagation,
            # symmetric with the list_tables/profile_table branches below.
            envelope = resolver.list_schemas(source)
            schemas: dict[str, Any] = (
                envelope.get("sources", {}).get(source, {}).get("schemas", {})
            )
            rows = [{**m, "name": n} for n, m in schemas.items()]
            return QueryResult(data=query.project(query.apply_limit(rows)))

        if table is None:
            envelope = resolver.list_tables(source, schema)
            source_schemas: dict[str, Any] = (
                envelope.get("sources", {}).get(source, {}).get("schemas", {})
            )
            if schema not in source_schemas:
                return plain_error(f"Schema {schema!r} not found in source {source!r}")
            tables: dict[str, Any] = source_schemas[schema].get("tables", {})
            rows = [{**m, "name": n} for n, m in tables.items()]
            return QueryResult(data=query.project(query.apply_limit(rows)))

        if column is None:
            envelope = resolver.profile_table(source, schema, table)
            all_tables: dict[str, Any] = (
                envelope.get("sources", {})
                .get(source, {})
                .get("schemas", {})
                .get(schema, {})
                .get("tables", {})
            )
            if table not in all_tables:
                return plain_error(
                    f"Table {schema}.{table} not found in source {source!r}"
                )
            columns: dict[str, Any] = all_tables[table].get("columns") or {}
            rows = [{**m, "name": n} for n, m in columns.items()]
            return QueryResult(data=query.project(query.apply_limit(rows)))

        # column specified → profile_column
        envelope = resolver.profile_column(source, schema, table, column)
        col_map: dict[str, Any] = (
            envelope.get("sources", {})
            .get(source, {})
            .get("schemas", {})
            .get(schema, {})
            .get("tables", {})
            .get(table, {})
            .get("columns")
            or {}
        )
        if column not in col_map:
            # The resolver drops the schemas key for both "table missing" and
            # "column missing" — probe profile_table to emit the right message.
            table_env = resolver.profile_table(source, schema, table)
            table_found = table in (
                table_env.get("sources", {})
                .get(source, {})
                .get("schemas", {})
                .get(schema, {})
                .get("tables", {})
            )
            if not table_found:
                return plain_error(
                    f"Table {schema}.{table} not found in source {source!r}"
                )
            return plain_error(f"Column {column!r} not found in {schema}.{table}")
        return QueryResult(data=query.project([{**col_map[column], "name": column}]))


def fetch_fk_edges(
    adapter_registry: AdapterRegistry,
    table: str,
) -> list[dict[str, str]]:
    """Return FK edges for ``table`` from dbt manifest + super-schema heuristics.

    Declared dbt relationship edges (confidence 1.0) are always included.
    Super-schema heuristic edges are included when ``is_recommended=True``
    (confidence >= 0.80 — the bimodal high-confidence naming case).

    All edges are returned in the canonical ``from_table/from_column/to_table/
    to_column`` format that ``resolve_fk_column_links`` expects.

    Only outbound edges (where the FK column lives on ``table``) are returned.
    Inbound edges (another table referencing ``table``) are skipped — they are
    not FK columns on the queried table.

    **Schema not consulted.** Both the dbt manifest and the super-schema cache
    key relationships by bare table name only, not by schema. A project with
    two tables named ``deal`` in different schemas will see edges from both
    merged together. Cross-schema disambiguation is deferred to Phase 3.

    Returns an empty list when:
    - No manifest exists at target/manifest.json.
    - No super-schema cache exists or no recommended heuristic edges found.

    Args:
        adapter_registry: The executor's adapter registry (provides project).
        table: Table name to fetch FK edges for.
    """

    from dbt_charts.core.inspect.cache_factory import build_resolver  # noqa: PLC0415

    resolver = build_resolver(adapter_registry)

    # Declared FK edges from dbt manifest (confidence=1.0, always included).
    edges: list[dict[str, str]] = [
        e
        for e in resolver._all_manifest_relationships()  # noqa: SLF001 — internal seam
        if e["from_table"] == table
    ]

    # Heuristic recommended edges from the super-schema cache.
    # Use left_table/left_column/right_table/right_column format; normalize to
    # from_*/to_* for resolve_fk_column_links. Only outbound edges (left_table
    # == table) are included; inbound edges are skipped.
    if resolver.cache is not None:
        declared_cols = {e["from_column"] for e in edges}
        raw = resolver.cache._read_raw()  # noqa: SLF001 — no public equivalent
        if isinstance(raw, dict):
            _tables = raw.get("tables")
            if isinstance(_tables, dict):
                for tbl_data in _tables.values():
                    tbl_name = tbl_data.get("table_name")
                    if tbl_name != table:
                        continue
                    _rels = tbl_data.get("relationships")
                    if not isinstance(_rels, list):
                        continue
                    for rel in _rels:
                        if not rel.get("is_recommended"):
                            continue
                        if rel.get("left_table") != table:
                            continue  # inbound edge — skip
                        from_col = rel["left_column"]
                        if from_col in declared_cols:
                            continue  # declared edge already covers this column
                        edges.append(
                            {
                                "from_table": table,
                                "from_column": from_col,
                                "to_table": rel["right_table"],
                                "to_column": rel["right_column"],
                            }
                        )

    return edges
