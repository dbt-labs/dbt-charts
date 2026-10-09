"""SchemaSource for a source reached through an Arrow driver.

A driver source has no dbt adapter, so ``DbtSchemaSource`` cannot list it.
Every level is answered from the one ``information_schema`` query
``bulk_schema_for_config`` already runs, in the shape ``SQLiteSchemaSource``
returns: no manifest, no lineage, ``kind`` always ``table``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from dbt_charts.core.connections import bulk_schema_for_config

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.source import SourceConfig
    from dbt_charts.core.inspect.bulk_schema import SchemaTree


_Payload = dict[str, Any]  # type-state: explicit_any — the resolver's JSON-ish payload


class DriverSchemaSource:
    """Live schema source over an Arrow driver. One bulk query per call."""

    name = "driver"
    generated_at: datetime | None = None
    has_manifest: bool = False

    def __init__(self, config: SourceConfig) -> None:
        self._config = config

    def _tree(self) -> SchemaTree:
        return bulk_schema_for_config(self._config)

    def list_schemas(
        self,
    ) -> _Payload | None:
        tree = self._tree()
        return {"schemas": {schema: {} for schema in tree}} if tree else None

    def list_tables(self, schema: str) -> _Payload | None:
        tables = self._tree().get(schema)
        if not tables:
            return None
        return {"tables": {table: {"kind": "table"} for table in tables}}

    def profile_table(
        self, schema: str, table: str, lineage_depth: int = 1
    ) -> _Payload | None:
        tables = self._tree().get(schema)
        columns = tables.get(table) if tables else None
        if not columns:
            return None
        return {
            "kind": "table",
            "table_exists": True,
            "columns": {
                name: {"actual_type": type_} for name, type_ in columns.items()
            },
            "upstream": [],
            "downstream": [],
        }
