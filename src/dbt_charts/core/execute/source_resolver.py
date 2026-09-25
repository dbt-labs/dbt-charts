"""Source resolver: execute-boundary resolution of authored `source:` values.

The `SourceResolver` Protocol runs in `AdapterRegistry.execute()` before the
adapter receives the query. When the authored source resolves to a configured
profile (board-level, project-level, or an inline dict), the adapter receives a
typed source config. For source-less queries (values, http, schema),
the resolver returns `None` and the adapter falls through to its own default
connection (DuckDB ``:memory:`` or the dbt-aware lookup).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path  # noqa: TID251 — resolves dbt profiles_dir on disk
from typing import Any, Protocol

from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.source import (
    VALID_SOURCE_TYPES,
    BaseSourceConfig,
    DbtProfileSourceConfig,
    DbtTargetSourceConfig,
    ResolvedSourceConfig,
    SourceConfig,
    parse_source_config,
)
from dbt_charts.core.diagnostics.base import DbtChartsError

# ERR_SOURCE_INLINE_FORBIDDEN and ERR_SOURCE_NOT_FOUND are compile-owned
# (dropping the domain segment collided the compile- and execute-side codes;
# compile's message_template won).
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_SOURCE_INLINE_FORBIDDEN,
    ERR_SOURCE_NOT_FOUND,
)
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_NO_DEFAULT_SOURCE,
    ERR_SOURCE_CROSS_FILE_FORBIDDEN,
    ERR_SOURCE_INVALID_TYPE,
    ERR_SOURCE_MISSING_TYPE,
    ERR_SOURCE_NOT_FOUND_EMPTY,
)
from dbt_charts.core.diagnostics.execution import ExecutionError

# ERR-SOURCE-NOT-FOUND and ERR-SOURCE-INLINE-FORBIDDEN's message_template
# requires query_name — most SourceResolver callers execute a named
# dashboard/view query and pass the real name, but a few (schema
# introspection, bulk profiling, ad-hoc `execute_query`) have none to give.
AD_HOC_QUERY_NAME = "ad-hoc query"


@dataclass
class DbtContext:
    """Carries dbt project context for source resolution.

    When present and no `sources:` are configured at all, an unknown string
    source name defers (returns None) rather than raising — the query then
    falls through to whichever adapter claims a source-less SQL query:
    DbtAdapter for dbt-jinja (`ref()`/`source()`, resolved via the manifest),
    or DuckDBAdapter otherwise, which auto-discovers the dbt project's own
    local dev warehouse file under its `data/` directory (see
    ``DuckDBAdapter.__init__``'s `dbt_project_path` parameter), falling back
    to `:memory:` when discovery finds nothing there either. When any
    `sources:` are configured, or when this is absent entirely, unknown names
    always raise instead.

    dbt_project_path is populated by AdapterRegistry._derive_dbt_context() from
    the registered DbtAdapter. It is used by DefaultSourceResolver to expand
    dbt_profile sources into their concrete warehouse SourceConfig before routing,
    so the registry re-route sees the real type (e.g. "duckdb" → DuckDBAdapter).
    """

    dbt_project_path: Path | None = None


class SourceResolver(Protocol):
    """Single execute-boundary chokepoint for source resolution.

    Implementations map an authored `source:` value (str | dict | None) to a
    typed `ResolvedSourceConfig | None` before any database driver is loaded.

    Callers (AdapterRegistry) invoke resolve() once per query, then forward the
    result to the matched adapter via a `source_config` kwarg.
    """

    def resolve(
        self,
        authored: str | dict[str, Any] | SourceConfig | None,
        board_sources: dict[str, dict[str, Any]],
        project_sources: ProjectSourcesConfig,
        dbt_context: DbtContext | None,
        query_name: str = AD_HOC_QUERY_NAME,
    ) -> ResolvedSourceConfig | None:
        """Resolve an authored source value to a typed SourceConfig.

        Args:
            authored: The raw `source:` value from the compiled query, or a
                pre-coerced SourceConfig instance (model-layer validation may
                have already run parse_source_config on a dict input).
            board_sources: Named sources declared in the board's `sources:` block.
            project_sources: Project-level sources from dbt_charts.yml.
            dbt_context: Present when a dbt project is in scope; when no
                `sources:` are configured at all, allows an unknown string
                name to fall through to whichever adapter claims a
                source-less query.
            query_name: Author-facing name of the query being resolved, for
                ERR-SOURCE-NOT-FOUND / ERR-SOURCE-INLINE-FORBIDDEN messages.
                Callers outside a named dashboard query (schema introspection,
                bulk profiling, ad-hoc `dct describe`/`execute_query`) have no
                name to give — the default documents that explicitly instead
                of guessing one.

        Returns:
            Typed source config, or None when no source applies (adapter uses
            its own default connection) or when dbt_context defers the name.

        Raises:
            DbtChartsError: With a ERR-SOURCE-* code on policy violations.
        """
        ...


class DefaultSourceResolver:
    """Behavior-preserving default resolver for CLI, local playground, and inspect surfaces.

    Resolves authored source values using board-level sources first, then project-level
    sources. Does not apply policy gates — inline dicts are accepted as-is. An unknown
    string name falls through to the dbt adapter only when no `sources:` are configured
    at all; once any are, an unmatched name is always a mistake and raises.

    Resolution order for a string authored value:
      1. board_sources lookup (board's `sources:` block)
      2. project_sources lookup (dbt_charts.yml `sources:`)
      3. raise ERR-SOURCE-NOT-FOUND when any project sources are configured
         (`project_sources.sources`) but the name matches neither lookup above
      4. dbt fallback: return None when dbt_context is set (no project sources
         configured at all) — the query then falls through to whichever
         adapter claims a source-less query (DbtAdapter for dbt-jinja SQL,
         DuckDBAdapter otherwise, which auto-discovers the dbt project's own
         local dev warehouse file under `data/`, else `:memory:`)
      5. raise ERR-SOURCE-NOT-FOUND-EMPTY otherwise

    For None authored: return None (sourceless query types — values, http, schema).
    For dict authored: validate and parse directly.

    Subclasses (e.g. an allowlist-enforcing resolver) override resolve(), apply
    their own policy gates, and delegate to super().resolve() for the shared
    lookup algorithm.
    """

    def resolve(
        self,
        authored: str | dict[str, Any] | SourceConfig | None,
        board_sources: dict[str, dict[str, Any]],
        project_sources: ProjectSourcesConfig,
        dbt_context: DbtContext | None,
        query_name: str = AD_HOC_QUERY_NAME,
    ) -> ResolvedSourceConfig | None:
        if authored is None:
            # No project-level default exists; board/meta `source:` inheritance
            # injects the default at compile time. Sourceless query types
            # (values, http, schema) reach here legitimately → None.
            return None

        if isinstance(authored, BaseSourceConfig):
            if isinstance(authored, DbtProfileSourceConfig):
                return self._expand_dbt_profile(authored, dbt_context)
            return authored

        if isinstance(authored, dict):
            parsed = self._parse(authored)
            if isinstance(parsed, DbtProfileSourceConfig):
                return self._expand_dbt_profile(parsed, dbt_context)
            return parsed

        source_dict = board_sources.get(authored) or project_sources.sources.get(
            authored
        )
        if source_dict is not None:
            parsed = self._parse(source_dict)
            if isinstance(parsed, DbtProfileSourceConfig):
                return self._expand_dbt_profile(parsed, dbt_context)
            return parsed

        # A configured `sources:` block names every source that's actually
        # valid — an unmatched name is a mistake (typo, generic guess) and
        # must raise, never fall through to the dbt-context deferral below.
        available = sorted(project_sources.sources.keys())
        if available:
            raise DbtChartsError.from_code(
                ERR_SOURCE_NOT_FOUND,
                query_name=query_name,
                source=authored,
                available=available,
            )

        if dbt_context is not None:
            return None

        raise DbtChartsError.from_code(
            ERR_SOURCE_NOT_FOUND_EMPTY,
            source=authored,
        )

    def _parse(self, source_dict: dict[str, Any]) -> SourceConfig:
        """Validate and parse a source dict to a typed SourceConfig."""
        if "type" not in source_dict:
            raise DbtChartsError.from_code(
                ERR_SOURCE_MISSING_TYPE,
                offending_value=repr(source_dict),
            )
        if source_dict["type"] not in VALID_SOURCE_TYPES:
            raise DbtChartsError.from_code(
                ERR_SOURCE_INVALID_TYPE,
                offending_value=source_dict["type"],
                available=sorted(VALID_SOURCE_TYPES),
            )
        return parse_source_config(source_dict)

    def _expand_dbt_profile(
        self,
        cfg: DbtProfileSourceConfig,
        dbt_context: DbtContext | None,
    ) -> ResolvedSourceConfig:
        """Expand a DbtProfileSourceConfig into a concrete warehouse SourceConfig.

        Reads profiles.yml using the canonical resolution order (profiles_dir →
        DBT_PROFILES_DIR → project root → ~/.dbt) and hands it to dbt, which
        validates the target and resolves its concrete warehouse type.

        The result is wrapped in DbtTargetSourceConfig, not parsed into an
        authored model like DuckDBSourceConfig: dbt owns profiles.yml, so
        re-validating its output against dbt charts' closed models is what rejected
        valid dbt config. The registry still re-routes on the concrete type.

        Called at every return point in resolve() when the result is a
        DbtProfileSourceConfig so the registry re-routes on the concrete type.

        Raises:
            ExecutionError: When dbt_project_path is absent, or profiles.yml lookup
                or dbt's own validation fails (FileNotFoundError / ValueError from
                _read_target_dict).
        """
        if dbt_context is None or dbt_context.dbt_project_path is None:
            raise ExecutionError(
                "dbt_profile source requires a dbt project, "
                "but this adapter has no dbt_project_path configured."
            )

        from dbt_charts.core.execute.adapters.dbt_adapter import _read_target_dict

        profiles_dir = (
            (dbt_context.dbt_project_path / cfg.profiles_dir).resolve()
            if cfg.profiles_dir is not None
            else None
        )

        try:
            target_dict = _read_target_dict(
                dbt_context.dbt_project_path,
                cfg.profile,
                cfg.target,
                profiles_dir=profiles_dir,
            )
        except (FileNotFoundError, ValueError) as exc:
            raise ExecutionError(str(exc)) from exc

        # The authored attribution rides the dbt charts source entry, not the dbt
        # target — profiles.yml belongs to dbt and rejects keys it doesn't know — so
        # carry it across the expansion or it is lost for every dbt_profile source.
        return DbtTargetSourceConfig(**target_dict, attribution=cfg.attribution)


def _summarize_inline_source(source_dict: dict[str, Any]) -> str:
    """Summarize a rejected inline source without echoing secret values.

    The rendered error message surfaces via `QueryResult.error` and structured
    logs — echoing the raw dict (which may carry `password`, `secret`, `token`,
    …) compounds the original mistake. Emit `type` plus the sorted key list so
    authors get actionable feedback ("you authored an inline postgres source")
    without the values.
    """
    type_value = source_dict.get("type", "<missing type>")
    keys = sorted(source_dict.keys())
    return f"type={type_value!r}, keys={keys}"


class AllowlistedSourceResolver(DefaultSourceResolver):
    """Strict source resolver: only names in `project_sources.sources` resolve.

    Four rejection rules fire before any resolution:

    0. Source-less queries — `authored is None` is refused with
       `ERR-NO-DEFAULT-SOURCE`. On the trusted local surface a sourceless
       ad-hoc query falls through to the engine's scratch DuckDB
       (`DefaultSourceResolver` returns None); on a hosted/multi-tenant surface
       that fallback is an SSRF / local-file-read vector, so every query must
       name a registered source. This is the trust-boundary decision — it lives
       here, not in a required model field.
    1. Inline source dicts — both per-query `source: {type: ..., host: ...}`
       and any board-level `board.sources` entry are refused with
       `ERR-SOURCE-INLINE-FORBIDDEN` (board.sources values are inline
       source definitions by construction, not name references).
    2. Cross-file `#` references — any authored string containing `#` is
       refused with `ERR-SOURCE-CROSS-FILE-FORBIDDEN`.
    3. Unknown names — a string not in `project_sources.sources` is refused
       with `ERR-SOURCE-NOT-FOUND`; the payload's `available` field
       carries the sorted allowlist.

    Accepted inputs delegate to `DefaultSourceResolver.resolve` for the shared
    lookup. The dbt-context fallback is intentionally disabled: an unknown
    name raises regardless of `dbt_context`, because dbt-profile names are not
    part of the configured allowlist by definition.

    **File sources are ordinary allowlist entries here; the rules above are
    about warehouse sources.** A csv/json/parquet entry names a repo-relative
    path — validated by `CsvSourceConfig`/`JsonSourceConfig`/`ParquetSourceConfig`'s
    own `files:` rules, which reject absolute paths and any path escaping the
    project root — not a host, a port, or a credential. There is no endpoint to
    approve, so a host that resolves file sources from its own repo (Cloud does,
    from the committed `dbt_charts.yml`) puts them in `project_sources` like any
    other name, and rule 1 is untouched: an *authored* source definition is still
    refused whatever its type.

    Two paths then diverge, and only one skips this resolver. `Executor` (the
    render path) short-circuits a file-source query into the
    `FileSourceMaterializer` before resolution ("Step 4c"), so a rendered board
    never asks here. `agent_api`'s `execute_query` and `query_board`, which
    Cloud exposes over MCP and chat, call `AdapterRegistry.execute` directly and
    DO reach this resolver, which returns the file config normally;
    `AdapterRegistry.execute` then refuses it itself, because it holds no
    materializer. That refusal is the one place the "you need the render path"
    answer lives. `describe_query` reaches it too: it routes a file source's
    SQL through `check_ad_hoc_query`, which wraps it in `DESCRIBE (...)` and
    hands it to the same `AdapterRegistry.execute` — the identical
    no-materializer refusal fires there if none is configured. Behavior is no
    more permissive than the execute path; describe just answers with columns
    instead of rows when a materializer *is* present.

    This is the closed-allowlist resolver — pick it when the caller does not
    trust authored YAML to declare arbitrary connections (e.g. hosted /
    multi-tenant deployments). Loosening any rule changes the trust boundary;
    treat changes here as security decisions, not stylistic ones.
    """

    def resolve(
        self,
        authored: str | dict[str, Any] | SourceConfig | None,
        board_sources: dict[str, dict[str, Any]],
        project_sources: ProjectSourcesConfig,
        dbt_context: DbtContext | None,
        query_name: str = AD_HOC_QUERY_NAME,
    ) -> ResolvedSourceConfig | None:
        if authored is None:
            raise DbtChartsError.from_code(
                ERR_NO_DEFAULT_SOURCE,
                available=sorted(project_sources.sources),
            )

        if isinstance(authored, BaseSourceConfig):
            raise DbtChartsError.from_code(
                ERR_SOURCE_INLINE_FORBIDDEN,
                query_name=query_name,
                offending_value=_summarize_inline_source(authored.model_dump()),
            )

        if isinstance(authored, dict):
            raise DbtChartsError.from_code(
                ERR_SOURCE_INLINE_FORBIDDEN,
                query_name=query_name,
                offending_value=_summarize_inline_source(authored),
            )

        # authored is now narrowed to str (None / SourceConfig / dict all raised).
        if "#" in authored:
            raise DbtChartsError.from_code(
                ERR_SOURCE_CROSS_FILE_FORBIDDEN,
                offending_value=authored,
            )

        if authored in board_sources and authored not in project_sources.sources:
            raise DbtChartsError.from_code(
                ERR_SOURCE_INLINE_FORBIDDEN,
                query_name=query_name,
                offending_value=_summarize_inline_source(board_sources[authored]),
            )

        if authored not in project_sources.sources:
            self._raise_not_found(authored, project_sources, query_name)

        return super().resolve(
            authored=authored,
            board_sources={},
            project_sources=project_sources,
            dbt_context=None,
            query_name=query_name,
        )

    @staticmethod
    def _raise_not_found(
        offending: str,
        project_sources: ProjectSourcesConfig,
        query_name: str,
    ) -> None:
        raise DbtChartsError.from_code(
            ERR_SOURCE_NOT_FOUND,
            query_name=query_name,
            source=offending,
            available=sorted(project_sources.sources),
        )
