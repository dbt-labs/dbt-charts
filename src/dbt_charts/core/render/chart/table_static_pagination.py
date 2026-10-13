"""Render-time capture of how a static export paginated each table.

A static export (``dct render --format html/svg`` with no interactive host)
ships no JS runtime to re-request a page from the server, so
``_paint_table`` pre-renders each page's rows into its own toggle
group and a small inline script flips which one is visible (see
``static_multi_page`` in ``render/chart/table.py``). Left uncapped, that loop
makes export size scale with total row count instead of page size —
``_STATIC_MULTI_PAGE_MAX_PAGES`` bounds it, and rows past the cap are never
drawn.

The page size the table drew with, and whether the note stating the cap fit
in the slot, are only known once the table is painted (a slot can squeeze the
page size below the authored ``page_rows`` and squeeze the note out), so the
renderer records them here. The text format states the counts; the
``WARN-STATIC-PAGINATION-CAPPED`` detector reads ``cap_note_dropped``. It
mirrors ``table_overflow.py``.

The sink is a ``ContextVar`` set around the SVG render, mirroring the existing
render-context vars in ``renderer.py``. Board rendering is single-threaded, so
the renderer's ``record_static_pagination`` call lands in the same context
the collector opened. When no sink is open (non-SVG formats, which never
rasterize a table), recording is a no-op.
"""

from __future__ import annotations

import contextvars
from collections.abc import Generator
from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict


class StaticPagination(BaseModel):
    """How a static export paginated one table.

    ``rendered_pages`` is how many pages made it into the artifact (at most
    the pre-render cap); ``total_pages`` is the table's real page count.
    ``cap_note_dropped`` is True when the export is capped and its slot was too
    short to draw the note that says so, leaving the file silent about the rows
    it lacks.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_rows: int
    total_rows: int
    rendered_pages: int
    total_pages: int
    cap_note_dropped: bool


# Stack of active collection sinks (innermost last); empty when no render pass
# is collecting. A stack rather than a nullable slot so nested renders each
# collect into their own map without a sentinel.
_sinks: contextvars.ContextVar[tuple[dict[str, StaticPagination], ...]] = (
    contextvars.ContextVar("static_pagination_sinks", default=())
)


@contextmanager
def collect_static_paginations() -> Generator[dict[str, StaticPagination]]:
    """Open a fresh sink for the duration of a render; yield the collected map.

    The renderer's ``record_static_pagination`` writes into this map while
    the context is open. Pops the sink on exit so nested renders don't leak
    into each other.
    """
    collected: dict[str, StaticPagination] = {}
    token = _sinks.set((*_sinks.get(), collected))
    try:
        yield collected
    finally:
        _sinks.reset(token)


def record_static_pagination(chart_id: str, pagination: StaticPagination) -> None:
    """Record how a table paginated into the innermost open sink.

    No-op when no sink is open (e.g. non-SVG formats).
    """
    sinks = _sinks.get()
    if sinks:
        sinks[-1][chart_id] = pagination
