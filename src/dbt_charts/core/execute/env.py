"""What a board's queries run against.

Stage: EXECUTE
Purpose: ``ExecEnv`` holds a project's sources, resolver, backends, observers and
    file-source materializer as plain state. Running a query is ``run.run_query``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import (
    Path,  # noqa: TID251 — names the dbt project directory and DuckDB/SQLite files on disk
)
from typing import TYPE_CHECKING, Any, Final

from dbt_charts.cli.filesystem_project import (
    FilesystemProject,  # tach-ignore(core->cli: host-type guard; needs a non-cli signal on Project — deferred)
)
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.execute.backends.base import Backend, BackendRegistry
from dbt_charts.core.execute.dbt_utils import (
    DbtRefResolver,
)
from dbt_charts.core.execute.drivers import DRIVER_SOURCE_TYPES
from dbt_charts.core.execute.observability import WarehouseObserver
from dbt_charts.core.execute.source_registry import SourceRegistry
from dbt_charts.core.execute.source_resolver import (
    DbtContext,
    DefaultSourceResolver,
)
from dbt_charts.core.project import Project

if TYPE_CHECKING:
    from dbt_charts.core.execute.file_source_materializer import (
        FileSourceMaterializer,
    )
    from dbt_charts.core.execute.source_resolver import SourceResolver

# Empty source config used as the default when the caller has no project sources to
# pass.  Module-level Final avoids per-call construction; SourceRegistry never mutates
# its project_sources argument (only self._synthetic is modified by register()).
_EMPTY_PROJECT_SOURCES: Final[ProjectSourcesConfig] = ProjectSourcesConfig(sources={})

# DuckDB driver settings: an open mapping the driver defines.
DuckDBConfig = dict[str, Any]  # type-state: explicit_any — open driver settings
SpreadKwargs = dict[str, Any]  # type-state: explicit_any — spread as **kwargs

# Approved local-dev surfaces that need non-exclusive DuckDB locks AND SQL
# read_csv/read_json_auto (see m3-duckdb-concurrency-and-file-sources initiative).
# Grep this symbol before adding allow_external_access_in_readonly=True elsewhere.
# A DuckDB source that authors attach/extensions/secrets gets external access
# only while those fields are applied; DuckDBBackend._connect then locks the
# connection to the attach paths and secret scopes.
LOCAL_AUTHORING_ENV_KWARGS: SpreadKwargs = {
    "read_only": True,
    "allow_external_access_in_readonly": True,
    "duckdb_config": {"enable_external_access": True},
}


def linked_dbt_path(project: Project) -> Path | None:
    """The project's dbt project directory, or None when it has no dbt project.

    dbt_project.yml is valid at project.dbt_root (see resolve_dbt_project_dir for
    how that's resolved) -- meaningful only for a real filesystem project (dbt's
    own profiles/target resolution assumes a directory).
    """
    return (
        project.dbt_root
        if isinstance(project, FilesystemProject)
        and project.dbt_project.exists("dbt_project.yml")
        else None
    )


def warehouse_routes(max_workers: int | None) -> dict[str, Backend]:
    """The routes of the warehouse kinds: Arrow drivers share one backend, dbt
    adapters another."""
    from dbt_charts.core.execute.backends.dbt_adapter_factory import DBT_SOURCE_TYPES
    from dbt_charts.core.execute.backends.dbt_core import DbtBackend
    from dbt_charts.core.execute.backends.driver import DriverBackend

    return {
        **dict.fromkeys(DRIVER_SOURCE_TYPES, DriverBackend(max_workers)),
        **dict.fromkeys(DBT_SOURCE_TYPES, DbtBackend(max_workers)),
    }


def build_exec_env(
    project: Project,
    *,
    read_only: bool = True,
    profile_type: str = "duckdb",
    target: str | None = None,
    duckdb_config: DuckDBConfig | None = None,
    resolver: SourceResolver | None = None,
    allow_external_access_in_readonly: bool = False,
    max_workers: int | None = None,
    observers: list[WarehouseObserver] | None = None,
    file_materializer: FileSourceMaterializer | None = None,
    file_materializer_factory: Callable[[], FileSourceMaterializer] | None = None,
    extra_routes: Mapping[str, Backend] | None = None,
) -> ExecEnv:
    """Build an ExecEnv with the standard backends.

    This is the single canonical way to create an exec env.
    All entry points should use this instead of hand-wiring adapters.

    Args:
        project: The dbt charts project supplying source config. When it is a
            ``FilesystemProject``, DuckDBBackend/SqliteBackend also root
            relative paths against ``project.root``; any other project gets
            ``data_dir=None``, which is fine as long as no source references a
            relative file path.
        read_only: Whether SQL adapters should be read-only (default True).
        profile_type: SQL dialect (default "duckdb").
        target: dbt target for every ``dbt_profile`` source that names none
            (``--target``). None: ``$DBT_TARGET``, then the profile's default.
        duckdb_config: Optional DuckDB config dict passed to the default
            connection. When read_only=True (the default) and
            allow_external_access_in_readonly=False, enable_external_access is
            hard-forced to False for caller SQL regardless of what this dict
            contains (see LOCAL_AUTHORING_ENV_KWARGS above). To
            combine read_only=True with external file access (read_csv, httpfs,
            etc.) on every connection, set both allow_external_access_in_readonly=True
            AND duckdb_config={"enable_external_access": True}. The default path
            (read_only=False) also allows external access without the flag.
        resolver: Source resolver instance. Defaults to DefaultSourceResolver when
            None. Pass an AllowlistedSourceResolver for deployed-mode enforcement.
        allow_external_access_in_readonly: Security opt-in; passed through to
            DuckDBBackend. Default False. Approved callsites passing True are
            listed on LOCAL_AUTHORING_ENV_KWARGS in this module.
        max_workers: Width of the dbt backend's per-source connection pool (warm
            warehouse worker threads). None falls back to the execution-config
            default.
        file_materializer: Pre-built materializer for CSV/JSON/Parquet file
            sources, shared with its owner.
        file_materializer_factory: Lazy builder for the file-source
            materializer, built on first use. When neither argument is given,
            the env lazily builds the local in-process materializer, so a
            fully-cached render never opens one.
        extra_routes: Caller-supplied backends by source kind, replacing the
            default route of any kind they name. The env owns them:
            ``close_env`` closes each.
    """
    from dbt_charts.core.execute.backends.duckdb_local import DuckDBBackend
    from dbt_charts.core.execute.backends.sqlite import SqliteBackend
    from dbt_charts.core.execute.file_source_materializer import (
        resolve_local_file_materializer_factory,
    )

    # Relative DuckDB/SQLite paths root against the project directory — only a
    # FilesystemProject has one. Any other project passes data_dir=None; the backends tolerate that
    # (no relative-path resolution needed until a source actually references one).
    data_dir = project.root if isinstance(project, FilesystemProject) else None
    resolved_dbt_path = linked_dbt_path(project)

    return make_exec_env(
        project=project,
        project_sources=project.sources,
        resolver=resolver,
        observers=observers,
        file_materializer=file_materializer,
        file_materializer_factory=(
            file_materializer_factory
            if file_materializer_factory is not None
            else resolve_local_file_materializer_factory(project, file_materializer)
        ),
        backends=BackendRegistry(
            {
                **warehouse_routes(max_workers),
                "duckdb": DuckDBBackend(
                    data_dir=data_dir,
                    read_only=read_only,
                    duckdb_config=duckdb_config,
                    allow_external_access_in_readonly=allow_external_access_in_readonly,
                ),
                "sqlite": SqliteBackend(data_dir=data_dir),
                **(extra_routes if extra_routes is not None else {}),
            }
        ),
        profile_type=profile_type,
        dbt_context=(
            DbtContext(dbt_project_path=resolved_dbt_path.resolve(), target=target)
            if resolved_dbt_path is not None
            else None
        ),
        # When the execute path opts into external file access under read-only,
        # cold schema introspection must open DuckDB with the same config.
        schema_introspection_duckdb_config=(
            duckdb_config if read_only and allow_external_access_in_readonly else None
        ),
    )


@dataclass
class MaterializerHolder:
    """The file-source materializer of one env, injected or built lazily.

    An injected materializer is closed by whoever owns it, so ``owned`` stays False and ``close`` leaves it
    alone. One built from ``factory`` on first use is ours, and ``close`` closes
    it. Both ``None`` leaves file-source queries refused.
    """

    materializer: FileSourceMaterializer | None = None
    factory: Callable[[], FileSourceMaterializer] | None = None
    owned: bool = field(default=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def get(self) -> FileSourceMaterializer | None:
        """The materializer, built at most once under a lock so concurrent render
        workers share a single instance."""
        if self.materializer is not None or self.factory is None:
            return self.materializer
        with self._lock:
            if self.materializer is None:
                self.materializer = self.factory()
                self.owned = True
        return self.materializer

    def close(self) -> None:
        if self.owned and self.materializer is not None:
            self.materializer.close()


@dataclass(eq=False)  # identity-hashed: serve's lease counter keys on the env itself
class ExecEnv:
    """What a project's queries run against: its sources, backends and file-source
    materializer. Plain state; ``make_exec_env`` fills the defaults and
    ``close_env`` releases it.

    SQL never picks its route: plan resolves a concrete source, and
    ``BackendRegistry`` maps that source's kind to a backend.

    Attributes:
        project: The dbt charts project; file reads go through its seam methods.
        sources: The project's source configuration plus synthetic registrations.
        resolver: Names the concrete source a query runs on.
        dbt_refs: Resolves ``ref()`` / ``source()`` against the project's manifest.
        backends: The routes of the sources that run on a ``Backend``.
        materializer: The file-source materializer, injected or built lazily.
        observers: Called after each query with (warehouse_type, status,
            duration_seconds).
        profile_type: The warehouse dialect reported when no dbt project
            names one (see ``detect_database_type_from_env``).
        dbt_context: The linked dbt project (its path and target), None when the
            project has none. Sources resolve against it; it routes nothing.
        schema_introspection_duckdb_config: DuckDB config that cold schema
            introspection must mirror so its connection matches the execute
            connection. DuckDB rejects a second connection to the same file
            opened with a different config.
        release_thread: Called by the engine-compile worker thread when it is done
            reading the project, so a host can release what the thread holds.
            Query-pool threads never call it.

    Example:
        >>> from dbt_charts.core.execute.env import build_exec_env
        >>> from dbt_charts.core.execute.run import run_query
        >>> from dbt_charts.cli.filesystem_project import FilesystemProject
        >>> env = build_exec_env(FilesystemProject(Path(".")))
        >>> result = run_query(env, SqlQuery(sql="SELECT 1", source="my_db"))
    """

    project: Project
    sources: SourceRegistry
    resolver: SourceResolver
    dbt_refs: DbtRefResolver
    backends: BackendRegistry
    materializer: MaterializerHolder
    observers: list[WarehouseObserver]
    profile_type: str | None
    dbt_context: DbtContext | None
    schema_introspection_duckdb_config: DuckDBConfig | None
    release_thread: Callable[[], None]


def _holds_nothing_per_thread() -> None:
    """The default ``release_thread``: a host with no per-thread resources has none to release."""


def make_exec_env(
    project: Project,
    project_sources: ProjectSourcesConfig = _EMPTY_PROJECT_SOURCES,
    resolver: SourceResolver | None = None,
    observers: list[WarehouseObserver] | None = None,
    file_materializer: FileSourceMaterializer | None = None,
    file_materializer_factory: Callable[[], FileSourceMaterializer] | None = None,
    backends: BackendRegistry | None = None,
    profile_type: str | None = None,
    dbt_context: DbtContext | None = None,
    schema_introspection_duckdb_config: DuckDBConfig | None = None,
    release_thread: Callable[[], None] = _holds_nothing_per_thread,
) -> ExecEnv:
    """An ``ExecEnv`` with the defaults filled in.

    *release_thread* is called on a worker thread that has read the project, once it is done:
    a host that keeps a resource per thread (a database connection) releases it there.

    Use ``build_exec_env`` to get one wired with the standard backends. Here
    ``backends=None`` routes nothing, so a warehouse source is refused by kind, and
    ``resolver=None`` is ``DefaultSourceResolver``. Both ``file_materializer`` and
    ``file_materializer_factory`` ``None`` leaves file-source queries refused.
    """
    return ExecEnv(
        project=project,
        sources=SourceRegistry(project_sources=project_sources),
        resolver=resolver if resolver is not None else DefaultSourceResolver(project),
        dbt_refs=DbtRefResolver(project),
        backends=backends if backends is not None else BackendRegistry({}),
        materializer=MaterializerHolder(file_materializer, file_materializer_factory),
        observers=list(observers) if observers else [],
        profile_type=profile_type,
        dbt_context=dbt_context,
        schema_introspection_duckdb_config=schema_introspection_duckdb_config,
        release_thread=release_thread,
    )


def close_env(env: ExecEnv) -> None:
    """Close the backends, which hold the connections, and the file-source
    materializer the env built itself via ``file_materializer_factory``, if any
    (its cache backend may hold a DuckDB handle). An injected materializer is
    never closed here: whoever built it owns it."""
    env.backends.close()
    env.materializer.close()
