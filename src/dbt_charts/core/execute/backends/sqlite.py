"""SQLite backend — filesystem-backed, local projects only.

Stage: EXECUTE
Purpose: Execute composed SQL against a SQLite file using stdlib sqlite3,
    binding params natively, and return typed rows.

Opens every connection in URI read-only mode so the driver refuses all writes.
Cloud never routes the ``sqlite`` kind.

SQLite is dynamically typed and the driver reports no result types, so column
types are inferred from the returned values, as for a values query. A column
with no non-null value (every column of an empty result) is declared ``string``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path  # noqa: TID251 — roots relative sqlite paths
from typing import TYPE_CHECKING

from dbt_charts.core.column_types import (
    ColumnType,
    TypedRows,
    declared_literal_cells,
    literal_schema,
    reject_duplicate_columns,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_ADAPTER_RELATIVE_PATH_NO_DATA_DIR,
    ERR_WAREHOUSE_RUNTIME,
)
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.dialects import SQLDialect, get_dialect
from dbt_charts.core.execute.adapters.dbt_adapter_factory import ConnectionSetupFailed
from dbt_charts.core.execute.sqlite_utils import sqlite_ro_uri
from dbt_charts.core.project import is_absolute_any_os

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.source import ResolvedSourceConfig
    from dbt_charts.core.execute.compose import ResolvedQuery


class SqliteBackend:
    """Runs SQLite sources, read-only, on a connection opened per query."""

    param_style: SQLDialect = get_dialect("sqlite")

    def __init__(self, *, data_dir: Path | None = None) -> None:
        """Initialize the backend.

        Args:
            data_dir: Directory relative sqlite paths resolve against, so the file
                is anchored to a fixed root regardless of process cwd. None for
                hosts with no local filesystem to root against — resolving a
                relative path without one raises.
        """
        self._data_dir = data_dir

    def execute(self, source: ResolvedSourceConfig, query: ResolvedQuery) -> TypedRows:
        """Run the statement against the source's file.

        Raises:
            DbtChartsError: A relative path with no ``data_dir``.
            ConnectionSetupFailed: The file could not be opened.
            QueryError: ``setup_sql`` was authored, or SQLite rejected the statement.
        """
        if query.setup_sql:
            raise QueryError(
                "setup_sql is not supported for SQLite sources. "
                "Remove the setup_sql block or switch to a DuckDB source."
            )
        path = source.model_dump(by_alias=True, exclude_none=True).get("path")
        if not path:
            raise QueryError("SQLite source_config missing required 'path' field.")
        try:
            conn = sqlite3.connect(
                sqlite_ro_uri(self._resolved_path(str(path))), uri=True
            )
        except sqlite3.OperationalError as exc:
            # "unable to open database file" is an OperationalError, same as a
            # genuine SQL fault — separated here so a missing file is not
            # reported as the query being wrong.
            raise ConnectionSetupFailed(exc) from exc
        try:
            cursor = conn.execute(query.sql, query.params)
            names = [
                desc[0]
                for desc in cursor.description
                or ()  # type-state: silent_fallback — a statement with no result set has no columns
            ]
            rows = cursor.fetchmany(query.limit.fetch_limit)
        except sqlite3.OperationalError as exc:
            raise QueryError.from_code(ERR_WAREHOUSE_RUNTIME, detail=str(exc)) from exc
        finally:
            conn.close()
        reject_duplicate_columns(names)
        data = [dict(zip(names, row, strict=True)) for row in rows]
        schema = literal_schema(data, names, untyped=ColumnType(kind="string"))
        cells = declared_literal_cells(data, schema)
        return TypedRows(
            names, [tuple(row.values()) for row in cells], list(schema.values())
        )

    def interrupt(self) -> None:
        """Nothing to cancel: a query's connection lives only for that query."""

    def close(self) -> None:
        """Nothing to release: no connection outlives a query."""

    def _resolved_path(self, path: str) -> str:
        if is_absolute_any_os(path):
            return path
        if self._data_dir is None:
            raise DbtChartsError.from_code(
                ERR_ADAPTER_RELATIVE_PATH_NO_DATA_DIR, adapter="SQLite", path=path
            )
        return str((self._data_dir / path).resolve())
