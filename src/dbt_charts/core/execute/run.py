"""Run stage: execute the lookup's misses and report one outcome per query.

Stage: EXECUTE (run)
Purpose: Take the ``Miss``es a lookup could not answer and return
    ``Mapping[name, Outcome]`` with ``Outcome = Rows | Failed``. How one miss
    executes is the caller's ``execute``; this module owns what surrounds it:
    the worker pool, the ``{{ queries.X.cache }}`` dependency waves, and the
    failure boundary, so no error is raised past ``run``.

Entry Points:
    - run(misses, execute, settle, max_workers) -> dict[str, Outcome]
"""

import contextvars
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from graphlib import TopologicalSorter

from dbt_charts.core.compile.models.query.normalized import is_sql_query
from dbt_charts.core.execute.adapters.base import ResolvedRelation
from dbt_charts.core.execute.cache_backend import ResultRows
from dbt_charts.core.execute.lookup import Miss

# Names the threads of the per-render query pool. A host that opens a
# thread-local resource per query (e.g. a per-thread DB connection) can key its
# cleanup on this prefix, so it reaps only pool workers and never the
# request/task thread that runs the synchronous cache-hit pre-pass.
RENDER_POOL_THREAD_PREFIX = "dct-render"

# Matches {{ queries.NAME.cache }} in authored SQL. The \s* around the dot
# tolerates whitespace variants like {{ queries.A . cache }} for consistency
# with the dep-graph regex.
CACHE_REF_JINJA_RE: re.Pattern[str] = re.compile(
    r"\{\{\s*queries\.(\w+)\s*\.\s*cache\s*\}\}"
)


@dataclass(frozen=True)
class Rows:
    """A miss that ran and produced rows."""

    result: ResultRows
    # None when the rows extend a stored set whose age the lookup already recorded.
    data_as_of: datetime | None
    relations: Sequence[ResolvedRelation] = ()


@dataclass(frozen=True)
class Failed:
    """A miss that raised. ``record`` is the failure as the store should keep it
    and the data ages count it: set for the query's own failures, None for a
    failure of the machinery around the query (an incremental tail, a cache-ref
    upstream), which is neither stored nor counted."""

    error: Exception
    record: Exception | None


Outcome = Rows | Failed


def _waves(misses: Sequence[Miss]) -> list[list[Miss]]:
    """Layer *misses* so a ``{{ queries.X.cache }}`` upstream runs and settles
    before its consumer. Upstreams that are not misses (served by lookup) gate
    nothing."""
    by_name = {miss.name: miss for miss in misses}
    graph: dict[str, set[str]] = {
        miss.name: (
            {
                ref
                for ref in CACHE_REF_JINJA_RE.findall(miss.query.sql)
                if ref in by_name
            }
            if is_sql_query(miss.query)
            else set()
        )
        for miss in misses
    }
    sorter = TopologicalSorter(graph)
    sorter.prepare()
    waves: list[list[Miss]] = []
    while sorter.is_active():
        ready = sorter.get_ready()
        waves.append([by_name[name] for name in ready])
        sorter.done(*ready)
    return waves


def run(
    misses: Sequence[Miss],
    execute: Callable[[Miss], Outcome],
    settle: Callable[[Miss, Outcome], None],
    max_workers: int | None = None,
) -> dict[str, Outcome]:
    """Execute *misses*; every one lands in the result as ``Rows`` or ``Failed``.

    ``execute`` runs one miss; whatever it raises becomes an uncacheable
    ``Failed``. ``settle`` is called once per miss, on the thread that ran it,
    as soon as its outcome is known, so a later wave (and the executor's memo)
    sees an upstream's rows. With ``max_workers=None`` everything runs on the
    calling thread; otherwise each wave fans across a pool of that width.
    """
    outcomes: dict[str, Outcome] = {}

    def run_one(miss: Miss) -> None:
        try:
            outcome = execute(miss)
        except Exception as exc:  # noqa: BLE001 — a backend may raise anything
            outcome = Failed(exc, None)
        outcomes[miss.name] = outcome
        try:
            settle(miss, outcome)
        except Exception as exc:  # noqa: BLE001 — a cache write may raise anything
            if isinstance(outcome, Rows):
                outcomes[miss.name] = Failed(exc, None)

    if not misses:
        return outcomes
    if max_workers is None:
        for wave in _waves(misses):
            for miss in wave:
                run_one(miss)
        return outcomes
    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix=RENDER_POOL_THREAD_PREFIX
    ) as pool:
        for wave in _waves(misses):
            # `ContextVar` does not cross `submit` on its own — a copy per task,
            # so `attribute()`'s scope reaches every worker without the workers
            # stepping on each other's Context via concurrent `.run()`.
            futures = [
                pool.submit(contextvars.copy_context().run, run_one, miss)
                for miss in wave
            ]
            for future in futures:
                future.result()
    return outcomes
