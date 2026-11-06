"""Run queries on a single DuckDB or SQLite backend through the real registry.

A test wants one backend with its own constructor arguments, not the full
``build_exec_env`` wiring; this routes it exactly as the registry does
(compose, then ``Backend.execute``, then Arrow to rows).
"""

from __future__ import annotations

from pathlib import Path  # noqa: TID251 — a test's data_dir
from typing import TYPE_CHECKING

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.query.normalized import AnyQuery, is_sql_query
from dbt_charts.core.compile.models.source import (
    DuckDBSourceConfig,
    ResolvedSourceConfig,
)
from dbt_charts.core.execute.backends.base import BackendRegistry
from dbt_charts.core.execute.backends.duckdb_local import (
    DuckDBBackend,
    default_duckdb_source,
)
from dbt_charts.core.execute.backends.sqlite import SqliteBackend
from dbt_charts.core.execute.env import close_env, make_exec_env
from dbt_charts.core.execute.query_result import QueryParams, QueryResult
from dbt_charts.core.execute.run import run_query
from dbt_charts.core.project import Project

if TYPE_CHECKING:
    import duckdb


class DuckDBRunner:
    """One ``DuckDBBackend`` behind a registry. A query with no source runs on
    *source_config*, else on ``:memory:``."""

    def __init__(
        self,
        *,
        source_config: DuckDBSourceConfig | None = None,
        data_dir: Path | None = None,
        read_only: bool = True,
        duckdb_config: dict[str, object] | None = None,
        allow_external_access_in_readonly: bool = False,
        dbt_project_path: str | None = None,
        project: Project | None = None,
    ) -> None:
        self.backend = DuckDBBackend(
            data_dir=data_dir,
            read_only=read_only,
            duckdb_config=duckdb_config,
            allow_external_access_in_readonly=allow_external_access_in_readonly,
        )
        self._configured_source = source_config
        self._dbt_project_path = Path(dbt_project_path) if dbt_project_path else None
        self.registry = make_exec_env(
            project=project
            if project is not None
            else FilesystemProject(data_dir or Path.cwd()),
            backends=BackendRegistry({"duckdb": self.backend}),
        )

    def _default_source(self) -> DuckDBSourceConfig:
        configured = self._configured_source
        if configured is not None and configured.path != ":memory:":
            return configured
        return default_duckdb_source(self._dbt_project_path)

    def execute(
        self,
        query: AnyQuery,
        variables: VariableValues | None = None,
        params: QueryParams = None,
        source_config: ResolvedSourceConfig | None = None,
    ) -> QueryResult:
        if source_config is None and is_sql_query(query):
            source_config = self._default_source()
        return run_query(
            self.registry, query, variables, params, source_config=source_config
        )

    def interrupt(self) -> None:
        self.registry.backends.interrupt()

    def pooled_connection(
        self, source: DuckDBSourceConfig | dict[str, object] | None = None
    ) -> duckdb.DuckDBPyConnection:
        """This thread's pooled connection for *source* (default: the default source)."""
        config = (
            DuckDBSourceConfig.model_validate(source)
            if isinstance(source, dict)
            else source or self._default_source()
        )
        raw = config.model_dump(by_alias=True)
        return self.backend._pooled_connection(  # noqa: SLF001
            raw,
            self.backend._resolved_path(raw),  # noqa: SLF001
        )

    def close(self) -> None:
        close_env(self.registry)


class SqliteRunner:
    """One ``SqliteBackend`` behind a registry."""

    def __init__(self, *, data_dir: Path | None = None) -> None:
        self.registry = make_exec_env(
            project=FilesystemProject(data_dir or Path.cwd()),
            backends=BackendRegistry({"sqlite": SqliteBackend(data_dir=data_dir)}),
        )

    def execute(
        self,
        query: AnyQuery,
        variables: VariableValues | None = None,
        params: QueryParams = None,
        source_config: ResolvedSourceConfig | None = None,
    ) -> QueryResult:
        return run_query(
            self.registry, query, variables, params, source_config=source_config
        )

    def close(self) -> None:
        close_env(self.registry)
