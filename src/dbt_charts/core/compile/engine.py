"""The Python host of the Rust compile: a project, read through `Project`, handed to
`_engine.compile` with the sqlglot analyzer behind its SQL seam."""

import json
from datetime import date
from typing import TypedDict

from dbt_charts import _engine
from dbt_charts.core.compile import sql_analysis
from dbt_charts.core.compile.config import (
    get_default_theme_name,
    html_policy_pin,
)
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.sql_analysis import Registry, Wire
from dbt_charts.core.diagnostics.execution import ExecutionError

# tach-ignore(compile->project: the host adapter reads a Project and compiles one of its files)
from dbt_charts.core.project import Project, ProjectPath


class _Files:
    """`_engine.compile`'s project reader, over a `Project`."""

    def __init__(self, project: Project) -> None:
        self.project = project

    def read(self, path: str) -> str | None:
        try:
            return self.project.read_text(path)
        except (OSError, UnicodeDecodeError, ExecutionError):
            return None

    def list(self, dir: str) -> list[str]:
        root = dir or "."  # type-state: silent_fallback — the engine names the root ""
        return list(self.project.iter_files(root, recursive=False))

    def exists(self, path: str) -> bool:
        return self.project.exists(path)


class _Analyzer:
    def __init__(self, registry: Registry) -> None:
        self.registry = registry

    def analyze_all(self, requests: list[Wire]) -> list[Wire]:
        return sql_analysis.analyze_all(requests, self.registry)


def engine_compile(path: ProjectPath, named: bool = False) -> _engine.CompiledBoard:
    """*path*'s board compiled in Rust, against the project's declared sources.

    *named* is whether the user named the file (a markdown body with a chart fence is then a board).

    Raises `_engine.CompileError`, its `.diagnostics` the coded findings.
    """
    project = path.project
    declared = project.declared_sources
    registry = declared.sources
    return _engine.compile(
        _Files(project),
        path.relpath,
        boards_root=".",
        today=date.today().isoformat(),
        default_theme=get_default_theme_name(),
        html_policy_ceiling=html_policy_pin(),
        sources={
            name: json.dumps(declaration) for name, declaration in registry.items()
        },
        implicit_source=declared.implicit_source,
        sql=_Analyzer(registry),
        named=named,
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
            _Files(project),
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
