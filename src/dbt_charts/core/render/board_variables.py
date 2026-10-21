"""The board's resolved variable values, scoped for one render pass.

Stage: RENDER
Purpose: Let a render-time decision that depends on a variable's current value
(e.g. which page a paginated table is showing) read it without every caller
having to remember to thread ``variables=`` through.

Consumers (the engine seam) read ``current_board_variables()``
themselves rather than requiring each caller to pass a ``variables=``
parameter through — a caller that forgot to opt in is exactly how a prior
variables-plumbing gap shipped (two production call sites silently painted
page 1 regardless of the real page). The scope is opened at each render
pass's own entry point, mirroring ``interactive_controls`` in
``controls.py``: ``render_board_svg`` (``boards.py``) opens it around its own
layout walk, so both the live ``render()`` main pass and ``dct artifact
render``'s replay path (``board_replay.py``) get it from the one place they
both call directly; the sizing pass never calls ``render_board_svg``, so
``renderer.py`` opens a separate scope around ``build_resolved_board``.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from typing import TYPE_CHECKING

from dbt_charts.core.compile.models.board.normalized import VariableValues

if TYPE_CHECKING:
    from collections.abc import Generator

    from dbt_charts.core.execute.chart_data_provider import ChartDataProvider

_BOARD_VARIABLES: ContextVar[VariableValues | None] = ContextVar(
    "dct_board_variables", default=None
)


@contextmanager
def board_variables(values: VariableValues) -> Generator[None]:
    """Scope the board's resolved variable values for one render pass."""
    token = _BOARD_VARIABLES.set(values)
    try:
        yield
    finally:
        _BOARD_VARIABLES.reset(token)


_ROOT_BOARD_WIDTH: ContextVar[float | None] = ContextVar(
    "dct_root_board_width", default=None
)


@contextmanager
def root_board_width(width: float) -> Generator[None]:
    """Scope the root board's width: the engine sizes a table's width fix from it."""
    token = _ROOT_BOARD_WIDTH.set(width)
    try:
        yield
    finally:
        _ROOT_BOARD_WIDTH.reset(token)


def current_root_board_width() -> float | None:
    """The root board's width for the render pass in progress, if any."""
    return _ROOT_BOARD_WIDTH.get()


def current_board_variables() -> VariableValues | None:
    """The board's variable values for the render pass in progress, if any."""
    return _BOARD_VARIABLES.get()


_BOARD_DATA_PROVIDER: ContextVar[ChartDataProvider | None] = ContextVar(
    "dct_board_data_provider", default=None
)


@contextmanager
def board_data_provider(provider: ChartDataProvider) -> Generator[None]:
    """Scope the query-result source board text reads."""
    token = _BOARD_DATA_PROVIDER.set(provider)
    try:
        yield
    finally:
        _BOARD_DATA_PROVIDER.reset(token)


def current_board_data_provider() -> ChartDataProvider | None:
    return _BOARD_DATA_PROVIDER.get()


# Render narrows a preset to its dates, so the id a control commits rides here.
_DATERANGE_PRESETS: ContextVar[Mapping[str, str]] = ContextVar(
    "dct_daterange_presets", default=MappingProxyType({})
)


@contextmanager
def daterange_presets(presets: Mapping[str, str]) -> Generator[None]:
    """Scope the committed preset id of each preset-valued daterange variable."""
    token = _DATERANGE_PRESETS.set(presets)
    try:
        yield
    finally:
        _DATERANGE_PRESETS.reset(token)


def current_daterange_presets() -> Mapping[str, str]:
    """Variable name to committed preset id, for the render in progress."""
    return _DATERANGE_PRESETS.get()
