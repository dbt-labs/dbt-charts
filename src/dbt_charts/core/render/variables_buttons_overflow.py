"""Render-time capture of a button row too wide for its slot."""

from __future__ import annotations

import contextvars
from collections.abc import Generator
from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict


class ButtonsOverflow(BaseModel):
    """One button row that drew as its menu form because it did not fit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    variable: str
    path: str | None
    needed: float
    available: float


_sinks: contextvars.ContextVar[tuple[list[ButtonsOverflow], ...]] = (
    contextvars.ContextVar("variable_buttons_overflow_sinks", default=())
)


@contextmanager
def collect_buttons_overflows() -> Generator[list[ButtonsOverflow]]:
    """Open a fresh sink and yield what is collected into it."""
    collected: list[ButtonsOverflow] = []
    token = _sinks.set((*_sinks.get(), collected))
    try:
        yield collected
    finally:
        _sinks.reset(token)


def record_buttons_overflow(record: ButtonsOverflow) -> None:
    """Write into the innermost open sink, if any."""
    sinks = _sinks.get()
    if sinks:
        sinks[-1].append(record)
