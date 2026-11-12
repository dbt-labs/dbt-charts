"""The Python host of the Rust compile: a project, read through `Project`, handed to
`_engine.compile` with the sqlglot analyzer behind its SQL seam."""

from __future__ import annotations

import json
from datetime import date
from typing import TypedDict

from dbt_charts import _engine
from dbt_charts.core.compile import sql_analysis
from dbt_charts.core.compile.config import (
    ProjectSourcesConfig,
    RawSources,
    get_default_theme_name,
    get_project_cache_root,
    html_policy_pin,
)
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.cache import CachePatch
from dbt_charts.core.compile.models.source import source_cache_layer
from dbt_charts.core.compile.sql_analysis import Registry, Wire

# tach-ignore(compile->project: the host adapter reads a Project and compiles one of its files)
from dbt_charts.core.project import Project, ProjectPath


def _raise_unreadable(path: str, error: OSError) -> None:
    raise error


class _Files:
    """`_engine.compile`'s project reader, over a `Project`. A file in `overrides` is served from
    there instead (an unsaved editor buffer).

    `read` answers `None` only for a file that is not there; any other failure (permissions, I/O,
    text that is not UTF-8) raises, and the engine refuses with `ERR-PROJECT-FILE-UNREADABLE`.
    """

    def __init__(self, project: Project, overrides: dict[str, str]) -> None:
        self.project = project
        self.overrides = overrides

    def read(self, path: str) -> str | None:
        if path in self.overrides:
            return self.overrides[path]
        try:
            return self.project.read_text(path)
        except FileNotFoundError:
            return None

    def list(self, dir: str) -> list[str]:
        root = dir or "."  # type-state: silent_fallback — the engine names the root ""
        return list(
            self.project.iter_files(root, recursive=False, onerror=_raise_unreadable)
        )

    def exists(self, path: str) -> bool:
        return self.project.exists(path)


class _Analyzer:
    def __init__(self, registry: Registry) -> None:
        self.registry = registry

    def analyze_all(self, requests: list[Wire]) -> list[Wire]:
        return sql_analysis.analyze_all(requests, self.registry)


def cache_wire(layer: CachePatch | None) -> _engine.CacheLayer:
    """A cache layer as the engine reads it: what the scope sets, `None` where it sets nothing."""
    if layer is None:
        return (None, None, None)
    return (layer.enabled, layer.ttl, layer.prefetch)


def engine_compile(
    path: ProjectPath | None,
    named: bool = False,
    content: str | None = None,
    project: Project | None = None,
    synthetic_sources: RawSources | None = None,
    sources: ProjectSourcesConfig | None = None,
) -> _engine.CompiledBoard:
    """*path*'s board compiled in Rust, against the project's declared sources and cache layers.

    The project's `cache:` block is the root of the cache cascade and each source's `cache:` its
    next scope; the engine resolves each query's policy through them.

    *named* is whether the user named the file (a markdown body with a chart fence is then a board).

    *content* is the board's text when the caller holds one the project does not (an unsaved
    editor buffer): the engine reads it for *path* and everything else from the project. `None`
    means no override: the board is read from the project.

    *path* `None` is a board with no project location (as `BoardFile.path` is None): no
    `meta.yml` cascade, no relative reference (a ref is refused as having no project location),
    and no file name in errors. Its text is *content*, and the *project* (whose sources and cache
    layers it compiles against) is then given too, since no path names it. Either missing is a
    `ValueError`; so is a *project* beside a *path*, which names its own.

    *synthetic_sources* are sources registered at run time that no declaration holds (an inspect
    session's `warehouse`); they compile as declared, so the plan carries them. A name the
    project also declares is a `ValueError`.

    *sources* is the registry to compile against when the caller connects to warehouses and holds
    one with its values rendered (an exec env's); `None` compiles against the project's declared
    sources, which hold no rendered value.

    Raises `_engine.CompileError`, its `.diagnostics` the coded findings.
    """
    if path is None:
        if content is None or project is None:
            raise ValueError(
                "a board with no path needs its text (content) and the project (project) "
                "it compiles against"
            )
    elif project is not None:
        raise ValueError("project is for a board with no path; a path names its own")
    else:
        project = path.project
    declared = project.declared_sources if sources is None else sources
    registry = declared.sources
    if synthetic_sources is not None:
        clash = registry.keys() & synthetic_sources.keys()
        if clash:
            raise ValueError(
                f"synthetic sources {sorted(clash)} are also declared by the project"
            )
        registry = {**synthetic_sources, **registry}
    return _engine.compile(
        _Files(
            project, {} if path is None or content is None else {path.relpath: content}
        ),
        None if path is None else path.relpath,
        boards_root=".",
        today=date.today().isoformat(),
        default_theme=get_default_theme_name(),
        html_policy_ceiling=html_policy_pin(),
        sources={
            name: json.dumps(declaration) for name, declaration in registry.items()
        },
        cache_root=cache_wire(get_project_cache_root(project)),
        source_caches={
            name: cache_wire(source_cache_layer(registry, name, name))
            for name in registry
        },
        implicit_source=declared.implicit_source,
        sql=_Analyzer(registry),
        named=named,
        text=content if path is None else None,
    )


class QueryFact(TypedDict):
    name: str
    sql: str | None


class ChartFact(TypedDict):
    id: str
    #: Empty when the chart has no `title:`.
    title: str
    query: str | None


class EngineIndex(TypedDict):
    """`CompiledBoard.index_json()`: the wire shape of the engine's board index."""

    version: int
    public_ids: dict[str, str]
    declared_below: list[str]
    title: str | None
    notes: str | None
    tags: list[str]
    queries: list[QueryFact]
    charts: list[ChartFact]
    #: Every file the compile read, the board's own among them.
    files: list[str]


_INDEX_VERSION = 1


def engine_index(path: ProjectPath) -> EngineIndex | None:
    """The board's index, or None when the engine does not read it: a markdown board
    (the engine reads YAML), a board that does not fold, or a project whose
    `dbt_charts.yml` sources do not load (listing and search report that file's
    error themselves and still name every board).

    The index stops before the SQL seam, the style and the lints, so a board whose SQL
    does not parse is indexed; `engine_compile` is what refuses it.
    """
    if path.is_markdown:
        return None
    project = path.project
    try:
        declared = project.declared_sources
    except CompilationError:
        return None
    try:
        raw = _engine.index(
            _Files(project, {}),
            path.relpath,
            boards_root=".",
            today=date.today().isoformat(),
            sources=list(declared.sources),
            implicit_source=declared.implicit_source,
        )
    except _engine.CompileError:
        return None
    index: EngineIndex = json.loads(raw)
    if index["version"] != _INDEX_VERSION:
        raise ValueError(
            f"engine index version {index['version']} is not {_INDEX_VERSION}"
        )
    return index
