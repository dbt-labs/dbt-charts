"""Self-check for the shipped board-grammar migration declarations.

Cross-checks every declared ``Move``/``Deletion``/``ConditionalMove`` against
the frozen schemas it names: the transition is adjacent, the source path is
present in the source grammar, and the destination path is present -- or, for
a ``Deletion``, absent -- in the target grammar. A mis-declared migration
rewrites boards that should have been left alone, so it has to be caught
somewhere; see ``MigrationRegistry`` for why that somewhere is here.
"""

from __future__ import annotations

from collections.abc import Iterable

from dbt_charts.core.compile.migrations import (
    ConditionalMove,
    Deletion,
    MigrationError,
    MigrationRegistry,
    Move,
)
from dbt_charts.core.compile.migrations.migrations import (
    JsonObject,
    YamlKeyPath,
    _format_path,
    _resolve_ref,
    _schema_branches,
    _schema_path_exists,
)
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
)


def validate_declarations(registry: MigrationRegistry) -> None:
    """Raise ``MigrationError`` on the first inconsistent declaration."""
    catalog = registry.catalog
    versions = catalog.versions
    positions = {version: index for index, version in enumerate(versions)}
    for move in registry.moves:
        source_index = positions.get(move.source_schema)
        if source_index is None:
            raise MigrationError(
                f"Move {_format_path(move.old_path)!r} → "
                f"{_format_path(move.new_path)!r} references a schema "
                "that is not retained"
            )
        target_index = positions.get(move.target_schema)
        if target_index is None:
            raise MigrationError(
                f"Move {_format_path(move.old_path)!r} → "
                f"{_format_path(move.new_path)!r} references a schema "
                "that is not retained"
            )
        if target_index != source_index - 1:
            raise MigrationError(
                f"Move {_format_path(move.old_path)!r} → "
                f"{_format_path(move.new_path)!r} must target the "
                "immediately succeeding schema"
            )
        target_schema_obj = catalog.schema_for(move.target_schema)
        if not _schema_path_exists(target_schema_obj, move.new_path):
            raise MigrationError(
                f"Move destination path {_format_path(move.new_path)!r} is absent from "
                f"{move.target_schema}"
            )
        if not _schema_path_exists(
            catalog.schema_for(move.source_schema), move.old_path
        ):
            raise MigrationError(
                f"Move source path {_format_path(move.old_path)!r} is absent from "
                f"{move.source_schema}"
            )
        if move.old_path == move.new_path:
            if move.value_map is None:
                raise MigrationError(
                    f"Move {_format_path(move.old_path)!r} has old_path == "
                    "new_path with no value_map; it would rewrite nothing. "
                    "An identity-path Move only makes sense as a value remap "
                    "on a key that survives the transition unrenamed -- give "
                    "it a value_map, or remove the declaration."
                )
        elif _schema_path_exists(target_schema_obj, move.old_path):
            raise MigrationError(
                f"Move source path {_format_path(move.old_path)!r} still exists in "
                f"{move.target_schema!r}; the field was not renamed away in this "
                "transition. Recognition reads a surviving source path as proof "
                "that a document predates the transition, so this would migrate "
                "current documents. (Not checked when old_path == new_path: a "
                "value-only remap on a key that survives unrenamed is exactly "
                "the identity-path shape, not a misfire.)"
            )
    for deletion in registry.deletions:
        source_index = positions.get(deletion.source_schema)
        if source_index is None:
            raise MigrationError(
                f"Deletion at {_format_path(deletion.path)!r} references a schema "
                "that is not retained"
            )
        target_index = positions.get(deletion.target_schema)
        if target_index is None:
            raise MigrationError(
                f"Deletion at {_format_path(deletion.path)!r} references a schema "
                "that is not retained"
            )
        if target_index != source_index - 1:
            raise MigrationError(
                f"Deletion at {_format_path(deletion.path)!r} must target the "
                "immediately succeeding schema"
            )
        target_schema_obj = catalog.schema_for(deletion.target_schema)
        source_schema_obj = catalog.schema_for(deletion.source_schema)
        if not schema_has_tail(source_schema_obj, deletion.path):
            raise MigrationError(
                f"Deletion source path {_format_path(deletion.path)!r} is absent from "
                f"{deletion.source_schema}"
            )
        target_still_has_it = (
            schema_has_tail_for_chart_type(
                target_schema_obj, deletion.path, deletion.chart_type
            )
            if deletion.chart_type is not None
            else schema_has_tail(target_schema_obj, deletion.path)
        )
        # A tail that survives elsewhere can still be genuinely retired at
        # the document root: the board's own style block lost `color` while
        # every chart family kept its own. The anchored walk is the proof,
        # and `_live_declares_tail` confines the firing to the positions
        # that actually lost it. Not an escape a chart-scoped deletion may
        # take -- a chart is never at the root, so the root's own retirement
        # says nothing about the family this one names.
        retired_at_root = (
            deletion.chart_type is None
            and _schema_path_exists(source_schema_obj, deletion.path)
            and not _schema_path_exists(target_schema_obj, deletion.path)
        )
        if target_still_has_it and not retired_at_root:
            scope = (
                f" on chart_type={deletion.chart_type!r}"
                if deletion.chart_type is not None
                else ""
            )
            raise MigrationError(
                f"Deletion path {_format_path(deletion.path)!r} still exists in "
                f"{deletion.target_schema!r}{scope}, at the document root "
                "included; the field was not removed in this transition — use a "
                "Move if the field was renamed, or remove the Deletion if the "
                "field is still valid"
            )
    for cond_move in registry.conditional_moves:
        source_index = positions.get(cond_move.source_schema)
        if source_index is None:
            raise MigrationError(
                f"ConditionalMove for chart_type={cond_move.chart_type!r} "
                f"at {_format_path(cond_move.old_tail)!r} references a schema "
                "that is not retained"
            )
        target_index = positions.get(cond_move.target_schema)
        if target_index is None:
            raise MigrationError(
                f"ConditionalMove for chart_type={cond_move.chart_type!r} "
                f"at {_format_path(cond_move.old_tail)!r} references a schema "
                "that is not retained"
            )
        if target_index != source_index - 1:
            raise MigrationError(
                f"ConditionalMove at {_format_path(cond_move.old_tail)!r} "
                "must target the immediately succeeding schema"
            )
        target_schema_obj = catalog.schema_for(cond_move.target_schema)
        if not schema_has_tail(
            catalog.schema_for(cond_move.source_schema), cond_move.old_tail
        ):
            raise MigrationError(
                f"ConditionalMove source tail {_format_path(cond_move.old_tail)!r} "
                f"is absent from {cond_move.source_schema}"
            )
        if not schema_has_tail(target_schema_obj, cond_move.new_tail):
            raise MigrationError(
                f"ConditionalMove destination tail {_format_path(cond_move.new_tail)!r} "
                f"is absent from {cond_move.target_schema}"
            )


def checked_registry(
    moves: Iterable[Move],
    deletions: Iterable[Deletion] = (),
    conditional_moves: Iterable[ConditionalMove] = (),
    *,
    catalog: YamlSchemaCatalog,
) -> MigrationRegistry:
    """Build a registry and self-check it, the way CI does for the shipped one."""
    registry = MigrationRegistry(moves, deletions, conditional_moves, catalog=catalog)
    validate_declarations(registry)
    return registry


def schema_has_tail(schema: JsonObject, tail: YamlKeyPath) -> bool:
    """True iff tail appears at any position in the JSON schema.

    Walks properties, anyOf, additionalProperties, and items, following $ref
    pointers. The seen-id guard stops cycles including ``$ref: "#"``
    self-references (the board schema's nested-board recursion), so every
    reachable schema node is visited exactly once.  ``schema`` is always the
    top-level root so $ref resolution stays correct when visiting child nodes.
    """
    seen: set[int] = set()

    def tail_exists_from(start: JsonObject) -> bool:
        """True iff tail is a property chain reachable from start."""
        nodes: list[JsonObject] = [start]
        for part in tail:
            next_nodes: list[JsonObject] = []
            for node in nodes:
                for branch in _schema_branches(schema, node):
                    props = branch.get("properties")
                    child = props.get(part) if isinstance(props, dict) else None
                    if isinstance(child, dict):
                        next_nodes.append(child)
            nodes = next_nodes
            if not nodes:
                return False
        return True

    def walk(node: JsonObject) -> bool:
        resolved = _resolve_ref(schema, node)
        rid = id(resolved)
        if rid in seen:
            return False
        seen.add(rid)
        if tail_exists_from(resolved):
            return True
        branches = resolved.get("anyOf")
        if isinstance(branches, list):
            return any(isinstance(b, dict) and walk(b) for b in branches)
        props = resolved.get("properties")
        if isinstance(props, dict):
            for child in props.values():
                if isinstance(child, dict) and walk(child):
                    return True
        add_props = resolved.get("additionalProperties")
        if isinstance(add_props, dict) and walk(add_props):
            return True
        items_node = resolved.get("items")
        return isinstance(items_node, dict) and walk(items_node)

    return walk(schema)


def schema_has_tail_for_chart_type(
    schema: JsonObject, tail: YamlKeyPath, chart_type: str
) -> bool:
    """True iff *tail* is declared on the branch discriminated as *chart_type*.

    ``schema_has_tail`` asks "does this tail exist anywhere in the schema" —
    too coarse for a ``chart_type``-scoped ``Deletion``: once a family loses a
    field but a sibling family (e.g. ``table``/``kpi``) keeps it, the plain
    global check still finds it via the sibling and wrongly reports the
    deletion as not-yet-applied.

    The gate and the tail search must run on the *same single branch* — a
    naive version that gates on "does any branch in this anyOf group declare
    chart_type" and then searches "does any branch in this SAME group have
    the tail" independently is wrong: with bar/table siblings in one anyOf,
    that reports true whenever *either* sibling matches its own half, so a
    bar-scoped deletion reads as unsatisfied forever because table (a
    different branch) still has the field. ``tail_exists_from`` therefore
    starts from one already-chart_type-confirmed branch and only expands
    *that branch's own* sub-structure for the remaining segments, never
    merging back into its unrelated siblings.
    """
    seen: set[int] = set()

    def branch_declares_chart_type(branch: JsonObject) -> bool:
        properties = branch.get("properties")
        if not isinstance(properties, dict):
            return False
        type_schema = properties.get("type")
        if not isinstance(type_schema, dict):
            return False
        enum = type_schema.get("enum")
        return isinstance(enum, list) and chart_type in enum

    def tail_exists_from(start: JsonObject) -> bool:
        nodes: list[JsonObject] = [start]
        for part in tail:
            next_nodes: list[JsonObject] = []
            for node in nodes:
                properties = node.get("properties")
                child = properties.get(part) if isinstance(properties, dict) else None
                if isinstance(child, dict):
                    next_nodes.extend(_schema_branches(schema, child))
            nodes = next_nodes
            if not nodes:
                return False
        return True

    def walk(node: JsonObject) -> bool:
        resolved = _resolve_ref(schema, node)
        rid = id(resolved)
        if rid in seen:
            return False
        seen.add(rid)
        for branch in _schema_branches(schema, resolved):
            if branch_declares_chart_type(branch) and tail_exists_from(branch):
                return True
        branches = resolved.get("anyOf")
        if isinstance(branches, list):
            if any(isinstance(b, dict) and walk(b) for b in branches):
                return True
        props = resolved.get("properties")
        if isinstance(props, dict):
            for child in props.values():
                if isinstance(child, dict) and walk(child):
                    return True
        add_props = resolved.get("additionalProperties")
        if isinstance(add_props, dict) and walk(add_props):
            return True
        items_node = resolved.get("items")
        return isinstance(items_node, dict) and walk(items_node)

    return walk(schema)
