"""Rewrite a chart's column references to its query result's own spelling.

Every field that names a result column carries ``ResultColumn`` in its type
(``ColumnRef``); this walks the chart by those types and replaces each name
with the column ``match_column`` picks. A name matching no column stays as
authored, so the reference's own not-found error still reports it. After this
runs, every reference equals a result column exactly, which is what resolve,
render, and the engine compare against.
"""

from __future__ import annotations

import functools
import re
import types
import typing
from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel

from dbt_charts.core.compile.column_match import match_column
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.markers import ResultColumn, ResultQuery
from dbt_charts.core.diagnostics.codes_compile import ERR_COLUMN_REF_DUPLICATE

_C = TypeVar("_C", bound=Chart)
# The walk reads pydantic type forms (classes, unions, Annotated) and the field
# values they type, neither of which has a narrower static type.
_TypeForm = Any  # type-state: explicit_any — a pydantic type form
_Value = Any  # type-state: explicit_any — a field value of any model
_M = TypeVar("_M", bound=BaseModel)

# The leading name of a `{{ name }}` / `{{ name | filter }}` placeholder.
_PLACEHOLDER = re.compile(r"(\{\{\s*)([A-Za-z_][A-Za-z0-9_]*)")


def match_column_refs(chart: _C, columns: Mapping[str, Sequence[str]]) -> _C:
    """The chart with every column reference spelled as its result spells it.

    ``columns`` holds each known query's result columns by query name; a
    reference reads its layer's query when the layer names one, else the
    chart's. A query absent from ``columns``, or with no columns (zero rows),
    leaves its references as authored.
    """
    if chart.query_name is None:
        return chart
    walk = _Walk(columns, f"chart {chart.id!r}")
    return walk.model(chart, chart.query_name, "", False)


class _Walk:
    def __init__(self, columns: Mapping[str, Sequence[str]], chart: str) -> None:
        self.columns = columns
        self.chart = chart

    def model(self, model: _M, scope: str, path: str, template: bool) -> _M:
        fields = _ref_fields(type(model))
        if not fields:
            return model
        for name in _scope_fields(type(model)):
            value = getattr(model, name)
            if isinstance(value, str):
                scope = value
        update: dict[str, _Value] = {}
        for name, tp in fields:
            value = getattr(model, name)
            new = self.value(value, tp, scope, f"{path}{name}", template)
            if new is not value:
                update[name] = new
        return model.model_copy(update=update) if update else model

    def value(
        self,
        value: _Value,
        tp: _TypeForm,
        scope: str,
        path: str,
        template: bool,
    ) -> _Value:
        if value is None:
            return value
        origin = typing.get_origin(tp)
        if origin is typing.Annotated:
            base, *meta = typing.get_args(tp)
            marker = next((m for m in meta if isinstance(m, ResultColumn)), None)
            if marker is not None:
                template = template or marker.template
                if isinstance(value, str):
                    return self.rename(value, scope, path, template)
            return self.value(value, base, scope, path, template)
        if origin in (typing.Union, types.UnionType):
            arm = next((a for a in typing.get_args(tp) if _accepts(a, value)), None)
            return (
                value if arm is None else self.value(value, arm, scope, path, template)
            )
        if isinstance(value, BaseModel):
            return self.model(value, scope, f"{path}.", template)
        if isinstance(value, str):
            return self.rename(value, scope, path, True) if template else value
        if isinstance(value, list | tuple) and typing.get_args(tp):
            item_tp = typing.get_args(tp)[0]
            items = [
                self.value(item, item_tp, scope, f"{path}[{i}]", template)
                for i, item in enumerate(value)
            ]
            if all(new is old for new, old in zip(items, value, strict=True)):
                return value
            return type(value)(items)
        if isinstance(value, dict) and len(typing.get_args(tp)) == 2:
            return self.mapping(value, tp, scope, path, template)
        return value

    def mapping(
        self,
        value: dict[_Value, _Value],
        tp: _TypeForm,
        scope: str,
        path: str,
        template: bool,
    ) -> dict[_Value, _Value]:
        key_tp, item_tp = typing.get_args(tp)
        scoped = _carries(key_tp, ResultQuery)
        out: dict[_Value, _Value] = {}
        authored: dict[_Value, _Value] = {}
        changed = False
        for key, item in value.items():
            new_key = self.value(key, key_tp, scope, f"{path}.{key}", template)
            item_scope = key if scoped and isinstance(key, str) else scope
            new_item = self.value(item, item_tp, item_scope, f"{path}.{key}", template)
            if new_key in out:
                raise CompilationError.from_code(
                    ERR_COLUMN_REF_DUPLICATE,
                    where=f"{self.chart} {path}",
                    first=authored[new_key],
                    second=key,
                    column=new_key,
                )
            out[new_key] = new_item
            authored[new_key] = key
            changed |= new_key is not key or new_item is not item
        return out if changed else value

    def rename(self, ref: str, scope: str, path: str, template: bool) -> str:
        available = self.columns.get(scope)
        if not available:
            return ref
        where = f"{self.chart} {path}"
        if template:
            return _PLACEHOLDER.sub(
                lambda m: (
                    m.group(1)
                    + (match_column(m.group(2), available, where) or m.group(2))
                ),
                ref,
            )
        return match_column(ref, available, where) or ref


def _accepts(
    tp: _TypeForm,
    value: _Value,
) -> bool:
    origin = typing.get_origin(tp)
    if origin is typing.Annotated:
        return _accepts(typing.get_args(tp)[0], value)
    if origin is typing.Literal:
        return value in typing.get_args(tp)
    if origin in (typing.Union, types.UnionType):
        return any(_accepts(arm, value) for arm in typing.get_args(tp))
    if origin is not None:
        return isinstance(value, origin)
    return isinstance(tp, type) and isinstance(value, tp)


def _carries(
    tp: _TypeForm,
    marker: type,
) -> bool:
    """Whether ``marker`` annotates ``tp`` or any type it is built from."""
    if typing.get_origin(tp) is typing.Annotated and any(
        isinstance(m, marker) for m in tp.__metadata__
    ):
        return True
    return any(_carries(arg, marker) for arg in typing.get_args(tp))


def _field_type(model: type[BaseModel], name: str) -> _TypeForm:
    info = model.model_fields[name]
    if not info.metadata:
        return info.annotation
    return typing.Annotated[info.annotation, *info.metadata]


@functools.cache
def _scope_fields(model: type[BaseModel]) -> tuple[str, ...]:
    return tuple(
        name
        for name in model.model_fields
        if _carries(_field_type(model, name), ResultQuery)
    )


@functools.cache
def _ref_fields(
    model: type[BaseModel],
) -> tuple[tuple[str, _TypeForm], ...]:
    """The fields of ``model`` that hold a column reference at any depth."""
    return tuple(
        (name, tp)
        for name in model.model_fields
        if _reads_columns(tp := _field_type(model, name), frozenset({model}))
    )


def _reads_columns(
    tp: _TypeForm,
    seen: frozenset[type[BaseModel]],
) -> bool:
    if _carries(tp, ResultColumn):
        return True
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        if tp in seen:
            return False
        return any(
            _reads_columns(_field_type(tp, name), seen | {tp})
            for name in tp.model_fields
        )
    return any(_reads_columns(arg, seen) for arg in typing.get_args(tp))
