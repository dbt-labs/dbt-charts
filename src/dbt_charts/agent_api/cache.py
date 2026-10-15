"""Cache constructors at the agent_api boundary."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from dbt_charts.core.compile.config import resolve_cache_boot

# Re-exported: TrivialDuckDBCache is the declared return type of open_cache,
# so embedders annotating a held cache reach it here without a core import.
from dbt_charts.core.execute.trivial_local_cache import (
    TrivialDuckDBCache as TrivialDuckDBCache,
)

if TYPE_CHECKING:
    from dbt_charts.cli.filesystem_project import FilesystemProject

__all__ = [
    "TrivialDuckDBCache",
    "open_cache",
    "project_cache_ctx",
]


def open_cache(
    path: Path | None, max_age: timedelta | None = None
) -> TrivialDuckDBCache:
    """Open the query-result cache at an already-resolved path.

    ``path=None`` opens an in-memory cache — ephemeral, discarded when closed.
    A path opens (or creates, if absent) a persistent DuckDB file, reused
    across invocations and safe to share with other processes: the file is
    held only for the length of each read or write. ``max_age`` deletes
    entries older than that when the cache is opened; None keeps them all.
    Callers own the returned cache's lifecycle (close it).
    Does not consult project config — use ``project_cache_ctx`` for that.
    """
    return TrivialDuckDBCache(db_path=path, max_age=max_age)


@contextmanager
def project_cache_ctx(
    project: FilesystemProject,
    *,
    no_cache: bool = False,
    cache_path: Path | None = None,
) -> Generator[TrivialDuckDBCache | None]:
    """Open the query-result cache honoring project config, closing it on exit.

    The caller owns the project instance. Pair this with
    ``ProjectSession.from_project(project, cache=...)`` rather than
    ``ProjectSession.open(project_dir, cache=...)`` — ``open()`` builds its own
    project from the path, so composing it with this function constructs two.

    Precedence: ``no_cache=True`` (the CLI's ``--no-cache``) skips the cache
    entirely; ``cache_path`` (``--cache PATH``, or its ``DCT_CACHE_PATH`` env
    backing) opens that persistent file. Otherwise the project's
    ``dbt_charts.yml`` ``cache:`` block picks the location — a configured ``path``
    opens that file (created if absent); no path opens the per-user file
    shared by every project, pruned of entries older than a week. A project-level ``cache: false`` defaults the *cascade*
    to off without closing the door on a nearer scope's opt-in, so it still
    opens a store; the resolved per-query policy decides what gets written.

    Yields:
        ``None`` only when ``no_cache=True`` — callers pass this straight
        through as ``ProjectSession(cache=...)``.
    """
    boot = resolve_cache_boot(project, no_cache=no_cache, cache_path=cache_path)
    cache = open_cache(boot.path, boot.max_age) if boot.enabled else None
    try:
        yield cache
    finally:
        if cache is not None:
            cache.close()
