"""Structural sharing between two resolved style trees."""

from __future__ import annotations

import copy
import dataclasses
from functools import cache
from typing import Any, TypeVar

from pydantic import BaseModel

_T = TypeVar("_T")


def share_unchanged(new: _T, ref: _T) -> _T:
    """*new*, reusing every subtree of *ref* equal to it at the same path.

    Equality is type-strict (``1`` never matches ``True``, ``0.0`` never
    ``-0.0``) and counts ``model_fields_set``, which pydantic's ``==`` ignores
    but ``merge_onto_base`` reads as authored. A node with an unequal
    descendant is rebuilt as a shallow copy of *new* pointing at the shared
    children, so its own ``model_fields_set`` survives.
    """
    kind = type(new)
    if new is ref or kind is not type(ref):
        return new
    if kind in _SCALARS:
        return ref if new == ref else new
    if kind is float:
        return ref if repr(new) == repr(ref) else new
    if isinstance(new, BaseModel):
        assert isinstance(ref, BaseModel)
        same = (
            new.__pydantic_fields_set__ == ref.__pydantic_fields_set__
            and new.__pydantic_extra__ == ref.__pydantic_extra__
            and new.__pydantic_private__ == ref.__pydantic_private__
        )
        new_values = new.__dict__
        ref_values = ref.__dict__
    elif dataclasses.is_dataclass(new):
        names = _dataclass_fields(kind)
        new_values = {name: getattr(new, name) for name in names}
        ref_values = {name: getattr(ref, name) for name in names}
        same = True
    elif kind is list or kind is tuple:
        assert isinstance(new, (list, tuple))
        assert isinstance(ref, (list, tuple))
        if len(new) != len(ref):
            return new
        items = [share_unchanged(n, r) for n, r in zip(new, ref, strict=True)]
        if all(i is r for i, r in zip(items, ref, strict=True)):
            return ref
        if all(i is n for i, n in zip(items, new, strict=True)):
            return new
        return type(new)(items)
    elif kind is dict:
        assert isinstance(new, dict)
        assert isinstance(ref, dict)
        if [(type(k), k) for k in new] != [(type(k), k) for k in ref]:
            return new
        shared = {k: share_unchanged(v, ref[k]) for k, v in new.items()}
        if all(shared[k] is ref[k] for k in shared):
            return ref
        if all(shared[k] is new[k] for k in shared):
            return new
        return type(new)(shared)
    else:
        return ref if new == ref else new

    updates: dict[str, Any] = {}  # type-state: explicit_any — field values
    for name, child in new_values.items():
        ref_child = ref_values[name]
        shared_child = share_unchanged(child, ref_child)
        same = same and shared_child is ref_child
        if shared_child is not child:
            updates[name] = shared_child
    if same:
        return ref
    if not updates:
        return new
    # Not model_copy/replace: model_copy marks updated fields as set, and
    # replace re-runs __init__ validation on values already validated.
    rebuilt = copy.copy(new)
    for name, value in updates.items():
        object.__setattr__(rebuilt, name, value)
    return rebuilt


_SCALARS = frozenset({str, int, bool})


@cache
def _dataclass_fields(kind: type) -> tuple[str, ...]:
    return tuple(field.name for field in dataclasses.fields(kind))
