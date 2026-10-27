"""Render-time capture of a numeric table column authored `align: left`.

Tables are a custom SVG renderer, not Vega-Lite, so there is no spec to
inspect: the Rust table engine judges the column while it sizes the number
lane and the engine's warning sinks record the verdict here. The two codes
(``WARN-TABLE-NUMERIC-ALIGN-LEFT``, ``WARN-TABLE-ALIGN-NO-VISIBLE-EFFECT``) are
mutually exclusive; their conditions live on the codes in
core/diagnostics/codes_render.py.

A sink is a ``ContextVar`` set around the SVG render, mirroring
``table_overflow.py``: board rendering is single-threaded, so ``record_*``
calls land in the sink the collector opened. A chart renders more than once
(sizing probes, placements); ``reset_table_align`` clears the chart's earlier
verdict first so the latest render wins. With no sink open (non-SVG formats),
recording is a no-op.
"""

from __future__ import annotations

import contextvars
from collections.abc import Generator
from contextlib import contextmanager

_ragged_sinks: contextvars.ContextVar[tuple[dict[str, list[str]], ...]] = (
    contextvars.ContextVar("table_numeric_align_left_sinks", default=())
)
_no_effect_sinks: contextvars.ContextVar[tuple[dict[str, list[str]], ...]] = (
    contextvars.ContextVar("table_align_no_visible_effect_sinks", default=())
)


@contextmanager
def collect_table_numeric_align_lefts() -> Generator[dict[str, list[str]]]:
    """Open a fresh sink for the duration of a render; yield the collected map."""
    collected: dict[str, list[str]] = {}
    token = _ragged_sinks.set((*_ragged_sinks.get(), collected))
    try:
        yield collected
    finally:
        _ragged_sinks.reset(token)


def record_table_numeric_align_left(chart_id: str, column: str) -> None:
    """Record a ragged-width left-aligned numeric column. No-op with no sink open."""
    sinks = _ragged_sinks.get()
    if sinks:
        sinks[-1].setdefault(chart_id, []).append(column)


@contextmanager
def collect_table_align_no_visible_effects() -> Generator[dict[str, list[str]]]:
    """Open a fresh sink for the duration of a render; yield the collected map."""
    collected: dict[str, list[str]] = {}
    token = _no_effect_sinks.set((*_no_effect_sinks.get(), collected))
    try:
        yield collected
    finally:
        _no_effect_sinks.reset(token)


def record_table_align_no_visible_effect(chart_id: str, column: str) -> None:
    """Record a no-slack left-aligned numeric column. No-op with no sink open."""
    sinks = _no_effect_sinks.get()
    if sinks:
        sinks[-1].setdefault(chart_id, []).append(column)


def reset_table_align(chart_id: str) -> None:
    """Drop `chart_id`'s earlier verdicts, so the latest render's wins."""
    for sinks in (_ragged_sinks.get(), _no_effect_sinks.get()):
        if sinks:
            sinks[-1].pop(chart_id, None)
