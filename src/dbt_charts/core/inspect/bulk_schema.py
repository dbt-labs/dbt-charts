"""Whole-source schema in one query — the non-N+1 path.

``bulk_schema`` issues a single ``INFORMATION_SCHEMA.COLUMNS``-style query
(built per dialect on ``SQLDialect.bulk_schema_sql``) and parses it into an
ordered ``{schema: {table: {column: type}}}`` tree. This replaces the per-schema
``list_tables`` relation walk (``1 + N`` metadata round-trips) that hangs AI
prompt assembly on large warehouses.

The query runs through the standard ``AdapterRegistry.execute`` path with
``limit=None`` — the full column list is requested. If the
``execution.max_rows`` ceiling fires before the full result is fetched, a
``RuntimeError`` is raised (a truncated schema tree would silently omit
columns and produce incorrect AI prompt output). Routing through the
cache-aware executor is a deliberate follow-up; this module is the primitive.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.dialects import get_dialect

if TYPE_CHECKING:
    from dbt_charts.core.execute.adapters import AdapterRegistry

# Ordered schema tree: schema -> table -> column -> type.
SchemaTree = dict[str, dict[str, dict[str, str]]]

# Raw warehouse credential dict / adapter row values — heterogeneous JSON-ish
# scalars (str/int/bool/None), the same boundary as
# AdapterRegistry.resolve_source_config's dict[str, Any].
_RawValues = Mapping[str, Any]  # type-state: explicit_any — warehouse-boundary values


def bulk_schema_scope(source_type: str, source_config: _RawValues) -> str:
    """The dialect-specific whole-source scope for ``bulk_schema_sql``.

    BigQuery needs the region qualifier to span every dataset in one query;
    Snowflake names the database; Databricks names the Unity Catalog. The value
    comes from registered connection config, never user input.
    """
    scope = ""
    if source_type.lower() == "bigquery":
        location = source_config.get("location") or source_config.get("region")
        if location:
            scope = f"region-{str(location).lower()}"
    elif source_type.lower() == "snowflake":
        database = source_config.get("database")
        if database:
            scope = str(database)
    elif source_type.lower() == "databricks":
        namespace = source_config.get("catalog")
        if namespace:
            scope = str(namespace)
    return scope


def parse_bulk_schema_rows(rows: Iterable[_RawValues]) -> SchemaTree:
    """Parse ``INFORMATION_SCHEMA.COLUMNS``-shaped rows into a schema tree."""
    tree: SchemaTree = {}
    for row in rows:
        # Metadata column names vary in case across warehouses (Snowflake
        # upper-cases them); normalize before reading.
        lower = {str(k).lower(): v for k, v in row.items()}
        schema_name = lower.get("table_schema")
        table_name = lower.get("table_name")
        column_name = lower.get("column_name")
        if not schema_name or not table_name or not column_name:
            continue
        raw_type = lower.get("data_type")
        data_type = str(raw_type) if raw_type else ""
        tree.setdefault(str(schema_name), {}).setdefault(str(table_name), {})[
            str(column_name)
        ] = data_type
    return tree


def parse_bulk_table_rows(rows: Iterable[_RawValues]) -> dict[str, list[str]]:
    """Parse ``(table_schema, table_name)`` rows into table names by schema."""
    tables: dict[str, list[str]] = {}
    for row in rows:
        lower = {str(k).lower(): v for k, v in row.items()}
        schema_name = lower.get("table_schema")
        table_name = lower.get("table_name")
        if not schema_name or not table_name:
            continue
        tables.setdefault(str(schema_name), []).append(str(table_name))
    return tables


def bulk_schema(source: str, adapter_registry: AdapterRegistry) -> SchemaTree:
    """Return the whole source's schema tree from a single warehouse query.

    Fails only through a narrow contract, so a best-effort caller can catch it
    precisely instead of a bare ``Exception``:

      * ``DbtChartsError`` — the source name does not resolve.
      * ``NotImplementedError`` — the dialect has no bulk-introspection form
        (e.g. BigQuery without a region qualifier).
      * ``RuntimeError`` — the query returned or raised an error (any driver
        exception is normalized into this).
    """
    source_config = adapter_registry.resolve_source_config(source)
    source_type = str(source_config.get("type"))
    dialect = get_dialect(source_type)
    scope = bulk_schema_scope(source_type, source_config)
    sql = dialect.bulk_schema_sql(scope)

    try:
        result = adapter_registry.execute(SqlQuery(sql=sql, source=source, limit=None))
    except DbtChartsError:
        raise
    except Exception as e:  # noqa: BLE001 — normalize driver errors to RuntimeError
        raise RuntimeError(f"bulk_schema query failed for {source!r}: {e}") from e
    if result.error:
        raise RuntimeError(f"bulk_schema query failed for {source!r}: {result.error}")
    if result.truncated_reason is not None:
        raise RuntimeError(
            f"bulk_schema query for {source!r} was truncated to {len(result.data)} rows "
            f"by {result.truncated_reason!r} — schema tree is incomplete. "
            "Raise execution.max_rows or DCT_MAX_ROWS_CEILING above the source's "
            "column count to fix this."
        )

    return parse_bulk_schema_rows(result.data)


def format_bulk_schema(schema: SchemaTree, source: str, dialect: str) -> str:
    """Render a schema tree into a compact prompt block."""
    lines = [f"## Schema: {source} ({dialect})"]
    for schema_name, tables in schema.items():
        for table_name, columns in tables.items():
            lines.append(f"\n### {schema_name}.{table_name}")
            for column_name, data_type in columns.items():
                suffix = f" ({data_type})" if data_type else ""
                lines.append(f"- {column_name}{suffix}")
    return "\n".join(lines)
