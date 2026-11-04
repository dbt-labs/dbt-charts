"""Render-time capture of tables that don't fit the width they were given.

A table sizes its columns to honor each one's minimum readable width. When
those widths sum past the width the table was handed (its dashboard tile, or the
page), the renderer clips the table at its slot's edge, so the columns past it
are not shown. Nothing but a warning surfaces that to the author.

This module is the seam that lets a warning detector see it. The table renderer
records the overflow at the exact point it decides the columns don't fit — so
the signal is the renderer's own boundary, not a separate guess — into a sink
that ``TABLE_COLUMNS_OVERFLOW`` reads via ``WarningContext.table_overflows``.

The sink is a ``ContextVar`` set around the SVG render, mirroring the existing
render-context vars in ``renderer.py``. Board rendering is single-threaded, so
the renderer's ``record_table_overflow`` call lands in the same context the
collector opened. When no sink is open (non-SVG formats, which never rasterize a
table), recording is a no-op — those formats have no visual overflow to warn
about.
"""

from __future__ import annotations

import contextvars
from collections.abc import Generator
from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict


class TableOverflowCause(BaseModel):
    """The word that forced a table past its slot.

    Present only when the columns would have fit with their word floors lifted,
    so pins and numeric lanes are never blamed for it. ``column`` is the column
    its floor lifted most and ``word_width`` that floor's word. ``header_word``
    is the word itself when it comes from the header; a cell's value is never
    carried.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    column: str
    word_width: float
    header_word: str | None  # type-state: optional — a cell's word is never carried


class TableOverflow(BaseModel):
    """A table whose columns need more width than its slot allows.

    Measured at render time from the settled column layout, so it reflects what
    actually rendered — not an estimate. ``required_width`` is the table's
    natural width once every column has its honored (minimum readable) width;
    ``available_width`` is the width the table was given. Recorded exactly when
    ``required_width > available_width`` — the renderer's own clip boundary.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    required_width: float
    available_width: float
    cause: TableOverflowCause | None  # type-state: optional — no word to blame


# Stack of active collection sinks (innermost last); empty when no render pass
# is collecting. A stack rather than a nullable slot so nested renders each
# collect into their own map without a sentinel.
_sinks: contextvars.ContextVar[tuple[dict[str, TableOverflow], ...]] = (
    contextvars.ContextVar("table_overflow_sinks", default=())
)


@contextmanager
def collect_table_overflows() -> Generator[dict[str, TableOverflow]]:
    """Open a fresh sink for the duration of a render; yield the collected map.

    The renderer's ``record_table_overflow`` writes into this map while the
    context is open. Pops the sink on exit so nested renders don't leak into
    each other.
    """
    collected: dict[str, TableOverflow] = {}
    token = _sinks.set((*_sinks.get(), collected))
    try:
        yield collected
    finally:
        _sinks.reset(token)


def record_table_overflow(chart_id: str, overflow: TableOverflow) -> None:
    """Record a table's slot overflow into the innermost open sink, if any.

    No-op when no sink is open (e.g. non-SVG formats).
    """
    sinks = _sinks.get()
    if sinks:
        sinks[-1][chart_id] = overflow
