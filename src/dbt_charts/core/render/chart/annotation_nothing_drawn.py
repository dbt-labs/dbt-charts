"""Render-time capture of an annotation the engine resolved but drew nothing for.

The engine decides this while placing marks (a category that names no slot, a
rule or band clipped off the plot), so ``WARN_ANNOTATION_NOTHING_DRAWN`` reads
the record rather than re-deriving the placement.

The sink is a ``ContextVar`` set around the render, mirroring
``x_domain_paint_order.py``. A chart may render more than once per sink (sizing
retries), so records are keyed by annotation index. When no sink is open,
recording is a no-op.
"""

from __future__ import annotations

import contextvars
from collections.abc import Generator
from contextlib import contextmanager

# chart id → authored annotation index → why nothing was drawn
NothingDrawn = dict[str, dict[int, str]]

_sinks: contextvars.ContextVar[tuple[NothingDrawn, ...]] = contextvars.ContextVar(
    "annotation_nothing_drawn_sinks", default=()
)


@contextmanager
def collect_annotation_nothing_drawn() -> Generator[NothingDrawn]:
    """Open a fresh sink for the duration of a render; yield the collected map."""
    collected: NothingDrawn = {}
    token = _sinks.set((*_sinks.get(), collected))
    try:
        yield collected
    finally:
        _sinks.reset(token)


def record_annotation_nothing_drawn(chart_id: str, index: int, reason: str) -> None:
    """Record an annotation that drew nothing into the innermost open sink, if any."""
    sinks = _sinks.get()
    if sinks:
        sinks[-1].setdefault(chart_id, {})[index] = reason
