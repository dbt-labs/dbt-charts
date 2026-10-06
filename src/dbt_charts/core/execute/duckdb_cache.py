"""DuckDB caching layer for persistent query result storage.

Stage: EXECUTE (Cache)
Purpose: Persist query results in DuckDB for fast board loads
and cross-database queries.

Cache Key: (source_hash, query_hash, variables_hash)
  - source_hash  — stable hash of the data source identity
  - query_hash   — SHA-256 of the SQL/query definition
  - variables_hash — SHA-256 of the variable values that affect the result

board_slug and query_name are METADATA only, not key components. This lets two
boards with identical SQL against the same source share one cache entry
(cross-board deduplication).

Dependencies:
    - duckdb (optional at module load; required at TrivialDuckDBCache() construction)

See also:
    - dbt_charts/core/execute/cache_backend.py — QueryResultCache Protocol
    - dbt_charts/core/execute/trivial_local_cache.py — TrivialDuckDBCache (replace-on-write)
"""

import hashlib
import json
from typing import Any

from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.query.normalized import AnyQuery, is_sql_query
from dbt_charts.core.compile.models.source import SourceConfig
from dbt_charts.core.execute.cache_keys import source_identity

# ─────────────────────────────────────────────────────────────────────────────
# Hash helpers (public — imported by executor.py)
# ─────────────────────────────────────────────────────────────────────────────


def compute_query_hash(sql: str) -> str:
    """Compute hash of query SQL."""
    return hashlib.sha256(sql.encode()).hexdigest()[:16]


def compute_variables_hash(variables: dict[str, Any]) -> str:
    """Compute hash of variable values (only the relevant ones for a query)."""
    if not variables:
        return "0" * 16
    serialized = json.dumps(sorted(variables.items()), default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def compute_source_hash(
    source: str | dict[str, Any] | SourceConfig | None,
    board_sources: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Compute a stable hash for a data source identity.

    The hash distinguishes connections with different identities (host,
    user, project, keyfile, ...) so two users pointing the same source
    name at different credentials never share a cache entry. Secrets
    (password, token, api_key, ...) are stripped before hashing — see
    `cache_keys.SECRET_FIELDS`.

    Args:
        source: Source identifier. May be:
          - a connection string / name (str) like "warehouse_prod"
          - a source config dict with arbitrary keys (inline YAML)
          - None → treated as the default/in-process source
        board_sources: Optional resolved-source map (`board.sources`) used
          to expand a string name into its identity-bearing config dict.
          When omitted or the name is absent, falls back to hashing the
          name string as-is.

    Returns:
        16-char hex string that is stable across calls for the same input.
    """
    # Resolve named string references through the project sources map so
    # different users with the same source name but different credentials
    # produce different hashes.
    if isinstance(source, str) and board_sources and source in board_sources:
        source = board_sources[source]

    identity = source_identity(source)

    if identity is None:
        # No explicit source — default adapter (in-memory DuckDB, CSV, etc.)
        canonical = "__default__"
    elif isinstance(identity, str):
        canonical = identity.strip()
    else:
        # Dict source config: sort keys for stability
        canonical = json.dumps(sorted(identity.items()), default=str, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def compute_relevant_variables(
    query: AnyQuery, variables: VariableValues | None
) -> dict[str, Any]:
    """Filter variables to the subset a query's cache key actually depends on.

    The single filter both `compute_cache_key` hashes and callers that need the
    filtered dict itself (e.g. cache-row audit metadata) reuse.
    """
    if variables and query.variable_dependencies:
        return {k: v for k, v in variables.items() if k in query.variable_dependencies}
    return {}


def compute_cache_key(
    query: AnyQuery,
    variables: dict[str, Any] | None = None,
    board_sources: dict[str, dict[str, Any]] | None = None,
    source_version: str = "",
) -> tuple[str, str, str]:
    """The content-addressed cache key ``(source_hash, query_hash, variables_hash)``.

    Single source of truth for query cache identity — every Executor cache read and
    write derives its key here. Keyed by content, never by name:

    - SQL queries hash ``sql`` (plus ``setup_sql``) and the source's identity;
    - other queries hash ``source_description`` against the default source.

    Variables are filtered to the query's declared dependencies before hashing, so
    a change to an unrelated variable does not bust the entry.

    Args:
        query: The normalized query whose cache identity to compute.
        variables: Full variable values; filtered to ``query.variable_dependencies``.
        board_sources: Project source map (``board.sources``) used to expand a named
            source into its identity-bearing config. ``None`` for sources without a
            board context (e.g. registered-view queries).
        source_version: A content-version token folded into the source identity.
            For file sources (CSV/JSON/Parquet) the caller passes the combined
            ``Project.file_version`` of the source's files — without it the source
            hash covers only the config (paths), and an edited data file would
            serve stale rows. Empty string (the default) for non-file sources whose
            identity is fully captured by the config.
    """
    variables_hash = compute_variables_hash(
        compute_relevant_variables(query, variables)
    )
    if is_sql_query(query):
        content = query.sql
        if query.setup_sql:
            content += "\n" + query.setup_sql
        # Fold only when incremental is active (a watermark column name) so a
        # non-incremental query — the common case — hashes exactly as it did
        # before this feature existed. Folding unconditionally would bust
        # every existing cache entry on upgrade. Inactive contributes nothing
        # to the content, so a column literally named "None" can't collide
        # with the inactive state either.
        if isinstance(query.incremental, str):
            content += f"\n__incremental:{query.incremental}"
        # Folded only when set so existing entries keep their keys.
        if query.dialect is not None:
            content += f"\n__dialect:{query.dialect}"
        source_hash = compute_source_hash(query.source, board_sources=board_sources)
        if source_version:
            source_hash = compute_query_hash(f"{source_hash}:{source_version}")
        return (
            source_hash,
            compute_query_hash(content),
            variables_hash,
        )
    return (
        compute_source_hash(None),
        compute_query_hash(query.source_description),
        variables_hash,
    )
