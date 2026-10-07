"""Registry pre-template query execution.

Purpose: Execute a RegisteredView's ``queries:`` block before its template
         renders, exposing results as ``queries.<name>`` template context.

The public API is:
- ``materialize_path_params(spec, path_params)`` — replace ``{{ path.X }}``
  placeholders with literal route path param values.
- ``run_registry_queries(view, path_params, adapter_registry, ...)`` — execute
  all queries declared in the view's ``queries:`` block and return a
  ``dict[name, QueryResult]`` for the template context.

Registry queries reuse the normal dbt charts query system: the same
``normalize_query`` entry point, source protocols, values protocols,
validation, and execution code as ordinary board queries.  ``{{ path.X }}``
placeholders are materialized before the query spec reaches the normalizer,
because ``SchemaQuery``'s Pydantic validators reject raw Jinja templates.

A slow-registry-query warning is emitted via ``logging.warning`` when a query
exceeds ``slow_query_threshold_s`` (default 2.0 s).  Registry queries run
synchronously before template rendering, so slow ones block the pipeline.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.normalize.queries import normalize_query
from dbt_charts.core.compile.template.query_result import QueryResult
from dbt_charts.core.execute.cache_backend import CacheHit

if TYPE_CHECKING:
    from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
    from dbt_charts.core.execute.cache_backend import QueryResultCache
    from dbt_charts.core.registered_views.models import RegisteredView

logger = logging.getLogger(__name__)

# Matches {{ path.param_name }} with optional surrounding whitespace.
_PATH_PARAM_RE = re.compile(r"\{\{[\t ]*path\.([a-zA-Z_][a-zA-Z0-9_]*)[\t ]*\}\}")

# Default slow-query threshold for registry queries.  Registry queries block
# generated-board construction, so 2 s is a reasonable authorship nudge.
DEFAULT_SLOW_QUERY_THRESHOLD_S: float = 2.0


class RegistryQueryError(Exception):
    """Raised when a registry query fails to normalize, materialize, or execute.

    Always includes the view name and query name in the message so the author
    can locate the offending registry entry.
    """


# ---------------------------------------------------------------------------
# Path-param materialization
# ---------------------------------------------------------------------------


def materialize_path_params(
    spec: dict[str, Any],
    path_params: dict[str, str],
) -> dict[str, Any]:
    """Replace ``{{ path.X }}`` placeholders in a query spec with literal values.

    Only top-level string values are processed.  Nested dicts/lists (e.g. a
    ``filters`` block) are left untouched — they are processed by the normal
    query execution path.

    Args:
        spec: Raw query spec dict (e.g. from registry YAML ``queries:``).
        path_params: Route path params extracted by the router (e.g.
            ``{"source": "snowflake", "schema": "raw", "table": "events"}``).

    Returns:
        A new dict with ``{{ path.X }}`` placeholders replaced by their values.

    Raises:
        ValueError: If a ``{{ path.X }}`` placeholder references a key that is
            not present in ``path_params``.
    """
    result: dict[str, Any] = {}
    for key, value in spec.items():
        if not isinstance(value, str):
            result[key] = value
            continue

        def _replace(m: re.Match[str]) -> str:
            param = m.group(1)
            if param not in path_params:
                raise ValueError(
                    f"Registry query references '{{{{ path.{param} }}}}' but "
                    f"'{param}' is not in the route path params. "
                    f"Available: {sorted(path_params)}"
                )
            return path_params[param]

        result[key] = _PATH_PARAM_RE.sub(_replace, value)
    return result


# ---------------------------------------------------------------------------
# run_registry_queries
# ---------------------------------------------------------------------------


def run_registry_queries(
    view: RegisteredView,
    path_params: dict[str, str],
    adapter_registry: AdapterRegistry,
    cache: QueryResultCache | None = None,
    *,
    slow_query_threshold_s: float = DEFAULT_SLOW_QUERY_THRESHOLD_S,
) -> dict[str, QueryResult]:
    """Execute all pre-template queries declared in a registered view.

    Each query in ``view.queries`` is:
    1. Materialized: ``{{ path.X }}`` placeholders replaced with values from
       ``path_params``.
    2. Normalized: validated with ``normalize_query`` (same path as board queries).
    3. Executed: run via ``adapter_registry.execute``.
    4. Wrapped: result becomes a ``QueryResult``.

    Results are returned as a ``dict[name, QueryResult]`` — the
    expansion pipeline injects this into the template context as
    ``queries.<name>``.

    Args:
        view: The registered view whose ``queries:`` block to run.
        path_params: Route path params (e.g. from ``RouteMatch.path_params``).
        adapter_registry: An ``AdapterRegistry`` with adapters for the query
            types declared in the view (e.g. ``type: schema``, ``type: sql``).
        cache: Optional content-addressed result cache. When provided, a cache
            hit skips ``adapter_registry.execute`` entirely. ``None`` disables
            caching (existing behavior). Pass the serve cache instance so
            ``/data/`` results share the same file and TTL as dashboard queries.
        slow_query_threshold_s: Emit a ``logging.warning`` when a single
            registry query takes longer than this many seconds (default 2.0).
            Registry queries block generated-board construction.

    Returns:
        Dict mapping each query name to its ``QueryResult``.  Empty
        dict when ``view.queries`` is None or empty.

    Raises:
        RegistryQueryError: If path-param materialization fails, the query
            spec fails normalization/validation, or the adapter returns an
            error.  Always includes the view name and query name.
    """
    if not view.queries:
        return {}

    results: dict[str, QueryResult] = {}

    for query_name, raw_spec in view.queries.items():
        context = f"view={view.name!r}, query={query_name!r}"

        # Step 1: Materialize {{ path.X }} into literal values.
        try:
            spec = materialize_path_params(raw_spec, path_params)
        except ValueError as exc:
            raise RegistryQueryError(
                f"Path-param materialization failed ({context}): {exc}"
            ) from exc

        # Step 2: Normalize (validate) — reuses normal query compilation.
        try:
            query = normalize_query(
                query_name,
                spec,
                sources=adapter_registry.project.sources.sources,
            )
        except CompilationError as exc:
            raise RegistryQueryError(
                f"Query validation failed ({context}): {exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 — pydantic ValidationError and friends
            raise RegistryQueryError(
                f"Query normalization failed ({context}): {exc}"
            ) from exc

        # Step 3: Execute via the adapter registry (with optional cache).
        # query.cache is the resolved policy (project cascade root, since
        # registered views author no per-query cache: block today): false
        # opts the query out of both read and write, matching board queries.
        cache_key: tuple[str, str, str] | None = None
        if cache is not None and query.cache.enabled:
            from dbt_charts.core.execute.duckdb_cache import compute_cache_key

            # Path params are already baked into the materialized SQL /
            # source_description, so there are no variables — keying is purely by
            # query content. No board context here, so board_sources is omitted.
            # File sources are intentionally NOT handled on this path. A SqlQuery
            # against a file source can now succeed through
            # adapter_registry.execute when a materializer is configured — it no
            # longer fails loudly at the SQL adapter — but every pre-template
            # query registry.yaml ships today is `type: schema`, which stays
            # refused for file sources unconditionally regardless of a
            # materializer. So no file-source query actually reaches this cache
            # today; if a future entry authors a SqlQuery against a file source,
            # this key must fold in Project.file_version first, or edited data
            # files would go stale.
            cache_key = compute_cache_key(query)
            outcome = cache.get(*cache_key, ttl=query.cache.ttl_timedelta)
            # Registry queries raise on failure rather than caching the error
            # (below), so only a rows outcome can be here — narrowed anyway so
            # a cached error from any other writer can never be read as rows.
            if isinstance(outcome, CacheHit):
                results[query_name] = QueryResult(query_name, outcome.rows)
                continue

        start = time.monotonic()
        result = adapter_registry.execute(
            query, variables=None, board=None, query_name=query_name
        )
        elapsed = time.monotonic() - start

        if elapsed > slow_query_threshold_s:
            logger.warning(
                "Slow registry query: view=%r query=%r took %.3fs "
                "(threshold %.1fs). Registry queries block generated-board "
                "construction — consider caching or simplifying the query.",
                view.name,
                query_name,
                elapsed,
                slow_query_threshold_s,
            )

        if result.error:
            raise RegistryQueryError(
                f"Query execution failed ({context}): {result.error}"
            )
        if result.truncated_reason is not None:
            raise RegistryQueryError(
                f"Registry query '{query_name}' for view '{view.name}' was truncated "
                f"to {len(result.data)} rows by {result.truncated_reason!r} — the "
                "board-generation template would receive incomplete data. "
                "Raise execution.max_rows or DCT_MAX_ROWS_CEILING to fix this."
            )

        if cache is not None and cache_key is not None:
            cache.put(
                *cache_key,
                result.data,
                board_slug=view.name,
                query_name=query_name,
            )

        results[query_name] = QueryResult(query_name, result.data)

    return results
