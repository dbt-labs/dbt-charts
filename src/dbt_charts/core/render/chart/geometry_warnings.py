"""Render-time capture of the warnings the engine raises about a laid-out chart.

Mirrors ``plot_width_floor_record.py``'s ContextVar-sink shape: a band, panel or
label-slot width is a fact of the laid-out chart, so the engine reports it as a
warning and the matching detector (``render/warnings``) turns the captured
params into a ``Diagnostic``. ``_collect_render_warnings`` opens the sink around
its render pass; ``engine.py`` records into it; ``WarningContext.geometry_warnings``
carries the result.
"""

from __future__ import annotations

import contextvars
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

EngineParams = dict[str, Any]  # type-state: explicit_any — engine warning params
# chart id -> warning code -> the engine's params for each time it raised it.
GeometryWarnings = dict[str, dict[str, list[EngineParams]]]

_sinks: contextvars.ContextVar[tuple[GeometryWarnings, ...]] = contextvars.ContextVar(
    "geometry_warning_sinks", default=()
)
# chart id -> the narrowest painted width its placement warnings were recorded at.
_placement_widths: contextvars.ContextVar[tuple[dict[str, float], ...]] = (
    contextvars.ContextVar("geometry_placement_widths", default=())
)


@contextmanager
def collect_geometry_warnings() -> Generator[GeometryWarnings]:
    """Open a fresh sink; yield the collected map of chart id to code to params."""
    collected: GeometryWarnings = {}
    token = _sinks.set((*_sinks.get(), collected))
    widths_token = _placement_widths.set((*_placement_widths.get(), {}))
    try:
        yield collected
    finally:
        _placement_widths.reset(widths_token)
        _sinks.reset(token)


def claim_placement(chart_id: str, width: float) -> bool:
    """Whether a painted render at `width` speaks for the chart's placement warnings.

    A chart placed twice is judged at its narrowest placement, where crowding shows;
    a render at the same width replaces an earlier one. With no sink open every
    render claims.
    """
    widths = _placement_widths.get()
    if not widths:
        return True
    recorded = widths[-1].get(chart_id)
    if recorded is not None and width > recorded:
        return False
    widths[-1][chart_id] = width
    return True


def reset_geometry_warnings(chart_id: str) -> None:
    """Forget what an earlier painted render of the chart raised, once a render at its
    narrowest placement so far replaces it."""
    sinks = _sinks.get()
    if sinks:
        sinks[-1].pop(chart_id, None)


def record_geometry_warning(code: str, params: EngineParams) -> None:
    """Record a warning into the innermost open sink; a no-op with none open."""
    sinks = _sinks.get()
    if sinks:
        sinks[-1].setdefault(params["chart_id"], {}).setdefault(code, []).append(params)
