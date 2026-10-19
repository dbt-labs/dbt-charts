"""Query execution helpers for the render pipeline.

All chart-direct queries are submitted concurrently via ``ThreadPoolExecutor``.
``{{ queries.X }}`` dependencies are *not* pre-executed separately because the
executor inlines them as subqueries — executing the dependency query on its own
would be wasted work.
"""

from __future__ import annotations

import contextvars
import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.models.board.normalized import VariableValues

if TYPE_CHECKING:
    from dbt_charts.core.execute.executor import Executor

logger = logging.getLogger(__name__)

# Names the threads of the per-render query pool. A host that opens a
# thread-local resource per query (e.g. a per-thread DB connection) can key its
# cleanup on this prefix, so it reaps only pool workers and never the
# request/task thread that runs the synchronous cache-hit pre-pass.
RENDER_POOL_THREAD_PREFIX = "dct-render"


def execute_queries_parallel(
    executor: Executor,
    query_names: set[str],
    variables: VariableValues | None = None,
    max_workers: int = 8,
) -> dict[str, Exception | None]:
    """Execute *query_names* concurrently via ``ThreadPoolExecutor``.

    All queries are submitted immediately — ``{{ queries.X }}`` references are
    Jinja-inlined by the executor so no ordering is needed.

    Returns ``{query_name: None}`` on success or ``{query_name: exc}``
    on failure.  Errors in one query don't block others.
    """
    if not query_names:
        return {}

    results: dict[str, Exception | None] = {}

    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix=RENDER_POOL_THREAD_PREFIX
    ) as pool:
        # `ContextVar` does not cross `ThreadPoolExecutor.submit` on its own — a
        # copy per task (not one shared copy) so `attribute()`'s scope (surface,
        # actor, client, …) reaches every worker without the workers stepping on
        # each other's Context via concurrent `.run()`.
        futures: dict[Future[list[dict[str, Any]]], str] = {
            pool.submit(
                contextvars.copy_context().run, executor.execute_query, name, variables
            ): name
            for name in query_names
        }

        for fut in as_completed(futures):
            name = futures[fut]
            try:
                fut.result()
                results[name] = None
            except Exception as exc:  # noqa: BLE001 — arbitrary adapter errors
                logger.debug("Parallel execution of query '%s' failed: %s", name, exc)
                results[name] = exc
                executor._query_errors[name] = exc

    return results
