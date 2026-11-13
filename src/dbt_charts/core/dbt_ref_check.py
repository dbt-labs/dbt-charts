"""Extract and validate dbt ref()/source() calls from a board's host-facts queries."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from dbt_charts.core.compile.host_facts import HostQuery, sql_texts
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
from dbt_charts.core.execute.dbt_jinja import REF_CALL_RE, SOURCE_CALL_RE
from dbt_charts.core.execute.dbt_utils import source_target_path

if TYPE_CHECKING:
    from dbt_charts.core.project import Project


@dataclass
class QueryRefCalls:
    """dbt macro calls extracted from a single query's SQL."""

    refs: list[str] = field(default_factory=list)
    sources: list[tuple[str, str]] = field(default_factory=list)


def extract_ref_calls(queries: Mapping[str, HostQuery]) -> dict[str, QueryRefCalls]:
    """Scan only SQL queries for ref()/source() calls.

    Returns only queries that have at least one ref or source call.
    Non-SQL query types are skipped.
    """
    out: dict[str, QueryRefCalls] = {}
    for name, query in sql_texts(queries).items():
        refs = REF_CALL_RE.findall(query["sql"])
        sources = [(src, tbl) for src, tbl in SOURCE_CALL_RE.findall(query["sql"])]
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


SourceYaml = Mapping[
    str, Mapping[str, object]
]  # type-state: object_annotation — unparsed source YAML


def named_source_target_path(
    source: str | None,
    board_sources: SourceYaml,
    project_sources: SourceYaml,
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
    queries: Mapping[str, HostQuery],
    project: Project,
    query_names: Iterable[str],
) -> dict[str, str | None]:
    """Each SQL query's authored manifest directory, by its named source.

    A board declares no sources of its own, so only the project's are read.
    """
    wanted = set(query_names)
    return {
        name: named_source_target_path(
            query["source"], {}, project.declared_sources.sources
        )
        for name, query in sql_texts(queries).items()
        if name in wanted
    }


def check_manifest_refs(
    queries: Mapping[str, HostQuery], project: Project
) -> list[Diagnostic]:
    """Validate ref()/source() calls against the dbt manifest.

    Each query is checked against the manifest its source's ``target_path``
    names (dbt's default location when it names none).

    Empty when no queries have dbt calls. Warns once per manifest path that
    queries need but is not found.
    """
    calls = extract_ref_calls(queries)
    if not calls:
        return []

    target_paths = query_target_paths(queries, project, calls)
    found: list[Diagnostic] = []
    for target_path in dict.fromkeys(target_paths.values()):
        found += _check_manifest(
            project,
            target_path,
            {n: c for n, c in calls.items() if target_paths[n] == target_path},
        )
    return found


def _check_manifest(
    project: Project,
    target_path: str | None,
    calls: dict[str, QueryRefCalls],
) -> list[Diagnostic]:
    try:
        loaded = load_manifest(project, target_path)
    except ExecutionError as exc:
        return [exc.to_diagnostic()]

    if loaded is None:
        kind = _manifest_warn_kind(calls)
        paths = manifest_relpath(project, target_path)
        return [
            Diagnostic.from_code(
                WARN_DBT_MANIFEST_MISSING,
                message=WARN_DBT_MANIFEST_MISSING.message_template.format(
                    kind=kind, paths=paths
                ),
                fix=WARN_DBT_MANIFEST_MISSING.fix_template,
            )
        ]

    index = ref_index(loaded)
    found: list[Diagnostic] = []
    for qcalls in calls.values():
        for ref_name in qcalls.refs:
            if ref_name not in index.refs:
                found.append(
                    DbtChartsError.from_code(
                        ERR_DBT_REF_UNKNOWN_NODE,
                        ref_name=ref_name,
                        available=index.available_refs,
                        why=index.why(ref_name),
                    ).to_diagnostic()
                )
        for src, tbl in qcalls.sources:
            if (src, tbl) not in index.sources:
                found.append(
                    DbtChartsError.from_code(
                        ERR_DBT_SOURCE_UNKNOWN_TABLE,
                        source_name=src,
                        table_name=tbl,
                        available=index.available_sources,
                        why=index.why(f"{src}.{tbl}"),
                    ).to_diagnostic()
                )
    return found
