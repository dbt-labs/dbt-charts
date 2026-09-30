"""Render-time capture of tables whose slot fits fewer rows than the page holds.

An auto-sized tile is measured by the renderer and always fits its page, but a
pinned slot (explicit height, grid row, equalized siblings) can be shorter:
``_largest_safe_page_rows`` then cuts the page, and the export photographs as a
faithful table while half the data moved to page 2.

This module is the seam that lets a warning detector see that cut,
mirroring ``table_overflow.py`` and ``table_static_pagination.py``: the renderer
records it at the exact point it settles on a page smaller than the one the
table's own pagination asked for, into a sink that ``TABLE_PAGE_SQUEEZED`` reads
via ``WarningContext.table_page_squeezes``.

The sink is a ``ContextVar`` set around the SVG render, mirroring the existing
render-context vars in ``renderer.py``. Board rendering is single-threaded, so
the renderer's ``record_table_page_squeeze`` call lands in the same context the
collector opened. When no sink is open (non-SVG formats, which never rasterize a
table), recording is a no-op.
"""

from __future__ import annotations

import contextvars
from collections.abc import Generator
from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict


class TablePageSqueeze(BaseModel):
    """A table drawn at fewer rows per page than its pagination asked for.

    ``drawn_rows`` is what the slot fit; ``page_rows`` is what a page would have
    held had it not — the resolved pagination page size, capped at the row count
    that exists, so a 20-row default over 4 rows asks for 4, not 20; and
    ``total_rows`` is how many rows the query returned. Recorded exactly when
    ``drawn_rows < page_rows`` — the point where the paginator overrides the
    height the sizer reserved.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    drawn_rows: int
    page_rows: int
    total_rows: int


# Stack of active collection sinks (innermost last); empty when no render pass
# is collecting. A stack rather than a nullable slot so nested renders each
# collect into their own map without a sentinel.
_sinks: contextvars.ContextVar[tuple[dict[str, TablePageSqueeze], ...]] = (
    contextvars.ContextVar("table_page_squeeze_sinks", default=())
)


@contextmanager
def collect_table_page_squeezes() -> Generator[dict[str, TablePageSqueeze]]:
    """Open a fresh sink for the duration of a render; yield the collected map.

    The renderer's ``record_table_page_squeeze`` writes into this map while the
    context is open. Pops the sink on exit so nested renders don't leak into
    each other.
    """
    collected: dict[str, TablePageSqueeze] = {}
    token = _sinks.set((*_sinks.get(), collected))
    try:
        yield collected
    finally:
        _sinks.reset(token)


def record_table_page_squeeze(chart_id: str, squeeze: TablePageSqueeze) -> None:
    """Record a table's squeezed page into the innermost open sink.

    No-op when no sink is open (e.g. non-SVG formats).
    """
    sinks = _sinks.get()
    if sinks:
        sinks[-1][chart_id] = squeeze
