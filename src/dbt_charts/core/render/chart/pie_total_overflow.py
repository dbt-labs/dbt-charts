"""Render-time capture of a donut whose center total is wider than its hole.

The engine measures the total against the hole the wheel actually draws and
reports the overflow (``engine.py``'s WARN-PIE-TOTAL-EXCEEDS-INNER-RADIUS
sink); ``render/warnings/pie_total_exceeds_inner_radius.py`` reads the
collected dict via ``WarningContext.pie_total_overflows``. Same ContextVar-sink
shape as ``plot_width_floor_record.py``.
"""

from __future__ import annotations

import contextvars
from collections.abc import Generator
from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict


class PieTotalOverflow(BaseModel):
    """The formatted total, its width, and the hole it overflows, in px.

    ``slot_width``/``slot_height`` are the plot box the wheel drew in.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    formatted_value: str
    text_width: float
    hole_diameter: float
    slot_width: float
    slot_height: float


_sinks: contextvars.ContextVar[tuple[dict[str, PieTotalOverflow], ...]] = (
    contextvars.ContextVar("pie_total_overflow_sinks", default=())
)


@contextmanager
def collect_pie_total_overflows() -> Generator[dict[str, PieTotalOverflow]]:
    """Open a fresh sink; yield the collected map of chart_id → overflow."""
    collected: dict[str, PieTotalOverflow] = {}
    token = _sinks.set((*_sinks.get(), collected))
    try:
        yield collected
    finally:
        _sinks.reset(token)


def record_pie_total_overflow(chart_id: str, overflow: PieTotalOverflow) -> None:
    """Record into the innermost open sink; a no-op when none is open."""
    sinks = _sinks.get()
    if sinks:
        sinks[-1][chart_id] = overflow
