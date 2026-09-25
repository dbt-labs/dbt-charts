"""SQLite query adapter — filesystem-backed, local projects only.

Stage: EXECUTE
Purpose: Execute SQL queries against a SQLite file using stdlib sqlite3 (read-only).

Opens every connection in URI read-only mode so the driver refuses all writes.
Cloud never registers this adapter in its build_cloud_adapter_registry factory.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path  # noqa: TID251 — roots relative sqlite paths
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    is_sql_query,
)
from dbt_charts.core.compile.sql_guard import validate_select_only
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_ADAPTER_RELATIVE_PATH_NO_DATA_DIR,
    ERR_WAREHOUSE_RUNTIME,
)
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.adapters.base import (
    BaseAdapter,
    QueryParams,
    QueryResult,
    apply_row_limit_truncation,
    connection_failure,
    handle_adapter_error,
    plain_error,
    resolve_effective_row_limit,
)
from dbt_charts.core.execute.adapters.dbt_utils import DbtRefResolver
from dbt_charts.core.execute.sqlite_utils import sqlite_ro_uri as _sqlite_ro_uri
from dbt_charts.core.project import is_absolute_any_os

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.source import ResolvedSourceConfig
    from dbt_charts.core.project import Project


class SqliteAdapter(BaseAdapter):
    """Adapter for executing SQL queries against a SQLite file (read-only).

    Resolves relative paths via an injected data_dir so the file is always
    anchored to a fixed root regardless of process cwd.

    Supported query types: sql

    Example:
        >>> adapter = SqliteAdapter(data_dir=Path("/path/to/project"))
        >>> query = SqlQuery(sql="SELECT * FROM t", source="mydb")
        >>> result = adapter.execute(query, source_config=cfg)
    """

    def __init__(
        self, *, data_dir: Path | None = None, project: Project | None = None
    ) -> None:
        """Initialize SQLite adapter.

        Args:
            data_dir: Directory relative sqlite paths resolve against. None for
                hosts with no local filesystem to root against (e.g. Cloud) —
                resolving a relative path without one raises ValueError.
            project: Seam the dbt manifest is read through, so `{{ ref() }}`
                resolves. None for a host with no project to read.
        """
        self._data_dir = data_dir
        self._dbt_refs = DbtRefResolver(project)

    @property
    def supported_types(self) -> set[str]:
        """Return supported query types."""
        return {"sql"}

    def _can_execute(
        self, query: AnyQuery, source_config: ResolvedSourceConfig | None
    ) -> bool:
        """Claim SQL only against a resolved SQLite source."""
        return (
            is_sql_query(query)
            and source_config is not None
            and source_config.type == "sqlite"
        )

    def _execute(
        self,
        query: AnyQuery,
        variables: VariableValues | None = None,
        params: QueryParams = None,
        source_config: ResolvedSourceConfig | None = None,
    ) -> QueryResult:
        """Execute SQL against a SQLite file (read-only URI mode).

        Parameters are bound natively via sqlite3 — no literal inlining.
        SQLiteDialect uses ? placeholders; render_parameterized emits one ?
        per occurrence so the param list length matches the placeholder count.
        """
        if not is_sql_query(query):
            # _can_execute gates this — unreachable at runtime; narrows type to SqlQuery.
            return plain_error(f"Expected SQL query, got {query.query_type}")

        if query.setup_sql:
            return plain_error(
                "setup_sql is not supported for SQLite sources. "
                "Remove the setup_sql block or switch to a DuckDB source."
            )

        if source_config is None:
            return plain_error("SQLite source requires a source_config with 'path'.")

        raw_config: dict[str, Any] = source_config.model_dump(
            by_alias=True, exclude_none=True
        )
        db_path_str = raw_config.get("path")
        if not db_path_str:
            return plain_error("SQLite source_config missing required 'path' field.")

        # Resolve relative paths via data_dir (mirrors DuckDB's behavior). Without
        # this, a relative path like ./data/bird.sqlite resolves against the
        # process cwd, which breaks when dct is run from a different directory.
        if not is_absolute_any_os(str(db_path_str)):
            if self._data_dir is None:
                raise DbtChartsError.from_code(
                    ERR_ADAPTER_RELATIVE_PATH_NO_DATA_DIR,
                    adapter="SQLite",
                    path=str(db_path_str),
                )
            resolved_db_path = str((self._data_dir / str(db_path_str)).resolve())
        else:
            resolved_db_path = str(db_path_str)

        from dbt_charts.core.compile.template.parameterized import render_parameterized
        from dbt_charts.core.dialects import get_dialect

        try:
            sql, resolved_relations = self._dbt_refs.resolve(query.sql)
        except DbtChartsError as e:
            return handle_adapter_error("dbt ref resolution", e)
        sqlite_dialect = get_dialect("sqlite")
        resolved_variables = variables if variables is not None else {}
        if params is not None:
            resolved_sql = sql
            resolved_params: list[Any] = list(params)
        else:
            try:
                parameterized = render_parameterized(
                    sql,
                    variables=resolved_variables,
                    dialect=sqlite_dialect,
                    strict=not query.lenient_variables,
                )
                resolved_sql = parameterized.sql
                resolved_params = list(parameterized.params)
            except (ValueError, KeyError, TypeError) as e:
                return handle_adapter_error("SQL parameterization", e)

        # Resolved outside the try/except below: a malformed DCT_MAX_ROWS_CEILING
        # raises ValueError here, which must surface as a config error, not get
        # relabeled "sqlite SQL execution failed" by the driver-error boundary.
        # Bounds the driver's own fetch (cursor.fetchmany()) — resolved_sql is
        # sent to sqlite3 unmodified, so every statement shape (EXPLAIN, an
        # author's own inline LIMIT) behaves exactly as it would without this
        # ceiling.
        row_fetch_limit = resolve_effective_row_limit(query.limit)
        try:
            validate_select_only(resolved_sql, dialect="sqlite")
            try:
                conn = sqlite3.connect(_sqlite_ro_uri(resolved_db_path), uri=True)
            except sqlite3.OperationalError as e:
                # "unable to open database file" is an OperationalError, same as
                # a genuine SQL fault — separated here so a missing file is not
                # reported as the query being wrong.
                return connection_failure("sqlite", e)
            try:
                cursor = conn.execute(resolved_sql, resolved_params)
                columns = (
                    [desc[0] for desc in cursor.description]
                    if cursor.description
                    else []
                )
                rows = cursor.fetchmany(row_fetch_limit.fetch_limit)
            finally:
                conn.close()
        except sqlite3.OperationalError as e:
            return QueryResult(
                data=[],
                error=QueryError(
                    f"SQLite SQL execution failed: {e}", code=ERR_WAREHOUSE_RUNTIME
                ),
            )
        except Exception as e:  # noqa: BLE001 — sqlite adapter error boundary
            return handle_adapter_error("sqlite SQL execution", e)

        rows, truncated_reason = apply_row_limit_truncation(rows, row_fetch_limit)

        data = [dict(zip(columns, row, strict=False)) for row in rows]
        return QueryResult(
            data=data,
            columns=columns,
            resolved_relations=resolved_relations or None,
            truncated_reason=truncated_reason,
        )
