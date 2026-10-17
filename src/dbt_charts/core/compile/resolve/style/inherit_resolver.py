"""Inherit resolver: fill style leaf fields from single-link InheritGraph chains.

apply_inherit fills None-sentinel leaves by walking the single-link
parent chains declared via Inherit/InheritSlot markers on the compiled Style.
It runs after merge_onto_base, replacing the former hard-coded push loop approach.

Not in scope: _build_chart_style_context, resolved_axis_style.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import cache
from typing import Any, TypeVar, get_args

from pydantic import BaseModel, TypeAdapter
from pydantic.fields import FieldInfo

from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.factories import _PatchBase
from dbt_charts.core.compile.models.markers import Absent, AutoUnset, Overlay
from dbt_charts.core.compile.models.style.theme import Style
from dbt_charts.core.compile.resolve.style.inherit_graph import InheritGraph

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_ValueT = TypeVar("_ValueT")
_type_adapter = cache(TypeAdapter)


def defer_base_none_children(
    base: BaseModel, patch: BaseModel, prefix: str
) -> tuple[BaseModel, BaseModel, list[str]]:
    """Split *patch* into a remainder and the writes into empty slots.

    Returns ``(remainder, held, paths)``: *held* carries only the model-valued
    writes whose *base* slot is None, *paths* lists them rooted at *prefix*.
    The caller fills those slots from their inherit parents, then merges *held*.
    """
    values: dict[str, Any] = {}  # type-state: explicit_any — patch field values
    held_values: dict[str, Any] = {}  # type-state: explicit_any — patch field values
    fields_set: set[str] = set()
    held_fields: set[str] = set()
    paths: list[str] = []
    base_fields = type(base).model_fields
    for name in patch.model_fields_set:
        patch_value = getattr(patch, name)
        path = f"{prefix}.{name}"
        if name not in base_fields:
            values[name] = patch_value
            fields_set.add(name)
            continue

        base_value = getattr(base, name)
        if base_value is None and isinstance(patch_value, BaseModel):
            held_values[name] = patch_value
            held_fields.add(name)
            paths.append(path)
            continue
        if isinstance(base_value, BaseModel) and isinstance(patch_value, BaseModel):
            child_rest, child_held, child_paths = defer_base_none_children(
                base_value, patch_value, path
            )
            if child_paths:
                held_values[name] = child_held
                held_fields.add(name)
                paths.extend(child_paths)
            if not child_rest.model_fields_set:
                continue
            patch_value = child_rest
        values[name] = patch_value
        fields_set.add(name)

    patch_type = type(patch)
    return (
        patch_type.model_construct(_fields_set=fields_set, **values),
        patch_type.model_construct(_fields_set=held_fields, **held_values),
        paths,
    )


def _path_depth(path: str) -> int:
    return path.count(".")


def _obj_path(path: str) -> str:
    """Strip the root-class-name prefix for model traversal.

    InheritGraph paths are prefixed with the root model class name
    (e.g. ``"Style.charts.font.color"``). The model instance has no such
    attribute, so drop the first segment before reading or updating it.
    """
    return path[path.index(".") + 1 :]


def apply_inherit(merged: Style, links: InheritGraph) -> Style:
    """Fill None-sentinel leaves in *merged* by following *links* parent chains.

    For every path in *links* whose current value is None, walks the single-link
    parent chain until a non-None value is found.  Each node is resolved at most
    once (memoized), so A→B→C resolves in one pass.

    *links* paths must be prefixed with the root model class name (``"Style."``),
    as returned by ``build_inherit_graph``.

    Returns a new Style with all link-covered paths resolved.  Fields outside
    the links are never touched.
    """
    if not links:
        return merged

    snapshot = merged.model_dump(mode="python")
    path_values = {}
    for path in set(links) | set(links.values()):
        parts = _obj_path(path).split(".")
        current = snapshot.get(parts[0])
        for part in parts[1:]:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(part)
            if current is None:
                break
        path_values[path] = current

    memo = path_values.copy()
    memo.clear()
    updates = path_values.copy()
    updates.clear()
    for path in links:
        if path_values[path] is not None:
            continue

        chain: list[str] = []
        current_path = path
        resolved = path_values[path]
        while current_path not in memo:
            resolved = path_values[current_path]
            if resolved is not None:
                break
            chain.append(current_path)
            parent = links.get(current_path)
            if parent is None:
                break
            current_path = parent
        else:
            resolved = memo[current_path]

        for node in chain:
            memo[node] = resolved
        if resolved is not None:
            updates[path] = resolved

    if not updates:
        return merged

    # Determine effective writes against a temporary value snapshot. Shorter paths
    # land first so a container can satisfy a descendant before its fallback is
    # considered. The typed tree is rebuilt only after that decision, grouping all
    # writes so each affected model node is copied once.
    effective_updates = updates.copy()
    effective_updates.clear()
    for path in sorted(updates, key=_path_depth):
        obj_path = _obj_path(path)
        parts = obj_path.split(".")
        if len(parts) == 1:
            if snapshot.get(parts[0]) is None:
                snapshot[parts[0]] = updates[path]
                effective_updates[obj_path] = updates[path]
            continue

        current = snapshot.get(parts[0])
        for part in parts[1:-1]:
            if not isinstance(current, dict):
                break
            current = current.get(part)
            if current is None:
                break
        if isinstance(current, dict) and current.get(parts[-1]) is None:
            current[parts[-1]] = updates[path]
            effective_updates[obj_path] = updates[path]

    if not effective_updates:
        return merged

    result = _model_copy_paths(merged, effective_updates)

    # Inherited values are already typed, but a leaf update can violate a
    # cross-field invariant on its containing model. Validate the complete value
    # tree without returning the reconstruction, which would erase field-set
    # provenance on untouched patch models.
    Style.model_validate(result.model_dump(mode="python"))
    return result


def _model_copy_paths(model: _ModelT, updates_by_path: dict[str, _ValueT]) -> _ModelT:
    updates_by_field: dict[str, dict[str, _ValueT]] = {}
    for path, update_value in updates_by_path.items():
        field_name, separator, remainder = path.partition(".")
        updates_by_field.setdefault(field_name, {})[remainder if separator else ""] = (
            update_value
        )

    model_updates = {
        field_name: getattr(model, field_name) for field_name in updates_by_field
    }
    for field_name, field_updates in updates_by_field.items():
        field_value = model_updates[field_name]
        if "" in field_updates:
            input_value = field_updates[""]
            field = type(model).model_fields[field_name]
            field_value = _type_adapter(field.rebuild_annotation()).validate_python(
                input_value
            )

        descendant_updates = {
            path: child_value for path, child_value in field_updates.items() if path
        }
        if descendant_updates:
            if not isinstance(field_value, BaseModel):
                raise ValueError(
                    f"Cannot apply inherited fields below non-model path {field_name!r}"
                )
            field_value = _model_copy_paths(field_value, descendant_updates)
        model_updates[field_name] = field_value

    return model.model_copy(update=model_updates)


def _is_sparse(field: FieldInfo) -> bool:
    if any(isinstance(meta, Overlay) for meta in field.metadata):
        return True
    pending = [field.annotation]
    while pending:
        node = pending.pop()
        if isinstance(node, type) and issubclass(node, _PatchBase):
            return True
        pending.extend(get_args(node))
    return False


def _none_paths(model: BaseModel, path: str) -> Iterator[str]:
    for name, field in type(model).model_fields.items():
        if _is_sparse(field):
            continue
        value = getattr(model, name)
        child = f"{path}.{name}"
        if any(isinstance(meta, Absent) for meta in field.metadata):
            continue
        if value is None:
            yield child
        elif isinstance(value, BaseModel):
            yield from _none_paths(value, child)


def assert_style_total(style: Style) -> None:
    """Raise when a style field is ``None`` where a value was required.

    ``None`` is legal on a field carrying ``Absent`` (whose value is authored as
    written and is not walked) and anywhere inside a sparse
    (``*Patch``-typed or ``Overlay``) slot.
    """
    if missing := list(_none_paths(style, "Style")):
        raise unset_fields_error(missing)


def unset_fields_error(paths: list[str]) -> ValueError:
    return ValueError("Style fields left unset:\n" + "\n".join(paths))


def unset_autos(value: _ValueT) -> _ValueT:
    """*value* with every ``AutoUnset`` field that reads ``auto`` set to None.

    Runs once the cascade is done, so the readers of a resolved style see the
    field as the unset state they have always handled.
    """
    if not isinstance(value, BaseModel):
        return value
    updates: dict[str, Any] = {}  # type-state: explicit_any — replacement field values
    for name, field in type(value).model_fields.items():
        child = getattr(value, name)
        if child == "auto" and any(isinstance(m, AutoUnset) for m in field.metadata):
            updates[name] = None
        else:
            unset = unset_autos(child)
            if unset is not child:
                updates[name] = unset
    return value.model_copy(update=updates) if updates else value


def settled(value: _ValueT | None) -> _ValueT:
    """A leaf ``assert_style_total`` has already proven set, narrowed for the type checker."""
    assert value is not None
    return value


def merge_unset(base: _ModelT, patch: BaseModel | None) -> _ModelT:
    """``merge_onto_base`` for a patch that lands after the cascade: an ``auto``
    it writes on a renderer-default leaf resets that leaf to unset."""
    return unset_autos(merge_onto_base(base, patch))
