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

import datetime
import json
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypedDict

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


def current_board_variables() -> VariableValues | None:
    """The board's variable values for the render pass in progress, if any."""
    return _BOARD_VARIABLES.get()


def held_legend_view(chart_id: str) -> tuple[str, str] | None:
    """A chart's view param and its value as JSON, when the board holds one.

    The chart carries it whether or not it draws an entry or a label to change
    it (``legend_view.rs::held_view``); an empty value is a cleared view.
    """
    param = f"{LEGEND_VIEW_PREFIX}{chart_id}"
    variables = current_board_variables()
    value = variables.get(param) if variables is not None else None
    if value is None or value == "" or value == []:
        return None
    return param, json.dumps(value, separators=(",", ":"), ensure_ascii=False)


class LinkedOptions(TypedDict):
    """A choice variable's options, for a chart whose link sets it."""

    # As authored or queried: the engine prints each the way a row's cell prints.
    options: list[Any]  # type-state: explicit_any — raw option cells
    multiple: bool


_VARIABLE_OPTIONS: ContextVar[Mapping[str, LinkedOptions]] = ContextVar(
    "dct_variable_options", default=MappingProxyType({})
)


@contextmanager
def variable_options(options: Mapping[str, LinkedOptions]) -> Generator[None]:
    """Scope the options of the variables chart links set, for one render pass.

    A linked legend draws every option of its variable, not only the series
    the filtered query returned.
    """
    token = _VARIABLE_OPTIONS.set(MappingProxyType(dict(options)))
    try:
        yield
    finally:
        _VARIABLE_OPTIONS.reset(token)


def current_variable_options() -> Mapping[str, LinkedOptions]:
    """The options of the variables chart links set, for the render pass in progress."""
    return _VARIABLE_OPTIONS.get()


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


# A chart's legend view lives in the variable `_dct_legend_<chart-id>`; the engine
# (`legend_view.rs`) reads it, and a board's Reset clears it.
LEGEND_VIEW_PREFIX = "_dct_legend_"

# The names the caller set, as opposed to defaults the render merged in: a
# strip's Reset has something to undo only when one of its filters or a chart's
# legend view is here.
_OVERRIDDEN_VARIABLES: ContextVar[frozenset[str]] = ContextVar(
    "dct_overridden_variables", default=frozenset()
)


@contextmanager
def overridden_variables(names: frozenset[str]) -> Generator[None]:
    """Scope the variable names the caller supplied values for."""
    token = _OVERRIDDEN_VARIABLES.set(names)
    try:
        yield
    finally:
        _OVERRIDDEN_VARIABLES.reset(token)


def current_overridden_variables() -> frozenset[str]:
    """Variable names the caller set, for the render in progress."""
    return _OVERRIDDEN_VARIABLES.get()


def committed_text(
    value: object,  # type-state: object_annotation — any variable value; VariableValues is dict[str, Any]
) -> str:
    """A variable value as a host commits it, never a Python repr.

    ``str()`` on a list yields ``['a', 'b']``, which no host parses back; lists
    go out as JSON, dates as ISO.
    """
    if isinstance(value, (list, tuple)):
        return json.dumps(
            [
                member.isoformat() if isinstance(member, datetime.date) else member
                for member in value
            ]
        )
    return str(value)
