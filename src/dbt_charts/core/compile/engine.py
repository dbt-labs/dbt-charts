"""The Python host of the Rust compile: a project, read through `Project`, handed to
`_engine.compile` with the sqlglot analyzer behind its SQL seam."""

from __future__ import annotations

import json
from datetime import date
from string import Formatter
from typing import TypedDict

from dbt_charts import _engine
from dbt_charts._docs_site import DEFAULT_DOCS_SITE_URL, docs_site_url
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
from dbt_charts.core.compile.models.source import (
    authored_time_zone,
    parse_source_config,
    source_cache_layer,
)
from dbt_charts.core.compile.sql_analysis import Registry, Wire
from dbt_charts.core.diagnostics import (
    ERR_UNRESOLVED_REFERENCE,
    REGISTRY,
    WARN_RESTATED_FORMAT,
    WARN_RESTATED_INFERRED,
    WARN_RESTATED_STYLE,
    Diagnostic,
    SourceRange,
    WarningCode,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.diagnostic import RelatedLocation
from dbt_charts.core.diagnostics.registry import ErrorCode

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


def _source_zones(declared: ProjectSourcesConfig) -> dict[str, tuple[str, str | None]]:
    """Each typed source's `(type, authored time zone)`, for the engine's time-zone warnings.

    The engine decides which types run a session in the board's zone, so a declaration with a
    `type` is listed. Its zone is read from the parsed source (templates rendered); a source
    that does not parse has none, and is reported where it is used.
    """
    zones: dict[str, tuple[str, str | None]] = {}
    for name, authored in declared.sources.items():
        kind = authored.get("type")
        if not isinstance(kind, str):
            continue
        try:
            zone = authored_time_zone(parse_source_config(dict(authored)))
        except ValueError:
            zone = None
        zones[name] = (kind, zone)
    return zones


def engine_compile(
    path: ProjectPath | None,
    named: bool = False,
    content: str | None = None,
    project: Project | None = None,
    synthetic_sources: RawSources | None = None,
    sources: ProjectSourcesConfig | None = None,
    anchor: str | None = None,
) -> _engine.CompiledBoard:
    """*path*'s board compiled in Rust, against the project's declared sources and cache layers.

    The project's `cache:` block is the root of the cache cascade and each source's `cache:` its
    next scope; the engine resolves each query's policy through them.

    *named* is whether the user named the file (a markdown body with a chart fence is then a board).

    *content* is the board's text when the caller holds one the project does not (an unsaved
    editor buffer): the engine reads it for *path* and everything else from the project. `None`
    means no override: the board is read from the project.

    *path* `None` is a board with no project location (as `BoardFile.path` is None): no
    `meta.yml` cascade and no file name in errors; a relative reference resolves from *anchor*, a
    project directory, and is refused as having no project location when there is none. Its text is *content*, and the *project* (whose sources and cache
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
        zones=_source_zones(declared),
        sql=_Analyzer(registry),
        named=named,
        text=content if path is None else None,
        anchor=anchor if path is None else None,
    )


_RESTATED_CODES = (WARN_RESTATED_STYLE, WARN_RESTATED_INFERRED, WARN_RESTATED_FORMAT)


def wire_diagnostic(wire: _engine.CompileDiagnostic) -> Diagnostic:
    """An engine finding as the `Diagnostic` `ValidateResult` carries.

    The engine ships `code`, `params` and where the finding sits; the registered template
    words it. An error keeps its `params` as `fields`, bar the foreign `detail` text (what
    `DbtChartsError` does); a warning keeps the params its message does not print.
    """
    code = REGISTRY.get(wire["code"])  # raises KeyError for an unregistered code
    params = wire["params"]
    hint = wire.get("hint")
    if hint is not None:
        # The engine reads no environment, so its docs link is the shipped default.
        hint = hint.replace(DEFAULT_DOCS_SITE_URL, docs_site_url())
    elif code.hint_generator is not None:
        hint = code.hint_generator(**params)
    located = {
        "path": wire.get("path"),
        "range": SourceRange.model_validate(wire["range"]) if "range" in wire else None,
        "related": tuple(RelatedLocation.model_validate(r) for r in wire["related"]),
        "hint": hint,
    }
    if isinstance(code, ErrorCode):
        # `detail` is foreign text: it rides `Diagnostic.detail`, never `fields`.
        detail = params.get("detail")
        fields = {k: v for k, v in params.items() if k != "detail"}
        error = DbtChartsError.from_code(code, detail=detail, **fields)
        # A code built with a `field_path` param, not located by the engine, still names it.
        located["path"] = located["path"] or error.field_path or None
        if code is ERR_UNRESOLVED_REFERENCE and "context" in fields:
            # `fields` holds the bare place; only the message's `{context}` carries " in ".
            fields["context"] = fields["context"].removeprefix(" in ")
        return Diagnostic.from_code(
            code,
            message=str(error),
            fix=code.fix_template,
            query=fields.get("query_name"),
            fields=fields,
            detail=detail,
            **located,
        )
    if not isinstance(code, WarningCode):
        raise TypeError(
            f"engine reported a code that is neither error nor warning: {wire['code']}"
        )
    # A warning's `fields` are the params its message does not print, except a restated-key
    # warning, whose `value`, `source` and `why` the CLI prints from them.
    printed = {
        name for _, name, _, _ in Formatter().parse(code.message_template) if name
    }
    restated = code in _RESTATED_CODES
    return Diagnostic.from_code(
        code,
        message=code.message_template.format(**params),
        fix=code.fix_template.format(**params),
        query=params.get("query_name"),
        fields={
            k: v
            for k, v in params.items()
            if (k not in printed or (restated and k != "path"))
        },
        **located,
    )


def refusal_diagnostics(
    refusal: _engine.CompileError, relpath: str | None = None
) -> list[Diagnostic]:
    """The engine's coded refusals as diagnostics (never empty: an uncoded one is a bug),
    located in the board *relpath* when given."""
    if not refusal.diagnostics:
        raise ValueError(f"engine refused without a coded diagnostic: {refusal}")
    found = [wire_diagnostic(wire) for wire in refusal.diagnostics]
    if relpath is None:
        return found
    return [diagnostic.model_copy(update={"file": relpath}) for diagnostic in found]


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
