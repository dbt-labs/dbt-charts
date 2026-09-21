"""Session that owns project-scoped resources for dbt charts composition roots."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, cast, overload

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self

# Verb-forwarder imports use SUBMODULES so test monkeypatches on the
# module attribute affect the call site.
from dbt_charts.agent_api import (
    board_artifact as _board_artifact,
    describe as _describe,
    describe_query as _describe_query,
    migrate as _migrate,
    query as _query,
    search as _search,
    validate as _validate,
)
from dbt_charts.agent_api.board_artifact import EmitBoardArtifactResult
from dbt_charts.agent_api.migrate import MigrateSummary
from dbt_charts.agent_api.validate import (
    ValidateResult,
)
from dbt_charts.core import board as _core_board
from dbt_charts.core.compile.compiler import CompileResult
from dbt_charts.core.compile.config import (
    ProjectSourcesConfig,
    get_export_config,
)
from dbt_charts.core.execute.adapters.adapter_registry import (
    AdapterRegistry,
    build_adapter_registry,
)
from dbt_charts.core.execute.cache_backend import QueryResultCache
from dbt_charts.core.execute.file_source_materializer import (
    resolve_local_file_materializer_factory,
)
from dbt_charts.core.inspect.query_validator import (
    QueryDiagnostic,
    validate_query as _core_validate_query,
)
from dbt_charts.core.project import BoardFile, Project
from dbt_charts.core.project_roots import find_project_root
from dbt_charts.core.render.board_links import LinkContext
from dbt_charts.core.render.svg_cache import RenderedSvgCache, svg_cache_scope

if TYPE_CHECKING:
    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.board import BoardRenderResult
    from dbt_charts.core.execute.file_source_materializer import FileSourceMaterializer
    from dbt_charts.core.execute.source_resolver import SourceResolver
    from dbt_charts.core.inspect.query_validator import RelationshipContext
    from dbt_charts.core.render_format import RenderFormat

# Probe at module load time — single find_spec call per process.
_SUPER_SCHEMA_AVAILABLE: bool = (
    importlib.util.find_spec("dbt_charts_super_schema") is not None
)


@dataclass(init=False)
class ProjectSession:
    """Explicit container for project-scoped resources.

    Composition roots (CLI, MCP, dct serve, Cloud, A lIe, Playground) construct
    one ProjectSession at their documented boundary and pass it (or its component
    resources) into core. Verb methods on ProjectSession are thin forwarders to
    agent_api functions — they unpack self.project so callers don't re-thread
    it at every call site.
    """

    project: Project
    cache: QueryResultCache | None
    _owns_registry: bool
    _lazy_built: bool
    _read_only: bool
    _dialect: str
    _duckdb_config: dict[str, Any] | None
    _allow_external_access_in_readonly: bool
    _resolver: SourceResolver | None
    _file_materializer: FileSourceMaterializer | None

    def __init__(
        self,
        project: Project,
        cache: QueryResultCache | None = None,
        adapter_registry: AdapterRegistry | None = None,
        owns_registry: bool = False,
        read_only: bool = True,
        file_materializer: FileSourceMaterializer | None = None,
    ) -> None:
        self.project = project
        self.cache = cache
        self._owns_registry = adapter_registry is None or owns_registry
        self._lazy_built = adapter_registry is None
        if adapter_registry is not None:
            # cached_property stores in __dict__; pre-populating makes the descriptor
            # short-circuit the build on first access.
            self.__dict__["adapter_registry"] = adapter_registry
        self._read_only = read_only
        self._dialect = "duckdb"
        self._duckdb_config = None
        self._allow_external_access_in_readonly = False
        self._resolver = None
        self._file_materializer = file_materializer

    @classmethod
    def open(
        cls,
        project_dir: Path | str,
        *,
        cache: QueryResultCache | None = None,
        read_only: bool = True,
        dialect: str = "duckdb",
        duckdb_config: dict[str, Any] | None = None,
        allow_external_access_in_readonly: bool = False,
        resolver: SourceResolver | None = None,
    ) -> Self:
        """Construct a ProjectSession rooted at *project_dir*.

        Cache lifecycle belongs to the caller: pass ``cache=open_cache(path)``
        (or another cache backend) when caching is desired, otherwise omit and the
        project will run uncached. ``close()`` does not touch the cache — whoever
        opened it closes it. A caller deriving the cache from project config via
        ``project_cache_ctx(project)`` already holds a project — use
        ``from_project`` instead, or this builds a second one.

        The adapter registry is built lazily on first access to ``adapter_registry``.
        Call ``refresh()`` to close the current registry and force a rebuild on the
        next access.
        """
        from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415

        session = cls(
            project=FilesystemProject(Path(project_dir).resolve()),
            cache=cache,
            read_only=read_only,
        )
        session._dialect = dialect
        session._duckdb_config = duckdb_config
        session._allow_external_access_in_readonly = allow_external_access_in_readonly
        session._resolver = resolver
        return session

    @classmethod
    def from_project(
        cls,
        project: Project,
        *,
        cache: QueryResultCache | None = None,
        adapter_registry: AdapterRegistry | None = None,
        owns_registry: bool = False,
        read_only: bool = True,
        duckdb_config: dict[str, Any] | None = None,
        allow_external_access_in_readonly: bool = False,
        file_materializer: FileSourceMaterializer | None = None,
    ) -> Self:
        """Construct a ProjectSession from a pre-built Project.

        Use this when the caller already holds a ``project: Project`` and does
        not want to re-resolve the path.  The caller owns the Project lifecycle;
        this method stores it directly without re-wrapping.

        Pass ``adapter_registry`` to inject a pre-built registry (e.g. from a
        host-specific factory like ``build_cloud_adapter_registry``). When
        provided, the session uses it directly and does not call
        ``build_adapter_registry`` on first access. By default the caller
        retains ownership (``close()``/``__exit__`` leave it open) — pass
        ``owns_registry=True`` when handing off a throwaway registry built just
        for this session, so ``close()`` releases its connections. Never set
        this for a registry the caller (or another session) reuses afterward —
        e.g. a warm registry shared across requests must stay caller-owned.
        """
        session = cls(
            project=project,
            cache=cache,
            adapter_registry=adapter_registry,
            owns_registry=owns_registry,
            read_only=read_only,
            file_materializer=file_materializer,
        )
        session._duckdb_config = duckdb_config
        session._allow_external_access_in_readonly = allow_external_access_in_readonly
        return session

    @classmethod
    def from_board(
        cls,
        board_file: Path | str,
        *,
        cache: QueryResultCache | None = None,
    ) -> Self:
        """Open a ProjectSession rooted at the project directory discovered upward from board_file."""
        resolved = Path(board_file).resolve()
        project_root = find_project_root(resolved.parent, boundary=None)
        return cls.open(project_root, cache=cache)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @cached_property
    def adapter_registry(self) -> AdapterRegistry:
        """Lazily build the registry on first access; cached thereafter.

        Cleared and rebuilt by refresh() only when lazily built (no registry
        was injected at construction). Closed by close() iff we own it
        (owns_registry — see from_project()). When constructed with an
        injected registry, returns it directly.
        """
        # This lazy-build path only runs when no adapter_registry was injected
        # at construction: ProjectSession.open() (always a FilesystemProject)
        # and hosts that always inject their own registry (Cloud, via
        # from_project(adapter_registry=...)) never reach here. But
        # from_project() can also be called WITHOUT an injected registry
        # against a non-filesystem Project (e.g. rendering a query-less board
        # through an in-memory Project in tests) — that's a supported path:
        # build_adapter_registry takes a base Project and only special-cases
        # FilesystemProject internally (data_dir / dbt-sibling detection fall
        # back to None for any other host), so no data file is ever
        # materialized via a non-filesystem Project's disk.
        #
        # Threading file_materializer through here is what makes dct query /
        # MCP execute_query / query_board work against a file source the same
        # way a local render already does.
        return build_adapter_registry(
            self.project,
            read_only=self._read_only,
            profile_type=self._dialect,
            duckdb_config=self._duckdb_config,
            allow_external_access_in_readonly=self._allow_external_access_in_readonly,
            resolver=self._resolver,
            file_materializer=self._file_materializer,
            file_materializer_factory=resolve_local_file_materializer_factory(
                self.project, self._file_materializer
            ),
        )

    def refresh(self) -> None:
        """Policy-free rebuild primitive.

        For projects opened via ``open()`` (no injected registry): closes the
        current registry (if built) and clears it so the next access rebuilds
        from disk. Also invalidates the sources and warnings_ignore caches so
        the next access re-reads disk.

        For projects constructed with an injected ``adapter_registry`` — whether
        or not the session owns it via ``owns_registry`` — skips the registry
        rebuild (build arguments are not available and a caller-injected
        registry may carry policy, like Cloud's SSRF-guarded resolver, that a
        generic rebuild cannot reconstruct); still invalidates the config
        caches.
        """
        if self._lazy_built and "adapter_registry" in self.__dict__:
            self.adapter_registry.close()
            del self.__dict__["adapter_registry"]
        self.__dict__.pop("_relationship_context", None)
        # Invalidate Project's cached_property caches in place. Reconstructing
        # via `self.project = Project(self.project.root)` would type-erase any
        # subclass — notably CloudManagedProject, whose `sources` overrides
        # cached_property to re-fetch from the Django ORM — silently defeating
        # Cloud-managed sources on every refresh-driven path. Popping the
        # cached keys preserves the instance (and its class) while forcing the
        # next access to re-run each property body.
        for key in ("sources", "warnings_ignore"):
            vars(self.project).pop(key, None)

    def close(self) -> None:
        """Close the adapter registry iff we own it. The cache is the caller's to close."""
        if self._owns_registry and "adapter_registry" in self.__dict__:
            self.adapter_registry.close()
            del self.__dict__["adapter_registry"]

    # Read-only views: project lifecycle owns these; external writers go through refresh().
    @property
    def sources(self) -> ProjectSourcesConfig:
        return self.project.sources

    @property
    def warnings_ignore(self) -> frozenset[str]:
        return self.project.warnings_ignore

    @property
    def charts_dir(self) -> Path:
        # ProjectSession is FS-only-meaningful here: `.open()` always builds a
        # FilesystemProject, and this property's only caller
        # (ai/context.py's resolve_dashboard_path) is a CLI/MCP-only path.
        # Same reasoning as the adapter_registry cast above.
        return cast("FilesystemProject", self.project).charts_dir

    @cached_property
    def _relationship_context(self) -> RelationshipContext | None:
        """Load relationship context from the super-schema cache (lazy, per-instance).

        Returns None when dbt_charts_super_schema is not installed or the cache
        has no relationship data.
        """
        if not _SUPER_SCHEMA_AVAILABLE:
            return None
        from dbt_charts_super_schema.inspect.relationship_context import (  # noqa: PLC0415
            load_relationship_context,
        )

        return load_relationship_context(self.project)

    # ── Verb forwarders ──────────────────────────────────────────────────────

    def validate_paths(
        self, paths: list[Path] | None, *, warehouse: bool = False
    ) -> list[ValidateResult]:
        """Validate boards; ``warehouse=True`` adds the per-adapter query check.

        The warehouse pass is strictly additive — the schema, cross-reference,
        and data-alias tiers run either way, so turning the flag on can only
        add findings, never hide them.
        """
        results = _validate.validate_paths(
            paths,
            project=self.project,
            adapter_registry=self.adapter_registry if warehouse else None,
        )
        return _validate.annotate_with_data_lint(results, project=self.project)

    def migrate_paths(
        self, paths: list[PurePosixPath] | None, *, dry_run: bool
    ) -> MigrateSummary:
        """Rewrite supported retired YAML syntax, capped at the latest released
        version, stamping an informational _schema_version alongside any real
        change. A file already at that version is left untouched, stamp
        included, regardless of its existing stamp's state."""
        return _migrate.migrate_paths(
            paths,
            project=self.project,
            dry_run=dry_run,
        )

    @overload
    def validate_query(
        self,
        sql: str,
        *,
        dialect: str | None = ...,
        suppress: set[str] | None = ...,
        return_suppressed: Literal[False] = ...,
    ) -> list[QueryDiagnostic]: ...

    @overload
    def validate_query(
        self,
        sql: str,
        *,
        dialect: str | None = ...,
        suppress: set[str] | None = ...,
        return_suppressed: Literal[True],
    ) -> tuple[list[QueryDiagnostic], list[QueryDiagnostic]]: ...

    def validate_query(
        self,
        sql: str,
        *,
        dialect: str | None = None,
        suppress: set[str] | None = None,
        return_suppressed: bool = False,
    ) -> list[QueryDiagnostic] | tuple[list[QueryDiagnostic], list[QueryDiagnostic]]:
        """Run SQL validation with relationship context for calibrated severity.

        Loads relationship context from the project's super-schema cache (when
        dbt_charts_super_schema is installed) and forwards it to core, so
        WARN-FANOUT-RISK diagnostics reflect known join multiplicities.

        Stateless — requires no adapter or warehouse connection.
        """
        if return_suppressed:
            return _core_validate_query(
                sql,
                dialect=dialect,
                suppress=suppress,
                relationship_context=self._relationship_context,
                return_suppressed=True,
            )
        return _core_validate_query(
            sql,
            dialect=dialect,
            suppress=suppress,
            relationship_context=self._relationship_context,
        )

    def describe_paths(self, paths: list[Path]) -> list[_describe.DescribeBoardResult]:
        return _describe.describe_paths(paths, project=self.project)

    def search_boards(self, query: str, limit: int = 10) -> _search.SearchResult:
        return _search.search_boards(query, project=self.project, limit=limit)

    def lookup_board_query_sql(
        self,
        name: str,
        path: Path,
        vars: dict[str, Any] | None = None,
    ) -> _query.BoardQueryLookupResult:
        return _query.lookup_board_query_sql(
            name=name, path=path, project=self.project, vars=vars
        )

    def query_board(
        self,
        name: str,
        path: Path,
        vars: dict[str, Any] | None = None,
        limit: int = 20,
    ) -> _query.QueryBoardResult:
        return _query.query_board(
            name,
            path,
            project=self.project,
            vars=vars,
            limit=limit,
            adapter_registry=self.adapter_registry,
        )

    def execute_query(
        self,
        sql: str,
        variables: dict[str, Any] | None = None,
        source: str | None = None,
        limit: int = 50,
        lenient_variables: bool = False,
    ) -> _query.ExecuteQueryResult:
        return _query.execute_query(
            sql,
            variables=variables,
            source=source,
            limit=limit,
            adapter_registry=self.adapter_registry,
            lenient_variables=lenient_variables,
        )

    def describe_query(
        self,
        sql: str,
        source: str | None = None,
        dialect: str | None = None,
    ) -> _describe_query.DescribeQueryResult:
        return _describe_query.describe_query(
            sql,
            source=source,
            dialect=dialect,
            adapter_registry=self.adapter_registry,
        )

    def emit_board(
        self,
        path: Path,
        artifact_path: Path,
        recording_path: Path,
        *,
        variables: dict[str, Any] | None = None,
        use_cache: bool = True,
    ) -> EmitBoardArtifactResult:
        """Compile, resolve, and execute a board; write its board artifact + recording."""
        return _board_artifact.emit_board_artifact(
            path,
            artifact_path,
            recording_path,
            project=self.project,
            adapter_registry=self.adapter_registry,
            variables=variables,
            use_cache=use_cache,
            result_cache=self.cache,
        )

    def render_board(
        self,
        board: BoardFile | None = None,
        *,
        chart: str | None = None,
        variables: dict[str, Any] | None = None,
        format: RenderFormat = "json",
        use_cache: bool = True,
        as_link: bool = False,
        server_port: int | None = None,
        scale: float | None = None,
        ignore_codes: set[str] | None = None,
        max_workers: int | None = None,
        link_context: LinkContext | None = None,
        standalone: bool = False,
        svg_cache: RenderedSvgCache | None = None,
        compile_result: CompileResult | None = None,
        **render_options: Any,
    ) -> BoardRenderResult:
        """Compile (or reuse) *board* and render it.

        Pass ``compile_result`` when the caller has already compiled *board*
        -- e.g. to authorize a yaml_content render's touched sources before
        executing it -- so this call renders from that SAME result instead
        of compiling again. A second, independent compile is not a redundant
        safety net: it can be anchored differently (a pathless compile has a
        different relative-ref base than a located one) and so can genuinely
        disagree with the first, which makes "compile once to authorize,
        compile again to execute" an attacker-controllable gap, not just
        wasted work. Pass ``board`` alongside it regardless -- it still
        supplies error-file stamping, dir-navigation variables, and the
        preview URL (see ``core.board.render_dashboard``'s own docstring).
        """
        # When the caller does not supply a link_context, derive origin from the
        # project's public_url config so dct render exports carry fully-qualified links.
        if link_context is None:
            public_url = get_export_config(self.project).public_url
            if public_url:
                link_context = LinkContext(origin=public_url)
        # Per board render, not per session: a host wires one to its store for
        # the board it is about to draw and drains it afterwards. Bound around
        # the call rather than threaded through it — the memo lives at the
        # vl-convert boundary a dozen frames below, and every signature in
        # between is about layout, not caching.
        with svg_cache_scope(svg_cache):
            return _core_board.render_dashboard(
                board=board,
                chart=chart,
                variables=variables,
                adapter_registry=self.adapter_registry,
                project=self.project,
                result_cache=self.cache,
                format=format,
                use_cache=use_cache,
                as_link=as_link,
                server_port=server_port,
                scale=scale,
                ignore_codes=ignore_codes,
                max_workers=max_workers,
                link_context=link_context,
                standalone=standalone,
                file_materializer=self._file_materializer,
                compile_result=compile_result,
                **render_options,
            )
