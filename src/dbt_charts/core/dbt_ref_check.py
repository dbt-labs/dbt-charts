"""Extract and validate dbt ref()/source() calls from a compiled result."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from dbt_charts.core.compile.models.query.normalized import is_sql_query
from dbt_charts.core.compile.models.source import parse_source_config
from dbt_charts.core.dbt_manifest import (
    load_manifest,
    manifest_relpath,
    ref_index,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_DBT_REF_UNKNOWN_NODE,
    ERR_DBT_SOURCE_UNKNOWN_TABLE,
    WARN_DBT_MANIFEST_MISSING,
)
from dbt_charts.core.diagnostics.diagnostic import Diagnostic
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.adapters.dbt_utils import source_target_path
from dbt_charts.core.execute.dbt_jinja import REF_CALL_RE, SOURCE_CALL_RE

if TYPE_CHECKING:
    from dbt_charts.core.compile.compiler import CompileResult
    from dbt_charts.core.project import Project


@dataclass
class QueryRefCalls:
    """dbt macro calls extracted from a single query's SQL."""

    refs: list[str] = field(default_factory=list)
    sources: list[tuple[str, str]] = field(default_factory=list)


def extract_ref_calls(compile_result: CompileResult) -> dict[str, QueryRefCalls]:
    """Scan only SqlQuery instances for ref()/source() calls.

    Returns only queries that have at least one ref or source call.
    Non-SQL query types are skipped.
    """
    out: dict[str, QueryRefCalls] = {}
    for name, query in compile_result.query_registry.items():
        if not is_sql_query(query):
            continue
        refs = REF_CALL_RE.findall(query.sql)
        sources = [(src, tbl) for src, tbl in SOURCE_CALL_RE.findall(query.sql)]
        if refs or sources:
            out[name] = QueryRefCalls(refs=list(refs), sources=sources)
    return out


def _manifest_warn_kind(calls: dict[str, QueryRefCalls]) -> str:
    has_refs = any(c.refs for c in calls.values())
    has_sources = any(c.sources for c in calls.values())
    if has_refs and has_sources:
        return "ref()/source()"
    if has_sources:
        return "source()"
    return "ref()"


_SourceYaml = Mapping[
    str, Mapping[str, object]
]  # type-state: object_annotation — unparsed source YAML


def named_source_target_path(
    source: str | None,
    board_sources: _SourceYaml,
    project_sources: _SourceYaml,
) -> str | None:
    """The manifest directory authored on the dbt_profile source a query names.

    Board ``sources:`` shadow the project's, as at execution, and the source is
    parsed (``env_var()`` rendered) as execution parses it, so a static check
    and the run read the same manifest.
    """
    if source is None:
        return None
    authored = board_sources.get(source) or project_sources.get(source)
    if authored is None or authored.get("type") != "dbt_profile":
        return None
    try:
        return source_target_path(parse_source_config(dict(authored)))
    except ValueError:
        # A malformed source is reported where it is used; here it only means
        # no target_path to honor.
        return None


def query_target_paths(
    compile_result: CompileResult, project: Project, query_names: Iterable[str]
) -> dict[str, str | None]:
    """Each SQL query's authored manifest directory, by its named source."""
    board_sources = compile_result.board.sources if compile_result.board else {}
    wanted = set(query_names)
    return {
        name: named_source_target_path(
            query.source, board_sources, project.sources.sources
        )
        for name, query in compile_result.query_registry.items()
        if name in wanted and is_sql_query(query)
    }


def check_manifest_refs(compile_result: CompileResult, project: Project) -> None:
    """Validate ref()/source() calls against the dbt manifest.

    Each query is checked against the manifest its source's ``target_path``
    names (dbt's default location when it names none).

    Mutates compile_result.errors and compile_result.warnings directly.
    No-ops when no queries have dbt calls. Warns once per manifest path that
    queries need but is not found.
    """
    calls = extract_ref_calls(compile_result)
    if not calls:
        return

    target_paths = query_target_paths(compile_result, project, calls)
    for target_path in dict.fromkeys(target_paths.values()):
        _check_manifest(
            compile_result,
            project,
            target_path,
            {n: c for n, c in calls.items() if target_paths[n] == target_path},
        )


def _check_manifest(
    compile_result: CompileResult,
    project: Project,
    target_path: str | None,
    calls: dict[str, QueryRefCalls],
) -> None:
    try:
        loaded = load_manifest(project, target_path)
    except ExecutionError as exc:
        compile_result.errors.append(exc.to_diagnostic())
        return

    if loaded is None:
        kind = _manifest_warn_kind(calls)
        paths = manifest_relpath(project, target_path)
        compile_result.warnings.append(
            Diagnostic.from_code(
                WARN_DBT_MANIFEST_MISSING,
                message=WARN_DBT_MANIFEST_MISSING.message_template.format(
                    kind=kind, paths=paths
                ),
                fix=WARN_DBT_MANIFEST_MISSING.fix_template,
            )
        )
        return

    index = ref_index(loaded)
    for _qname, qcalls in calls.items():
        for ref_name in qcalls.refs:
            if ref_name not in index.refs:
                compile_result.errors.append(
                    DbtChartsError.from_code(
                        ERR_DBT_REF_UNKNOWN_NODE,
                        ref_name=ref_name,
                        available=index.available_refs,
                    ).to_diagnostic()
                )
        for src, tbl in qcalls.sources:
            if (src, tbl) not in index.sources:
                compile_result.errors.append(
                    DbtChartsError.from_code(
                        ERR_DBT_SOURCE_UNKNOWN_TABLE,
                        source_name=src,
                        table_name=tbl,
                        available=index.available_sources,
                    ).to_diagnostic()
                )
