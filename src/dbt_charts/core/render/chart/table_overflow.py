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


class TableCramping(BaseModel):
    """A table the renderer had to degrade to fit the width it was given.

    Distinct from ``TableOverflow``, which is the physical case: columns that
    cannot fit even at their minimum readable width, so the table is clipped at
    its slot's edge. Cramping is the quieter ladder below that — the renderer absorbs
    the width shortfall by wrapping headers, and the table still fits its box
    while reading badly. The height axis is not cramping: a slot cutting the
    rows-per-page down is captured as ``TablePageSqueeze``
    (``table_page_squeeze.py``) for WARN-TABLE-PAGE-SQUEEZED, whose grow-the-
    slot fix matches that cause.

    ``required_width`` is the columns' pre-allocation demand (cell content, or
    the header label where that is wider); ``available_width`` is the budget
    they were divided into. ``wrapped_headers`` of ``column_count`` counts
    headers forced onto a second line.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    required_width: float
    available_width: float
    wrapped_headers: int
    column_count: int
    # The board width that clears the cramping, sized by the engine.
    suggested_width: float


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


_cramping_sinks: contextvars.ContextVar[tuple[dict[str, TableCramping], ...]] = (
    contextvars.ContextVar("table_cramping_sinks", default=())
)


@contextmanager
def collect_table_crampings() -> Generator[dict[str, TableCramping]]:
    """Open a fresh cramping sink for the duration of a render."""
    collected: dict[str, TableCramping] = {}
    token = _cramping_sinks.set((*_cramping_sinks.get(), collected))
    try:
        yield collected
    finally:
        _cramping_sinks.reset(token)


def record_table_cramping(chart_id: str, cramping: TableCramping) -> None:
    """Record a table's cramping into the innermost open sink, if any.

    No-op when no sink is open (e.g. non-SVG formats).
    """
    sinks = _cramping_sinks.get()
    if sinks:
        sinks[-1][chart_id] = cramping
