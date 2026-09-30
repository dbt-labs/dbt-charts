"""Render-time capture of static exports that couldn't pre-render every page.

A static export (``dct render --format html/svg`` with no interactive host)
ships no JS runtime to re-request a page from the server, so
``_paint_table`` pre-renders every page's rows into its own toggle
group and a small inline script flips which one is visible (see
``static_multi_page`` in ``render/chart/table.py``). Left uncapped, that loop
makes export size scale with total row count instead of page size —
``_STATIC_MULTI_PAGE_MAX_PAGES`` bounds it, and rows past the cap are never
drawn.

This module is the seam that lets a warning detector see it, mirroring
``table_overflow.py``: the table renderer records the cap firing at the exact
point it decides not to render the remaining pages, into a sink that
``STATIC_PAGINATION_CAPPED`` reads via ``WarningContext.static_pagination_caps``.

The sink is a ``ContextVar`` set around the SVG render, mirroring the existing
render-context vars in ``renderer.py``. Board rendering is single-threaded, so
the renderer's ``record_static_pagination_cap`` call lands in the same context
the collector opened. When no sink is open (non-SVG formats, which never
rasterize a table), recording is a no-op.
"""

from __future__ import annotations

import contextvars
from collections.abc import Generator
from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict


class StaticPaginationCap(BaseModel):
    """A static export that hit the pre-rendered-page cap.

    ``rendered_pages`` is how many pages actually made it into the artifact
    (the cap, or fewer if it never reached the cap for other reasons);
    ``total_pages`` is the table's real page count. Recorded exactly when
    ``rendered_pages < total_pages`` — the renderer's own truncation boundary.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    rendered_pages: int
    total_pages: int


# Stack of active collection sinks (innermost last); empty when no render pass
# is collecting. A stack rather than a nullable slot so nested renders each
# collect into their own map without a sentinel.
_sinks: contextvars.ContextVar[tuple[dict[str, StaticPaginationCap], ...]] = (
    contextvars.ContextVar("static_pagination_cap_sinks", default=())
)


@contextmanager
def collect_static_pagination_caps() -> Generator[dict[str, StaticPaginationCap]]:
    """Open a fresh sink for the duration of a render; yield the collected map.

    The renderer's ``record_static_pagination_cap`` writes into this map while
    the context is open. Pops the sink on exit so nested renders don't leak
    into each other.
    """
    collected: dict[str, StaticPaginationCap] = {}
    token = _sinks.set((*_sinks.get(), collected))
    try:
        yield collected
    finally:
        _sinks.reset(token)


def record_static_pagination_cap(chart_id: str, cap: StaticPaginationCap) -> None:
    """Record a table's static-export page cap into the innermost open sink.

    No-op when no sink is open (e.g. non-SVG formats).
    """
    sinks = _sinks.get()
    if sinks:
        sinks[-1][chart_id] = cap
