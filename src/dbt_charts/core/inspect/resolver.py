"""Layered schema resolver — composes SuperSchemaSource + DbtSchemaSource.

The resolver is the one entry point for the ``schema`` verb. Per-target
it picks between cache (warm) and dbt-source (cold), expands wildcards,
assembles the hierarchical named-dict tree, and stamps the response
with a single ``_meta`` footer.

The cascade is **per target**, not global. For every individual table /
column the resolver consults:

    1. ``SuperSchemaSource.profile_table(...)`` — if the cache has it,
       use the rich profile (stats, distributions, semantic types).
    2. ``DbtSchemaSource.profile_table(...)`` — fall through when the
       cache misses, returning the honest cold-start view (column types
       from the adapter; descriptions / declared types / tests when the
       manifest contributed).

This means a partially-populated cache still gets dbt's bare schema for
the unprofiled tables instead of silently dropping them — the level-3 /
level-4 asymmetry the initiative was filed to fix.

``_meta.sources_consulted`` reports exactly which layers actually
contributed to *this* response: ``["super_schema"]`` if everything came
from the cache, ``["dbt_adapter"]`` if everything came from dbt without
a manifest, ``["dbt_adapter", "dbt_manifest"]`` if the manifest also
contributed, and the union when a response mixed cache + dbt for
different tables. ``cache_built_at`` is set whenever ``super_schema`` is
in the list — anchored to the cache file's ``generated_at``.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from dbt_charts.cli.filesystem_project import (
    FilesystemProject,  # tach-ignore(core->cli: host-type guard; needs a non-cli signal on Project — deferred)
)
from dbt_charts.core.dbt_manifest import load_manifest
from dbt_charts.core.diagnostics.base import DbtChartsError

# ERR_SOURCE_NOT_FOUND is compile-owned (dropping the domain segment
# collided the compile- and execute-side codes; compile's message_template
# won).
from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_NOT_FOUND
from dbt_charts.core.diagnostics.codes_execute import ERR_SOURCE_NOT_FOUND_EMPTY
from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter
from dbt_charts.core.execute.source_resolver import AD_HOC_QUERY_NAME
from dbt_charts.core.inspect.sources.dbt import (
    DbtSchemaSource,
    extract_all_relationships,
)
from dbt_charts.core.inspect.sources.sqlite import SQLiteSchemaSource
from dbt_charts.core.project import Project, is_absolute_any_os

if TYPE_CHECKING:
    # SuperSchemaSource lives in the private dbt-charts-super-schema package.
    # Import only for type-checking; at runtime it is injected by the caller.
    from dbt_charts.core.execute.adapters import AdapterRegistry
    from dbt_charts_super_schema.inspect.sources.super_schema import SuperSchemaSource


AdapterFactory = Callable[[dict[str, Any]], Any]
SourceLayer = str  # "super_schema" / "dbt_adapter" / "dbt_manifest"


def _schema_adapter_factory(cfg: dict[str, Any]) -> Any:
    """Open file-backed DuckDB read-only for schema cold paths."""
    return build_adapter(cfg, read_only=True)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _expand_targets(spec: str | None, candidates: list[str]) -> list[str]:
    """Expand a target spec against a list of candidates.

    A spec is one or more alternation items separated by ``,`` or ``|``. Each
    item is either a literal name or an fnmatch glob (``*?[``). Results preserve
    spec order (then candidate order within a glob) and are deduped, so
    ``id|requester*`` and ``ticket,user`` both work the way you'd expect.
    """
    if spec is None or spec == "*":
        return list(candidates)
    out: list[str] = []
    seen: set[str] = set()
    for item in (s.strip() for s in re.split(r"[,|]", spec) if s.strip()):
        if any(ch in item for ch in "*?["):
            matches = [c for c in candidates if fnmatch.fnmatch(c, item)]
        else:
            matches = [item] if item in candidates else []
        for m in matches:
            if m not in seen:
                seen.add(m)
                out.append(m)
    return out


def is_exact_target(spec: str | None) -> bool:
    """True when the spec is a single literal name (no list or glob)."""
    return spec is not None and not any(ch in spec for ch in ",|*?[")


def _table_universe(
    cache_tables: dict[str, dict[str, Any]],
    dbt_tables: dict[str, dict[str, Any]],
) -> list[str]:
    """Return cache-first table names, deduped across cache and dbt."""
    seen: set[str] = set()
    universe: list[str] = []
    for name in cache_tables:
        if name not in seen:
            seen.add(name)
            universe.append(name)
    for name in dbt_tables:
        if name not in seen:
            seen.add(name)
            universe.append(name)
    return universe


class LayeredSchemaResolver:
    """Composes an optional SuperSchemaSource (cache) + DbtSchemaSource (live).

    ``cache`` is optional (default ``None``). When absent the resolver is
    dbt-only: all schema data comes from the dbt adapter + manifest with no
    warm-cache enrichment. OSS installs without ``dbt-charts-super-schema``
    pass no cache; Cloud/IDE installs pass a ``SuperSchemaSource`` instance.
    """

    def __init__(
        self,
        *,
        cache: SuperSchemaSource | None = None,
        adapter_registry: AdapterRegistry,
        project: Project,
        adapter_factory: AdapterFactory | None = None,
    ) -> None:
        self.cache = cache
        self.adapter_registry = adapter_registry
        self.project = project
        self._adapter_factory = adapter_factory or _schema_adapter_factory
        # NOTE: adapter_factory is accepted for callers with custom dispatch
        # (e.g. test fixtures). The default path reads the DuckDB config from
        # the registry at adapter-build time in _dbt_for so all construction
        # sites get the same posture without explicit wiring.
        self._dbt_sources: dict[str, DbtSchemaSource | SQLiteSchemaSource] = {}
        self._source_lookup_cache: dict[str, dict[str, Any]] | None = None
        # Cached manifest-relationship walk. Loaded directly from the
        # ``target/manifest.json`` file so cache-hit short-circuits don't
        # have to build a dbt adapter to surface ``referenced_by`` /
        # ``linked_via``. ``None`` means "haven't checked yet"; an empty
        # list means "checked, no manifest" (tests reuse the cache).
        self._manifest_relationships: list[dict[str, str]] | None = None

    # ---- Public entries -----------------------------------------------------

    def list_schemas(self, source: str) -> dict[str, Any]:
        """Level 2: enumerate schemas in a source.

        Cache contributes when present (carries ``table_count`` summaries);
        dbt contributes the canonical schema list when reachable. The
        result merges cache rows on top of the dbt enumeration. When
        the source name is not configured, ``DbtChartsError``
        (ERR-SOURCE-NOT-FOUND or ERR-SOURCE-NOT-FOUND-EMPTY)
        propagates so the verb wrapper turns it into ``success=False``.
        """
        source_entry = self._source_entry(source)
        used: set[SourceLayer] = set()
        cache_built_at = None

        cache_contrib = self.cache.list_schemas() if self.cache is not None else None
        if cache_contrib is not None:
            used.add("super_schema")
            cache_built_at = self.cache.generated_at  # type: ignore[union-attr]

        dbt = (
            self._try_dbt_for(source)
            if cache_contrib is not None
            else self._dbt_for(source)
        )
        dbt_contrib = dbt.list_schemas() if dbt is not None else None
        if dbt_contrib is not None:
            used.add("dbt_adapter")

        schemas: dict[str, dict[str, Any]] = {}
        if dbt_contrib is not None:
            for name in dbt_contrib["schemas"]:
                schemas.setdefault(name, {})
        if cache_contrib is not None:
            for name, entry in cache_contrib["schemas"].items():
                schemas.setdefault(name, {}).update(entry)

        cache_diagnostic: str | None = (
            cache_contrib.get("diagnostic") if cache_contrib is not None else None
        )
        sources_dict = {source: dict(source_entry)}
        if schemas:
            sources_dict[source]["schemas"] = schemas
        return _build_envelope(
            sources_dict,
            sources_consulted=_ordered(used),
            cache_built_at=cache_built_at if "super_schema" in used else None,
            hints=[cache_diagnostic] if cache_diagnostic else None,
        )

    def list_tables(self, source: str, schema: str) -> dict[str, Any]:
        """Level 3: lean table summaries.

        Per target: try the cache first; fall through to dbt for any
        schema/table the cache doesn't know about. Same shape regardless
        of which layers contributed.
        """
        return self._tabled_walk(
            source=source,
            schema_spec=schema,
            table_spec=None,
            column_spec=None,
            fresh=False,
        )

    def profile_table(
        self,
        source: str,
        schema: str,
        table: str,
        *,
        fresh: bool = False,
        lineage_depth: int = 1,
    ) -> dict[str, Any]:
        """Level 4: full profile.

        Per target the resolver tries cache first, falls through to dbt.
        ``fresh=True`` skips the cache entirely.
        """
        return self._tabled_walk(
            source=source,
            schema_spec=schema,
            table_spec=table,
            column_spec=None,
            fresh=fresh,
            lineage_depth=lineage_depth,
        )

    def profile_column(
        self,
        source: str,
        schema: str,
        table: str,
        column: str,
    ) -> dict[str, Any]:
        return self._tabled_walk(
            source=source,
            schema_spec=schema,
            table_spec=table,
            column_spec=column,
            fresh=False,
        )

    # ---- DbtSchemaSource factory (D5) ---------------------------------------

    def _dbt_for(self, source_name: str) -> DbtSchemaSource | SQLiteSchemaSource:
        """Build (and cache) the schema source for ``source_name``.

        SQLite sources use ``SQLiteSchemaSource`` (stdlib sqlite3, no dbt
        adapter needed). All other sources use ``DbtSchemaSource`` via
        ``build_adapter``.

        Raises ``DbtChartsError`` (ERR-SOURCE-NOT-FOUND or
        ERR-SOURCE-NOT-FOUND-EMPTY) if the registry can't resolve
        the source config, or whatever ``build_adapter`` raises (typically
        ``ImportError`` for a missing dbt-<warehouse> package). Callers
        that need cache-only degradation should call ``_try_dbt_for`` and
        check for ``None``.
        """
        if source_name not in self._dbt_sources:
            cfg = self.adapter_registry.resolve_source_config(source_name)
            if cfg.get("type", "").lower() == "sqlite":
                path = cfg.get("path")
                if not path:
                    raise ValueError(
                        f"SQLite source '{source_name}' is missing the required 'path' field."
                    )
                # Resolve relative paths against project_root — mirrors DuckDB's
                # behavior so ./data/bird.sqlite works from any cwd.
                if is_absolute_any_os(path):
                    resolved_path = path
                else:
                    if not isinstance(self.project, FilesystemProject):
                        raise ValueError(
                            f"SQLite source '{source_name}' has a relative path, "
                            "which requires a local filesystem project; got "
                            f"{type(self.project).__name__}. SQLite sources are "
                            "not supported on non-filesystem hosts."
                        )
                    resolved_path = str(self.project.data_path(path))
                self._dbt_sources[source_name] = SQLiteSchemaSource(path=resolved_path)
            else:
                # Mirror the registry's DuckDB config so cold introspection
                # and execute open the same file with the same config.
                # DuckDB rejects a second connection with a different posture.
                # This covers every LayeredSchemaResolver construction site
                # without requiring each caller to thread factory plumbing.
                # getattr: real AdapterRegistry always has this attribute (set
                # in __init__), but test-stub registries (_FakeRegistry in
                # libs/super-schema/tests) may not — None is the safe default.
                introspection_cfg = getattr(
                    self.adapter_registry,
                    "schema_introspection_duckdb_config",
                    None,
                )
                if (
                    introspection_cfg
                    and self._adapter_factory is _schema_adapter_factory
                ):
                    merged = dict(cfg)
                    merged_duckdb = dict(merged.get("duckdb_config") or {})
                    merged_duckdb.update(introspection_cfg)
                    merged["duckdb_config"] = merged_duckdb
                    adapter = _schema_adapter_factory(merged)
                else:
                    adapter = self._adapter_factory(cfg)
                # Pass db_path so DbtSchemaSource can resolve the correct DuckDB
                # attach name via PRAGMA database_list (needed for file-backed DuckDB
                # where list_relations(None, schema) returns an empty list).
                db_path = (
                    cfg.get("path") if cfg.get("type", "").lower() == "duckdb" else None
                )
                self._dbt_sources[source_name] = DbtSchemaSource(
                    adapter=adapter, project=self.project, db_path=db_path
                )
        return self._dbt_sources[source_name]

    def _try_dbt_for(
        self, source_name: str
    ) -> DbtSchemaSource | SQLiteSchemaSource | None:
        """Like ``_dbt_for`` but returns ``None`` on the build-time
        failure we can recover from: ``ImportError`` when the
        dbt-<dialect> package isn't installed. Runtime warehouse-connection
        errors from ``adapter.list_*`` are *not* caught here — they propagate
        and the verb wrapper turns them into ``success=False`` envelopes.
        """
        try:
            return self._dbt_for(source_name)
        except ImportError:
            return None

    def _all_manifest_relationships(self) -> list[dict[str, str]]:
        """Every forward FK declared in this project's dbt manifest.

        Loaded once per resolver instance, directly from the manifest file —
        no dbt adapter required. This is what
        lets the cache-hit short-circuit attach ``referenced_by`` /
        ``linked_via`` without paying for an adapter build.
        """
        if self._manifest_relationships is None:
            loaded = load_manifest(self.project, optional=True)
            self._manifest_relationships = extract_all_relationships(
                loaded.raw if loaded else None
            )
        return self._manifest_relationships

    # ---- Walkers ------------------------------------------------------------

    def _tabled_walk(
        self,
        source: str,
        schema_spec: str,
        table_spec: str | None,
        column_spec: str | None,
        fresh: bool,
        lineage_depth: int = 1,
    ) -> dict[str, Any]:
        """Walk schemas → tables → optional columns, per-target cache-then-dbt."""
        source_entry = self._source_entry(source)
        if (
            self.cache is not None
            and not fresh
            and table_spec is not None
            and is_exact_target(schema_spec)
            and is_exact_target(table_spec)
        ):
            cached = self.cache.profile_table(schema=schema_spec, table=table_spec)
            if cached is not None:
                cached.setdefault("upstream", [])
                cached.setdefault("downstream", [])
                profile: dict[str, Any] | None = cached
                if column_spec is not None:
                    profile = _filter_to_columns(cached, column_spec)
                if profile is not None:
                    short_used: set[SourceLayer] = {"super_schema"}
                    if _attach_cross_table(
                        profile, table_spec, self._all_manifest_relationships()
                    ):
                        short_used.add("dbt_manifest")
                    sources_dict = {source: dict(source_entry)}
                    sources_dict[source]["schemas"] = {
                        schema_spec: {"tables": {table_spec: profile}}
                    }
                    return _build_envelope(
                        sources_dict,
                        sources_consulted=_ordered(short_used),
                        cache_built_at=self.cache.generated_at,
                    )

        schema_universe = self._enumerate_schemas(source, fresh)
        used: set[SourceLayer] = set()
        schemas_dict: dict[str, Any] = {}
        for sname in _expand_targets(schema_spec, schema_universe):
            schema_entry, schema_used = self._walk_schema(
                source=source,
                schema=sname,
                table_spec=table_spec,
                column_spec=column_spec,
                fresh=fresh,
                lineage_depth=lineage_depth,
            )
            used.update(schema_used)
            if schema_entry:
                schemas_dict[sname] = schema_entry

        cache_built_at = (
            self.cache.generated_at  # type: ignore[union-attr]
            if "super_schema" in used
            else None
        )
        sources_dict = {source: dict(source_entry)}
        if schemas_dict:
            sources_dict[source]["schemas"] = schemas_dict
        return _build_envelope(
            sources_dict,
            sources_consulted=_ordered(used),
            cache_built_at=cache_built_at,
        )

    def _enumerate_schemas(self, source: str, fresh: bool) -> list[str]:
        """Build the universe of candidate schema names for the walk.

        Cache + dbt union — either alone might be incomplete (cache is a
        snapshot; dbt is the live warehouse). When the cache has data we
        treat dbt as best-effort for adapter build errors. When the cache
        is empty, we require dbt and let registry / build errors propagate.
        Schema enumeration only builds candidates; ``sources_consulted`` is
        stamped by the layer that contributes returned table / column leaves.
        """
        names: list[str] = []
        seen: set[str] = set()

        cache_contrib = (
            None if (fresh or self.cache is None) else self.cache.list_schemas()
        )
        if cache_contrib is not None:
            for n in cache_contrib["schemas"]:
                if n not in seen:
                    seen.add(n)
                    names.append(n)

        dbt = (
            self._try_dbt_for(source)
            if cache_contrib is not None
            else self._dbt_for(source)
        )
        dbt_contrib = dbt.list_schemas() if dbt is not None else None
        if dbt_contrib is not None:
            for n in dbt_contrib["schemas"]:
                if n not in seen:
                    seen.add(n)
                    names.append(n)
        return names

    def _walk_schema(
        self,
        source: str,
        schema: str,
        table_spec: str | None,
        column_spec: str | None,
        fresh: bool,
        lineage_depth: int = 1,
    ) -> tuple[dict[str, Any], set[SourceLayer]]:
        """Resolve tables for one schema. Returns (schema_entry, layers_used).

        Level 3 reads each layer's table list once, then dispatches lean
        summaries from those dicts. Level 4 still profiles each matched
        table because dbt has to return per-relation columns.
        """
        cache_tables, dbt_tables, dbt, dbt_has_manifest = self._schema_table_layers(
            source=source, schema=schema, fresh=fresh
        )
        universe = _table_universe(cache_tables, dbt_tables)
        tables_dict, used = self._dispatch_schema_targets(
            schema=schema,
            table_spec=table_spec,
            column_spec=column_spec,
            fresh=fresh,
            universe=universe,
            cache_tables=cache_tables,
            dbt_tables=dbt_tables,
            dbt=dbt,
            dbt_has_manifest=dbt_has_manifest,
            lineage_depth=lineage_depth,
        )

        schema_entry: dict[str, Any] = {}
        if tables_dict:
            schema_entry["tables"] = tables_dict
        return schema_entry, used

    def _schema_table_layers(
        self, source: str, schema: str, fresh: bool
    ) -> tuple[
        dict[str, dict[str, Any]],
        dict[str, dict[str, Any]],
        DbtSchemaSource | SQLiteSchemaSource | None,
        bool,
    ]:
        cache_list = None
        cache_tables_dict: dict[str, dict[str, Any]] = {}
        if not fresh and self.cache is not None:
            cache_list = self.cache.list_tables(schema=schema)
            if cache_list is not None:
                cache_tables_dict = dict(cache_list["tables"])
        dbt = (
            self._try_dbt_for(source)
            if cache_list is not None
            else self._dbt_for(source)
        )
        dbt_tables_dict: dict[str, dict[str, Any]] = {}
        dbt_has_manifest = False
        if dbt is not None:
            dbt_list = dbt.list_tables(schema=schema)
            if dbt_list is not None:
                dbt_tables_dict = dict(dbt_list["tables"])
            dbt_has_manifest = dbt.has_manifest

        return cache_tables_dict, dbt_tables_dict, dbt, dbt_has_manifest

    def _dispatch_schema_targets(
        self,
        schema: str,
        table_spec: str | None,
        column_spec: str | None,
        fresh: bool,
        universe: list[str],
        cache_tables: dict[str, dict[str, Any]],
        dbt_tables: dict[str, dict[str, Any]],
        dbt: DbtSchemaSource | SQLiteSchemaSource | None,
        dbt_has_manifest: bool,
        lineage_depth: int = 1,
    ) -> tuple[dict[str, Any], set[SourceLayer]]:
        used: set[SourceLayer] = set()
        tables_dict: dict[str, Any] = {}
        if table_spec is None:
            for tname in universe:
                summary, layers = self._lean_summary(
                    table=tname,
                    cache_tables=cache_tables,
                    dbt_tables=dbt_tables,
                    dbt_has_manifest=dbt_has_manifest,
                )
                if summary is None:
                    continue
                used.update(layers)
                tables_dict[tname] = summary
        else:
            for tname in _expand_targets(table_spec, universe):
                profile, layers = self._full_profile(
                    schema=schema,
                    table=tname,
                    fresh=fresh,
                    dbt=dbt,
                    dbt_has_manifest=dbt_has_manifest,
                    lineage_depth=lineage_depth,
                )
                if profile is None:
                    continue
                if column_spec is not None:
                    profile = _filter_to_columns(profile, column_spec)
                    if profile is None and layers == {"super_schema"}:
                        profile, layers = self._full_profile(
                            schema=schema,
                            table=tname,
                            fresh=True,
                            dbt=dbt,
                            dbt_has_manifest=dbt_has_manifest,
                            lineage_depth=lineage_depth,
                        )
                        if profile is not None:
                            profile = _filter_to_columns(profile, column_spec)
                    if profile is None:
                        continue
                used.update(layers)
                tables_dict[tname] = profile
        return tables_dict, used

    @staticmethod
    def _lean_summary(
        table: str,
        cache_tables: dict[str, dict[str, Any]],
        dbt_tables: dict[str, dict[str, Any]],
        dbt_has_manifest: bool,
    ) -> tuple[dict[str, Any] | None, set[SourceLayer]]:
        """Pick a level-3 summary from the per-schema lookups, cache first.

        Returns ``(None, set())`` when no layer has the table (cross-cutting
        non-match — caller drops the row from the response).
        """
        cached = cache_tables.get(table)
        if cached is not None:
            return cached, {"super_schema"}
        summary = dbt_tables.get(table)
        if summary is None:
            return None, set()
        layers: set[SourceLayer] = {"dbt_adapter"}
        if dbt_has_manifest and _summary_has_manifest_contribution(summary):
            layers.add("dbt_manifest")
        return summary, layers

    def _full_profile(
        self,
        schema: str,
        table: str,
        fresh: bool,
        dbt: DbtSchemaSource | SQLiteSchemaSource | None,
        dbt_has_manifest: bool,
        lineage_depth: int = 1,
    ) -> tuple[dict[str, Any] | None, set[SourceLayer]]:
        """Return a level-4 profile and the layers used to build it.

        Cache first per target. Falls through to ``dbt.profile_table`` —
        the per-table query is unavoidable for level 4 because the cache
        only stores rich profiles for previously-inspected tables; dbt's
        adapter must still answer per relation.

        After the source returns the profile, we attach manifest-derived
        cross-table fields (``referenced_by``, ``linked_via``) when the
        manifest is available — even on cache hits, since the manifest is
        the authority for declared FKs and the cache may pre-date the
        ``relationships:`` tests.
        """
        layers: set[SourceLayer]
        profile: dict[str, Any] | None
        all_rels = self._all_manifest_relationships()
        if not fresh and self.cache is not None:
            cached = self.cache.profile_table(schema=schema, table=table)
            if cached is not None:
                cached.setdefault("upstream", [])
                cached.setdefault("downstream", [])
                profile, layers = cached, {"super_schema"}
                if _attach_cross_table(profile, table, all_rels):
                    layers.add("dbt_manifest")
                return profile, layers
        if dbt is None:
            return None, set()
        profile = dbt.profile_table(
            schema=schema, table=table, lineage_depth=lineage_depth
        )
        if profile is None:
            return None, set()
        layers = {"dbt_adapter"}
        if dbt_has_manifest:
            _attach_cross_table(profile, table, all_rels)
            if _profile_has_manifest_contribution(profile):
                layers.add("dbt_manifest")
        return profile, layers

    # ---- Source entry helpers ----------------------------------------------

    def _source_entries(self) -> dict[str, dict[str, Any]]:
        if self._source_lookup_cache is None:
            self._source_lookup_cache = {}
            for raw in self.adapter_registry.list_sql_sources():
                name = raw["name"]
                self._source_lookup_cache[name] = {
                    k: v for k, v in raw.items() if k != "name"
                }
        return self._source_lookup_cache

    def _source_entry(self, source: str) -> dict[str, Any]:
        entries = self._source_entries()
        entry = entries.get(source)
        if entry is None:
            available = sorted(entries.keys())
            if available:
                raise DbtChartsError.from_code(
                    ERR_SOURCE_NOT_FOUND,
                    query_name=AD_HOC_QUERY_NAME,
                    source=source,
                    available=available,
                )
            raise DbtChartsError.from_code(ERR_SOURCE_NOT_FOUND_EMPTY, source=source)
        return entry


def _filter_to_columns(
    profile: dict[str, Any], column_spec: str
) -> dict[str, Any] | None:
    """Keep only columns matching ``column_spec``; drop the table if empty."""
    cols = profile.get("columns") or {}
    matched = _expand_targets(column_spec, list(cols))
    if not matched:
        return None
    out = dict(profile)
    out["columns"] = {name: cols[name] for name in matched}
    return out


# Keys a `DbtSchemaSource` only adds when manifest data merged into the
# response. Used to answer "did manifest *actually* contribute to this call"
# vs "is manifest *available*" (the latter is `has_manifest`). The
# distinction matters for `_meta.sources_consulted` honesty: a request for
# a table the warehouse has but the manifest doesn't reports
# `["dbt_adapter"]`, not `["dbt_adapter", "dbt_manifest"]`.
_MANIFEST_TABLE_KEYS = frozenset(
    ("description", "tags", "owner", "referenced_by", "linked_via")
)
# When the only manifest contribution on a profile is a forward FK injected
# onto a column's `relationships` list, table-level keys won't trip the
# manifest-contribution check. `_profile_has_manifest_contribution` covers
# this via `_MANIFEST_COLUMN_KEYS` containing `relationships`.
_MANIFEST_COLUMN_KEYS = frozenset(
    ("description", "declared_type", "tags", "granularity", "tests", "relationships")
)


# M2M cutoff: len(columns) <= 4 AND (cols_with_explicit_FK / len(columns)) >= 0.75
# AND >=2 distinct FK columns. The 4-column ceiling caps "obvious" join tables
# (`order_items(order_id, product_id)` qualifies; a wide fact table does not).
# 75% guarantees enough of the row is FK to be a connector and not a fact
# table that happens to carry two FKs. Both bounds are explicit per the
# initiative's no-magic rule — easier to defend than a percentile or fitted
# threshold.
_M2M_MAX_COLUMNS = 4
_M2M_MIN_FK_RATIO = 0.75


def _attach_cross_table(
    profile: dict[str, Any], table: str, all_rels: list[dict[str, str]]
) -> bool:
    """Attach manifest-derived FK views to ``profile``.

    Three views, all derivations of explicit forward-FK declarations in the
    dbt manifest — D3 forbids any naming-heuristic invention:

      * column-level ``relationships`` — declared forward FKs on this
        table's columns. ``DbtSchemaSource.profile_table`` already adds
        these on the cold path; the cache-hit short-circuit doesn't (the
        cache predates ``relationships:`` tests), so this helper merges
        them on regardless of where the profile came from.
      * table-level ``referenced_by`` — reverse-FK roll-up.
      * table-level ``linked_via`` — M2M two-hop reachability.

    Returns ``True`` when at least one field was attached (the
    ``dbt_manifest`` provenance bookkeeping rides on this).
    """
    if not all_rels:
        return False
    contributed = False
    if _attach_forward_relationships(profile, table, all_rels):
        contributed = True
    referenced_by = _compute_referenced_by(table, all_rels)
    if referenced_by:
        profile["referenced_by"] = referenced_by
        contributed = True
    linked_via = _compute_linked_via(profile, table, all_rels)
    if linked_via:
        profile["linked_via"] = linked_via
        contributed = True
    return contributed


def _attach_forward_relationships(
    profile: dict[str, Any], table: str, all_rels: list[dict[str, str]]
) -> bool:
    """Merge declared forward FKs onto ``profile['columns'][col]['relationships']``.

    Idempotent: if the cold dbt path already populated ``relationships``
    on a column, we don't double-record an entry that's already present.
    Skip columns that aren't on the profile (manifest declares an FK on
    a column the warehouse profile doesn't carry — renamed / dropped).
    """
    cols = profile.get("columns") or {}
    if not cols:
        return False
    contributed = False
    for rel in all_rels:
        if rel["from_table"] != table:
            continue
        col = cols.get(rel["from_column"])
        if col is None:
            continue
        existing = col.get("relationships")
        entry = {"to_table": rel["to_table"], "to_column": rel["to_column"]}
        if existing is None:
            col["relationships"] = [entry]
            contributed = True
        elif entry not in existing:
            existing.append(entry)
            contributed = True
    return contributed


def _compute_referenced_by(
    table: str, all_rels: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Reverse-FK roll-up: every declared FK that targets ``table``."""
    out = [
        {"from_table": rel["from_table"], "from_column": rel["from_column"]}
        for rel in all_rels
        if rel["to_table"] == table
    ]
    out.sort(key=lambda x: (x["from_table"], x["from_column"]))
    return out


def _compute_linked_via(
    profile: dict[str, Any], table: str, all_rels: list[dict[str, str]]
) -> list[dict[str, str]]:
    """M2M two-hop: ordered FK column pairs on ``table`` when it looks like
    a join table by the cutoff documented above."""
    cols = profile.get("columns") or {}
    if len(cols) == 0 or len(cols) > _M2M_MAX_COLUMNS:
        return []

    fk_targets: dict[str, str] = {}  # from_column -> first declared to_table
    for rel in all_rels:
        if rel["from_table"] != table:
            continue
        col = rel["from_column"]
        if col not in cols or col in fk_targets:
            continue
        fk_targets[col] = rel["to_table"]

    if len(fk_targets) < 2:
        return []
    if len(fk_targets) / len(cols) < _M2M_MIN_FK_RATIO:
        return []

    fk_cols = sorted(fk_targets)
    out: list[dict[str, str]] = []
    for i, a in enumerate(fk_cols):
        for b in fk_cols[i + 1 :]:
            out.append(
                {
                    "through_column_a": a,
                    "hop_table_a": fk_targets[a],
                    "through_column_b": b,
                    "hop_table_b": fk_targets[b],
                }
            )
    return out


def _summary_has_manifest_contribution(summary: dict[str, Any]) -> bool:
    return any(k in summary for k in _MANIFEST_TABLE_KEYS)


def _profile_has_manifest_contribution(profile: dict[str, Any]) -> bool:
    if any(k in profile for k in _MANIFEST_TABLE_KEYS):
        return True
    # Non-empty lineage lists mean the manifest contributed edge data.
    if profile.get("upstream") or profile.get("downstream"):
        return True
    cols = profile.get("columns")
    if isinstance(cols, dict):
        for col in cols.values():
            if isinstance(col, dict) and any(k in col for k in _MANIFEST_COLUMN_KEYS):
                return True
    return False


_LAYER_ORDER: tuple[SourceLayer, ...] = ("super_schema", "dbt_adapter", "dbt_manifest")


def _ordered(used: set[SourceLayer]) -> list[SourceLayer]:
    return [layer for layer in _LAYER_ORDER if layer in used]


def _build_envelope(
    sources_dict: dict[str, dict[str, Any]],
    sources_consulted: list[SourceLayer],
    cache_built_at: datetime | None = None,
    hints: list[str] | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "retrieved_at": _now_utc().isoformat(),
        "sources_consulted": sources_consulted,
    }
    if cache_built_at is not None:
        meta["cache_built_at"] = cache_built_at.isoformat()
    result: dict[str, Any] = {"sources": sources_dict, "_meta": meta}
    if hints:
        result["hints"] = hints
    return result
