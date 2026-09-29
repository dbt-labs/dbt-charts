"""InheritGraph: machine-readable map of style field direct parent links.

Usage::

    links = build_inherit_graph(Style)
    # {"Style.charts.axis_x.labels.font.color": "Style.charts.axis.labels.font.color", ...}

    chains = flatten_inherit_chains(links)
    # {"Style.charts.axis_x.labels.font.color": ("Style.charts.axis.labels.font.color",), ...}

Production entry point::

    from dbt_charts.core.compile.resolve.style.inherit_graph import get_inherit_graph
    links = get_inherit_graph()  # cached; uses the production Style model

Committed artifact::

    dbt-charts/src/dbt_charts/core/compile/resolve/style/inherit_registry.yaml

Regenerate after adding or removing Inherit / InheritSlot markers::

    just generate-inherit-registry

A drift test (test_inherit_registry.py::test_inherit_registry_matches_graph) fails
when the committed file diverges from regeneration.
"""

from __future__ import annotations

import functools
import typing
from typing import Any

from pydantic import BaseModel

from dbt_charts.core.compile.models.markers import (
    Inherit,
    InheritSlot,
    SkipInheritSlots,
)

InheritGraph = dict[str, str]

# Absolute dot-path as a tuple of segments, e.g. ("charts", "axis", "label").
StylePath = tuple[str, ...]


def _path_str(path: StylePath) -> str:
    return ".".join(path)


def _unwrap_model(annotation: Any) -> type[BaseModel] | None:
    """Return the sole BaseModel subclass from annotation, or None.

    Handles ``T``, ``T | None``, and ``Optional[T]``.  Returns None when the
    annotation is a leaf type or a union of more than one non-None type (e.g.
    ``str | SomeModel | None`` is treated as a leaf to avoid spurious recursion).
    """
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    args = typing.get_args(annotation)
    if not args:
        return None
    non_none = [a for a in args if a is not type(None)]
    if (
        len(non_none) == 1
        and isinstance(non_none[0], type)
        and issubclass(non_none[0], BaseModel)
    ):
        return non_none[0]
    return None


def _collect_paths(
    model_cls: type[BaseModel],
    prefix: StylePath,
    seen: frozenset[type],
) -> set[str]:
    """Recursively collect all leaf dot-paths reachable from *model_cls*."""
    if model_cls in seen:
        return set()
    seen = seen | {model_cls}
    paths: set[str] = set()
    for field_name, field_info in model_cls.model_fields.items():
        current: StylePath = prefix + (field_name,)
        nested = _unwrap_model(field_info.annotation)
        if nested is not None:
            paths.update(_collect_paths(nested, current, seen))
        else:
            paths.add(_path_str(current))
    return paths


def _build_graph(
    model_cls: type[BaseModel],
    prefix: StylePath,
    seen: frozenset[type],
    valid_paths: set[str],
    graph: InheritGraph,
    *,
    slot_at: StylePath | None = None,
    slot_from: StylePath | None = None,
    skip_slots: bool = False,
    slot_exclude: frozenset[str] = frozenset(),
) -> None:
    """Walk *model_cls* recursively and populate *graph* from Inherit/InheritSlot markers.

    ``skip_slots=True`` suppresses InheritSlot expansion for the entire subtree —
    used when a shared model's slot annotations must not apply at a particular path.

    ``slot_exclude`` carries the active slot's ``InheritSlot.exclude`` leaf names
    down through the recursion; a leaf whose field name is in this set gets no
    graph entry, leaving it a genuine cascade-managed sentinel.
    """
    if model_cls in seen:
        return
    seen = seen | {model_cls}

    for field_name, field_info in model_cls.model_fields.items():
        current: StylePath = prefix + (field_name,)
        metadata = field_info.metadata or []

        inherit_marker: Inherit | None = next(
            (m for m in metadata if isinstance(m, Inherit)), None
        )
        slot_marker: InheritSlot | None = next(
            (m for m in metadata if isinstance(m, InheritSlot)), None
        )
        skip_marker: SkipInheritSlots | None = next(
            (m for m in metadata if isinstance(m, SkipInheritSlots)), None
        )

        nested = _unwrap_model(field_info.annotation)

        if nested is not None:
            if skip_marker is not None:
                # SkipInheritSlots suppresses slot expansion for this field.
                # cascade=True: emit a container-level link so apply_inherit can
                # copy the whole object when the child is None.  Leaf writes
                # through a None intermediate are not viable (_set skips them).
                if (
                    skip_marker.cascade
                    and slot_at is not None
                    and slot_from is not None
                    and not skip_slots
                ):
                    suffix: StylePath = current[len(slot_at) :]
                    fallback_path = _path_str(slot_from + suffix)
                    graph[_path_str(current)] = fallback_path
                # Recurse into subtree.
                # cascade=True inside a slot: keep the outer slot context so
                # that individual leaf fields inside the subtree also get
                # slot-derived links (e.g. charts.line.marks.line.stroke.width
                # → charts.marks.line.stroke.width).  The container link above
                # handles the stroke=None case; leaf links handle the case where
                # the container is set but individual fields are None.  Inner
                # InheritSlot markers with an absolute from_path still fire
                # because skip_slots=False.
                # cascade=True outside a slot (canonical path): both slot args
                # are None already, so this is equivalent to clearing them.
                # cascade=False: set skip_slots=True to fully suppress inner
                # slot expansion (original SkipInheritSlots semantics).
                _build_graph(
                    nested,
                    current,
                    seen,
                    valid_paths,
                    graph,
                    slot_at=slot_at if skip_marker.cascade else None,
                    slot_from=slot_from if skip_marker.cascade else None,
                    skip_slots=not skip_marker.cascade,
                    slot_exclude=slot_exclude if skip_marker.cascade else frozenset(),
                )
            elif slot_marker is not None and not skip_slots:
                if slot_at is not None and slot_from is not None:
                    # Already inside an outer slot expansion.  The inner InheritSlot
                    # applies at the canonical source path, not at this derived-axis
                    # path.  Preserve the outer slot so leaves still expand as
                    # axis_x.labels.font.color → axis.labels.font.color rather than
                    # jumping directly to charts.font.color.
                    #
                    # Also emit a container-level copy link at the outer slot's own
                    # derived path (same mechanism as SkipInheritSlots(cascade=True)
                    # below) — unconditionally, not gated on this field's own
                    # (non-patch) annotation being nullable: the *outer* slot is what
                    # makes this position a sparse patch at runtime, regardless of
                    # whether the compiled type itself declares the field Optional.
                    # Without this, a patch that sets some sibling field and leaves
                    # this whole nested container unset can't be filled — leaf links
                    # alone can't write through a None intermediate.
                    outer_suffix: StylePath = current[len(slot_at) :]
                    fallback_path = _path_str(slot_from + outer_suffix)
                    graph[_path_str(current)] = fallback_path
                    _build_graph(
                        nested,
                        current,
                        seen,
                        valid_paths,
                        graph,
                        slot_at=slot_at,
                        slot_from=slot_from,
                        slot_exclude=slot_exclude,
                    )
                else:
                    from_path = slot_marker.from_path
                    from_tuple: StylePath = tuple(from_path.split("."))
                    if not any(p.startswith(from_path + ".") for p in valid_paths):
                        raise ValueError(
                            f"InheritSlot from_path {from_path!r} does not match any path in the model"
                        )
                    unknown_exclude = slot_marker.exclude - nested.model_fields.keys()
                    if unknown_exclude:
                        raise ValueError(
                            f"InheritSlot exclude {sorted(unknown_exclude)!r} on "
                            f"{_path_str(current)!r} not a field of {nested.__name__}"
                        )
                    # Nullable InheritSlot (T | None): emit a container-level copy
                    # link so apply_inherit can copy the whole parent object when
                    # this field is None.  apply_inherit._set() skips writes through
                    # None intermediates, so leaf links alone cannot fill a None
                    # container.  After the copy, leaf links fill any remaining None
                    # sub-fields when the field was partially set.
                    if type(None) in typing.get_args(field_info.annotation):
                        graph[_path_str(current)] = from_path
                    _build_graph(
                        nested,
                        current,
                        seen,
                        valid_paths,
                        graph,
                        slot_at=current,
                        slot_from=from_tuple,
                        slot_exclude=slot_marker.exclude,
                    )
            else:
                _build_graph(
                    nested,
                    current,
                    seen,
                    valid_paths,
                    graph,
                    slot_at=slot_at,
                    slot_from=slot_from,
                    skip_slots=skip_slots,
                    slot_exclude=slot_exclude,
                )
        else:
            # Leaf node.
            # SkipInheritSlots on a leaf scalar blocks slot expansion for that field,
            # mirroring its effect on nested-model fields (see nested branch above).
            #
            # Priority order:
            # 1. Slot-derived link (inside an InheritSlot expansion): points to the
            #    canonical slot position, which may itself carry an Inherit link.
            #    Keeping the slot chain intact lets the canonical position's Inherit
            #    take effect transitively (A→canonical→absolute) rather than
            #    short-circuiting to the absolute target and bypassing the canonical
            #    slot (which authors may override independently).
            # 2. Explicit Inherit: fires at canonical (non-slot) positions only,
            #    establishing the "ultimate fallback" for the entire slot family.
            #
            # A leaf named in the active slot's exclude set gets no link at
            # all: it stays a genuine cascade-managed sentinel (None unless
            # some tier's raw YAML sets it) rather than being backfilled.
            if field_name in slot_exclude:
                pass
            elif slot_at is not None and slot_from is not None and skip_marker is None:
                leaf_suffix: StylePath = current[len(slot_at) :]
                fallback_path = _path_str(slot_from + leaf_suffix)
                if fallback_path not in valid_paths:
                    raise ValueError(
                        f"InheritSlot expansion {fallback_path!r} not found in model paths"
                    )
                graph[_path_str(current)] = fallback_path
            elif inherit_marker is not None:
                p = inherit_marker.from_path
                current_path = _path_str(current)
                if p == current_path:
                    # Self-reference: the same Inherit marker appears on both the
                    # canonical source (e.g. ChartsStyle.aspect_ratio) and the
                    # per-family variant generated by build_patch_model_ext.  Skip
                    # the canonical path; only the family variant gets a graph entry.
                    pass
                elif p not in valid_paths:
                    raise ValueError(
                        f"Inherit from_path {p!r} not found in model paths"
                    )
                else:
                    graph[current_path] = p


def _build_slot_graph(
    model_cls: type[BaseModel],
    prefix: StylePath,
    seen: frozenset[type[BaseModel]],
    graph: InheritGraph,
    *,
    skip_slots: bool = False,
) -> None:
    """Walk *model_cls* and record slot-level links in *graph*.

    Unlike ``_build_graph``, stops at the ``InheritSlot`` boundary and records the
    container path rather than expanding into individual leaf paths.  Used for the
    human-readable ``inherit_registry.yaml``.
    """
    if model_cls in seen:
        return
    seen = seen | {model_cls}

    for field_name, field_info in model_cls.model_fields.items():
        current: StylePath = prefix + (field_name,)
        metadata = field_info.metadata or []

        slot_marker: InheritSlot | None = next(
            (m for m in metadata if isinstance(m, InheritSlot)), None
        )
        skip_marker: SkipInheritSlots | None = next(
            (m for m in metadata if isinstance(m, SkipInheritSlots)), None
        )
        inherit_marker: Inherit | None = next(
            (m for m in metadata if isinstance(m, Inherit)), None
        )

        nested = _unwrap_model(field_info.annotation)

        if nested is not None:
            if skip_marker is not None:
                _build_slot_graph(nested, current, seen, graph, skip_slots=True)
            elif slot_marker is not None and not skip_slots:
                # Record container-level link; stop — leaves are implied by the slot.
                graph[_path_str(current)] = slot_marker.from_path
            else:
                _build_slot_graph(nested, current, seen, graph, skip_slots=skip_slots)
        else:
            # Leaf — only record explicit Inherit markers (no slot expansion).
            if inherit_marker is not None:
                current_path = _path_str(current)
                if inherit_marker.from_path != current_path:
                    # Guard against self-loops: ChartsStyle.aspect_ratio carries the
                    # same Inherit marker as the per-family variant; skip the canonical.
                    graph[current_path] = inherit_marker.from_path


def build_slot_graph(root_model: type[BaseModel]) -> InheritGraph:
    """Build a slot-level InheritGraph for registry documentation.

    Records ``InheritSlot`` container paths instead of expanding to per-leaf links::

        {"Style.charts.font": "Style.font", ...}

    rather than the 7-entry per-leaf expansion that ``build_inherit_graph`` emits.

    Used for ``inherit_registry.yaml``.  ``apply_inherit`` uses
    ``build_inherit_graph`` (per-leaf) for correctness at runtime.
    """
    prefix: StylePath = (root_model.__name__,)
    graph: InheritGraph = {}
    _build_slot_graph(root_model, prefix, frozenset(), graph)
    return graph


def build_inherit_graph(root_model: type[BaseModel]) -> InheritGraph:
    """Build an InheritGraph from *root_model*'s Inherit/InheritSlot annotations.

    Returns a dict mapping each annotated leaf's absolute dot-path to its single
    direct parent dot-path.  All paths are prefixed with the root model class name
    (e.g. ``"Style.charts.font.color"``).  Paths with no annotations are absent.

    Raises ``ValueError`` if any referenced path does not exist in the model.
    """
    prefix: StylePath = (root_model.__name__,)
    valid_paths = _collect_paths(root_model, prefix, frozenset())
    graph: InheritGraph = {}
    _build_graph(root_model, prefix, frozenset(), valid_paths, graph)
    return graph


def flatten_inherit_chains(links: InheritGraph) -> dict[str, tuple[str, ...]]:
    """Expand single-link parent entries into ordered ancestor tuples.

    For each entry in *links*, follows the parent chain and returns all ancestors
    in order (direct parent first).  Terminal nodes that are not themselves entries
    in *links* are included as the final element.

    Example::

        links = {"a.b": "a", "a": "root"}
        flatten_inherit_chains(links)
        # {"a.b": ("a", "root"), "a": ("root",)}
    """
    memo: dict[str, tuple[str, ...]] = {}

    def _chain(path: str) -> tuple[str, ...]:
        if path in memo:
            return memo[path]
        parent = links.get(path)
        if parent is None:
            return ()
        result = (parent,) + _chain(parent)
        memo[path] = result
        return result

    return {path: _chain(path) for path in links}


@functools.cache
def get_inherit_graph() -> InheritGraph:
    """Return the cached InheritGraph for the production Style model."""
    from dbt_charts.core.compile.models.style.theme import Style

    return build_inherit_graph(Style)
