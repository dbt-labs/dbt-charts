"""Cache key helpers — produce identity payloads for source-aware hashing.

Stage: EXECUTE (Cache)
Purpose: Reduce a source reference to its cache identity, stripping secrets.

Secrets are NEVER included in cache hash inputs. Two users connecting to the
same source name with different credentials must produce different hashes
(so they don't share cache entries when their DB-side row-level access
differs); but a password rotation alone must NOT invalidate the cache.

`source_identity(source)` is the single transformation. The executor calls
it (via `compute_source_hash`) after resolving named source references
through the project's `board.sources` map.

See also:
    - dbt_charts/core/execute/duckdb_cache.py — `compute_source_hash` consumer
"""

from __future__ import annotations

import hashlib
from typing import Any

from dbt_charts.core.compile.models.source import BaseSourceConfig

# Folded into the key the result-cache stores (``stored_query_hash``), never into
# ``compute_cache_key``: recordings are keyed by the latter and must survive a
# bump. Bump it when the cells a query returns change shape for unchanged SQL, so
# every cache holding the old shape (local and Cloud) misses instead of serving it.
RESULT_FORMAT_VERSION = 1


def stored_query_hash(query_hash: str) -> str:
    """The ``query_hash`` a result-cache backend stores and looks up under.

    Re-hashed with ``RESULT_FORMAT_VERSION`` (same 16-hex width), so a bump makes
    every existing entry a miss. Applied inside the cache backends only.
    """
    return hashlib.sha256(f"{query_hash}:{RESULT_FORMAT_VERSION}".encode()).hexdigest()[
        :16
    ]


# Field names that must NEVER appear in cache hash inputs. Match by case-
# insensitive exact name. Conservative: anything that looks secret-shaped
# is dropped. Adding a new auth field elsewhere in the codebase? Add the
# name here.
SECRET_FIELDS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "access_key",
        "access_token",
        "private_key",
        "client_secret",
        "auth",
        "authorization",
    }
)

# Fields that describe caching policy or cost attribution — not connection
# identity. Dropped so editing them doesn't
# cold-restart the whole source's cache (every cache entry keyed off that
# source_hash would otherwise miss).
# `cache`: the source's own authored cache: block (BaseSourceConfig.cache).
# `attribution`: cost-attribution metadata (BaseSourceConfig.attribution) — it
# rides along to the warehouse as job labels but changes neither the connection
# nor the rows a query returns.
INCIDENTAL_FIELDS: frozenset[str] = frozenset({"cache", "attribution"})


def source_identity(
    source: str | dict[str, Any] | BaseSourceConfig | None,
) -> str | dict[str, Any] | None:
    """Reduce a source reference to its identity for cache hashing.

    String names and ``None`` pass through unchanged — they carry no
    secrets and the caller hashes them as-is. Dict configs (from inline
    YAML or resolved from `board.sources`) have every secret-shaped field
    dropped, so passwords never reach cache table names or hash inputs.
    Identity-bearing fields (user, host, project, dataset, keyfile, etc.)
    are preserved so two users with the same source name but different
    credentials get distinct cache entries. Incidental fields (the source's
    own ``cache:`` block, cost attribution) are also
    dropped — see ``INCIDENTAL_FIELDS``.

    Args:
        source: ``str`` name reference, ``dict`` config, or ``None``.

    Returns:
        Same type, with secrets and incidental fields stripped from dicts.
    """
    if source is None or isinstance(source, str):
        return source
    source_dict = (
        source.model_dump() if isinstance(source, BaseSourceConfig) else source
    )
    dropped = SECRET_FIELDS | INCIDENTAL_FIELDS
    return {k: v for k, v in source_dict.items() if k.lower() not in dropped}
