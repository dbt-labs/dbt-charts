"""Shared dbt utilities for SQL adapters."""

import re
import threading
from typing import TYPE_CHECKING

from dbt_charts.core.compile.models.source import (
    DbtProfileSourceConfig,
    DbtTargetSourceConfig,
)
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_DBT_CALL_UNSUPPORTED,
    ERR_DBT_MANIFEST_MISSING,
    ERR_DBT_REF_UNKNOWN_NODE,
    ERR_DBT_SOURCE_UNKNOWN_TABLE,
)
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.adapters.base import ResolvedRelation
from dbt_charts.core.execute.dbt_jinja import (
    REF_CALL_RE,
    SOURCE_CALL_RE,
    dbt_macro_kind,
    first_dbt_call,
    has_dbt_jinja,
)

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.source import ResolvedSourceConfig
    from dbt_charts.core.dbt_manifest import RefIndex
    from dbt_charts.core.project import Project

DBT_PROJECT_DB_NAMES = ["sample.duckdb", "dbt_charts_examples.duckdb", "dev.duckdb"]


def source_target_path(source_config: "ResolvedSourceConfig | None") -> str | None:
    """The manifest directory a dbt_profile source authored, before or after expansion."""
    if isinstance(source_config, DbtProfileSourceConfig | DbtTargetSourceConfig):
        return source_config.target_path
    return None


def resolve_dbt_refs_with_provenance(
    sql: str,
    index: "RefIndex",
) -> tuple[str, list[ResolvedRelation]]:
    """Resolve ref/source in SQL and record the resolved relation per call."""
    relations: list[ResolvedRelation] = []

    def resolve_ref(match: re.Match[str]) -> str:
        ref_name = match.group(1)
        entry = index.refs.get(ref_name)
        if entry is None:
            raise ExecutionError.from_code(
                ERR_DBT_REF_UNKNOWN_NODE,
                ref_name=ref_name,
                available=index.available_refs,
            )
        fragment, schema = entry
        relations.append(ResolvedRelation(ref_name=ref_name, schema=schema))
        return fragment

    sql = REF_CALL_RE.sub(resolve_ref, sql)

    def resolve_source(match: re.Match[str]) -> str:
        source_name = match.group(1)
        table_name = match.group(2)
        entry = index.sources.get((source_name, table_name))
        if entry is None:
            raise ExecutionError.from_code(
                ERR_DBT_SOURCE_UNKNOWN_TABLE,
                source_name=source_name,
                table_name=table_name,
                available=index.available_sources,
            )
        fragment, schema = entry
        ref_name = f"{source_name}.{table_name}"
        relations.append(ResolvedRelation(ref_name=ref_name, schema=schema))
        return fragment

    sql = SOURCE_CALL_RE.sub(resolve_source, sql)

    # Detection routes on the `{{ ref(` prefix; substitution has to parse a whole
    # call. A prefix matcher always accepts more than a parser, so check the
    # output rather than trust the patterns to have covered every spelling: a
    # call still standing here is one this engine cannot resolve.
    if has_dbt_jinja(sql):
        raise ExecutionError.from_code(
            ERR_DBT_CALL_UNSUPPORTED, call=first_dbt_call(sql)
        )

    return sql, relations


class DbtRefResolver:
    """Resolves `{{ ref() }}` / `{{ source() }}` against a project's dbt manifest.

    Call `resolve()` before rendering variable Jinja over raw SQL: the variable
    renderer runs under StrictUndefined, so a `ref()` that survives to it dies
    as an undefined Jinja global — an error that names neither dbt nor the
    manifest. Every adapter (`DbtAdapter`, `SqlAdapter`, `DuckDBAdapter`,
    `SqliteAdapter`) owns one for its own `_execute`/`prepare_sql`;
    `AdapterRegistry` owns one too, for the board-render composition step in
    `_compose_query_refs` that runs ahead of (and independently from) any
    adapter's own resolve-then-render.

    ``resolve(sql, target_path)`` selects the manifest, with one index built
    lazily per ``target_path``: a source's authored one picks the manifest its
    target built, none picks dbt's default location.

    The manifest is the gate. SQL without dbt Jinja is returned untouched and
    reads no files; SQL with it and no manifest to resolve against raises
    ERR-DBT-MANIFEST-MISSING rather than passing the call downstream.
    """

    def __init__(self, project: "Project | None") -> None:
        """Initialize the resolver.

        Args:
            project: Seam the manifests are read through. None for an adapter
                built without a project (Cloud's dev-only DuckDB registration,
                a bare adapter in a test) — ref()/source() then raises, since
                there is nothing to resolve it against.
        """
        self._project = project
        # Keyed by the authored target_path (None = dbt's default location);
        # an index of None marks a manifest that does not exist.
        self._indexes: dict[str | None, tuple[str, RefIndex | None]] = {}
        # One resolver is shared across the executor's worker threads; the
        # lock makes each first load atomic so a concurrent resolve() never
        # sees a path marked loaded with its index still unbuilt.
        self._load_lock = threading.Lock()

    def resolve(
        self, sql: str, target_path: str | None = None
    ) -> tuple[str, list[ResolvedRelation]]:
        """Return `sql` with refs replaced by relations, plus their lineage.

        Raises:
            ExecutionError: ERR-DBT-MANIFEST-MISSING when the SQL calls ref() or
                source() and no manifest is available;
                ERR-DBT-REF-UNKNOWN-NODE / ERR-DBT-SOURCE-UNKNOWN-TABLE when a
                manifest is available but does not contain the node named;
                ERR-DBT-CALL-UNSUPPORTED when the call is in a form this engine
                cannot resolve to a single relation. No ref() or source() ever
                leaves this method unresolved.
        """
        if not has_dbt_jinja(sql):
            return sql, []

        relpath, index = self._index_for(target_path)
        if index is None:
            raise ExecutionError.from_code(
                ERR_DBT_MANIFEST_MISSING,
                kind=dbt_macro_kind(sql),
                paths=[relpath],
            )
        return resolve_dbt_refs_with_provenance(sql, index)

    def _index_for(self, target_path: str | None) -> tuple[str, "RefIndex | None"]:
        """The manifest path and its ref/source index, built once per target_path."""
        from dbt_charts.core.dbt_manifest import (  # noqa: PLC0415
            load_manifest,
            manifest_relpath,
            ref_index,
        )

        with self._load_lock:
            if target_path not in self._indexes:
                project = self._project
                relpath = manifest_relpath(project, target_path)
                loaded = (
                    load_manifest(project, target_path) if project is not None else None
                )
                self._indexes[target_path] = (
                    relpath,
                    ref_index(loaded) if loaded is not None else None,
                )
            return self._indexes[target_path]
