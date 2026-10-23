"""Render-time capture of an x-axis whose tick labels still collide after
every enabled overlap strategy (skip/tilt) was tried.

The engine reports the collision as ``WARN-AXIS-LABEL-COLLISION`` at the point its
overlap strategies give up. This module is the seam that lets a warning detector
see it, mirroring ``table_overflow.py``: ``engine.py`` records the fact into a
sink a detector reads via ``WarningContext.axis_label_collisions``.

Scoped to line/area/scatter — bar and heatmap have their own
collision-adjacent warnings (``WARN_BAR_BAND_WIDTH_TOO_NARROW``,
``WARN_TOO_MANY_X_CATEGORIES``) and are not covered here.
"""

from __future__ import annotations

import contextvars
from collections.abc import Generator
from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict


class AxisLabelCollision(BaseModel):
    """An x-axis whose tick labels still overlap after skip/tilt were tried.

    ``label_count`` is the number of labels the final (possibly skipped)
    layout still had to fit; ``field`` names the x column so a detector can
    point the diagnostic's ``path`` at ``charts.<id>.x``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    label_count: int


_sinks: contextvars.ContextVar[tuple[dict[str, AxisLabelCollision], ...]] = (
    contextvars.ContextVar("axis_label_collision_sinks", default=())
)


@contextmanager
def collect_axis_label_collisions() -> Generator[dict[str, AxisLabelCollision]]:
    """Open a fresh sink for the duration of a render; yield the collected map."""
    collected: dict[str, AxisLabelCollision] = {}
    token = _sinks.set((*_sinks.get(), collected))
    try:
        yield collected
    finally:
        _sinks.reset(token)


def reset_axis_label_collision(chart_id: str) -> None:
    """Forget an earlier render's collision: the last render wins."""
    sinks = _sinks.get()
    if sinks:
        sinks[-1].pop(chart_id, None)


def record_axis_label_collision(chart_id: str, collision: AxisLabelCollision) -> None:
    """Record a chart's unresolved axis-label collision into the innermost
    open sink, if any. No-op when no sink is open."""
    sinks = _sinks.get()
    if sinks:
        sinks[-1][chart_id] = collision
