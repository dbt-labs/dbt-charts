"""YAML tree renderer — full schema as a nested YAML reference document.

Entry point: render_yaml_schema(schema: AuthorableSchema) -> str

Renders two YAML documents separated by ---:
  1. AuthoredBoard — the complete board YAML spec, expanded recursively
  2. Style — the complete style/theme tree

Nested models are inlined. Recursion is cut when a model appears in its own
ancestry path; source connector configs are never expanded (large leaf models
with no shared structure).

Multi-model union fields (anyOf) expand all branches, each preceded by a
# --- ModelName --- comment header.
"""

from __future__ import annotations

from typing import get_args

from dbt_charts.core.compile.models.source import SourceConfig
from dbt_charts.core.compile.schema.introspection import AuthorableSchema, SchemaField

# Source connector configs are large terminal models; stop expansion at them.
_SOURCE_CONFIG_NAMES: frozenset[str] = frozenset(
    m.__name__ for m in get_args(SourceConfig)
)


def _type_str(field: SchemaField) -> str:
    """Type annotation string for a leaf (no nested-model) field."""
    if field.enum_values is not None:
        parts: list[str] = list(field.extra_union_types) + [
            str(v) for v in field.enum_values
        ]
        return " | ".join(parts)
    return field.type_repr.replace("None", "null")


def _render_tree(
    model_name: str,
    schema: AuthorableSchema,
    ancestry: frozenset[str],
    indent: int,
) -> list[str]:
    """Render all fields of model_name as indented YAML lines.

    ancestry tracks models currently open on the call stack so cyclic
    references can be cut before infinite recursion.
    """
    model = schema.models.get(model_name)
    if model is None:
        return [" " * indent + f"# <{model_name}> -- not in schema"]
    new_ancestry = ancestry | {model_name}
    # Discriminated union nodes have no fields of their own; expand each family class.
    if model.union is not None:
        pad = " " * indent
        lines: list[str] = []
        seen_classes: set[str] = set()
        for class_name in model.union.variants.values():
            if class_name in seen_classes:
                continue
            seen_classes.add(class_name)
            lines.append(f"{pad}# --- {class_name} ---")
            lines.extend(_render_tree(class_name, schema, new_ancestry, indent))
        return lines
    lines = []
    for field in model.fields:
        lines.extend(_render_field(field, schema, new_ancestry, indent))
    return lines


def _partition_models(
    field: SchemaField, ancestry: frozenset[str]
) -> tuple[list[str], list[str], list[str]]:
    """Split field.nested_models into (expandable, recursive, stopped) lists."""
    expandable: list[str] = []
    recursive: list[str] = []
    stopped: list[str] = []
    for m in field.nested_models:
        if m in _SOURCE_CONFIG_NAMES:
            stopped.append(m)
        elif m in ancestry:
            recursive.append(m)
        else:
            expandable.append(m)
    return expandable, recursive, stopped


def _render_field(
    field: SchemaField,
    schema: AuthorableSchema,
    ancestry: frozenset[str],
    indent: int,
) -> list[str]:
    pad = " " * indent
    key = field.name

    if not field.nested_models:
        return [f"{pad}{key}: {_type_str(field)}"]

    expandable, recursive, stopped = _partition_models(field, ancestry)
    lines: list[str] = []

    if field.container in ("dict", "dict_of_list"):
        lines.append(f"{pad}{key}:")
        lines.extend(
            _dict_body(field, schema, ancestry, indent, expandable, recursive, stopped)
        )
    elif field.container == "list":
        lines.append(f"{pad}{key}:")
        lines.extend(
            _list_body(field, schema, ancestry, indent, expandable, recursive, stopped)
        )
    else:
        lines.append(f"{pad}{key}:")
        lines.extend(
            _inline_body(
                field, schema, ancestry, indent, expandable, recursive, stopped
            )
        )

    return lines


def _inline_body(
    field: SchemaField,
    schema: AuthorableSchema,
    ancestry: frozenset[str],
    indent: int,
    expandable: list[str],
    recursive: list[str],
    stopped: list[str],
) -> list[str]:
    pad = " " * indent
    lines: list[str] = []

    if len(expandable) == 1 and not recursive and not stopped:
        lines.extend(_render_tree(expandable[0], schema, ancestry, indent + 2))
    else:
        for name in expandable:
            lines.append(f"{pad}  # --- {name} ---")
            lines.extend(_render_tree(name, schema, ancestry, indent + 2))
        for name in recursive:
            lines.append(f"{pad}  # --- {name} (recursive) ---")
        for name in stopped:
            lines.append(f"{pad}  # --- {name} (not expanded) ---")

    if field.extra_union_types:
        lines.append(f"{pad}  # also: {' | '.join(field.extra_union_types)}")
    if field.enum_values:
        lines.append(
            f"{pad}  # also (scalar): {' | '.join(str(v) for v in field.enum_values)}"
        )

    return lines


def _dict_body(
    field: SchemaField,
    schema: AuthorableSchema,
    ancestry: frozenset[str],
    indent: int,
    expandable: list[str],
    recursive: list[str],
    stopped: list[str],
) -> list[str]:
    pad = " " * indent
    lines: list[str] = [f"{pad}  <key>:"]

    if len(expandable) == 1 and not recursive and not stopped:
        lines.extend(_render_tree(expandable[0], schema, ancestry, indent + 4))
    else:
        for name in expandable:
            lines.append(f"{pad}    # --- {name} ---")
            lines.extend(_render_tree(name, schema, ancestry, indent + 4))
        for name in recursive:
            lines.append(f"{pad}    # --- {name} (recursive) ---")
        for name in stopped:
            lines.append(f"{pad}    # --- {name} (not expanded) ---")

    if field.extra_union_types:
        lines.append(f"{pad}    # also: {' | '.join(field.extra_union_types)}")
    if field.enum_values:
        lines.append(
            f"{pad}    # also (scalar): {' | '.join(str(v) for v in field.enum_values)}"
        )

    return lines


def _list_body(
    field: SchemaField,
    schema: AuthorableSchema,
    ancestry: frozenset[str],
    indent: int,
    expandable: list[str],
    recursive: list[str],
    stopped: list[str],
) -> list[str]:
    """Render list items. Each model branch becomes a separate YAML sequence item."""
    pad = " " * indent
    lines: list[str] = []

    if len(expandable) == 1 and not recursive and not stopped:
        inner = _render_tree(expandable[0], schema, ancestry, indent + 4)
        lines.extend(_as_list_item(inner, indent))
    else:
        for name in expandable:
            lines.append(f"{pad}  # --- {name} ---")
            inner = _render_tree(name, schema, ancestry, indent + 4)
            lines.extend(_as_list_item(inner, indent))
        for name in recursive:
            lines.append(f"{pad}  # --- {name} (recursive) ---")
        for name in stopped:
            lines.append(f"{pad}  # --- {name} (not expanded) ---")

    if field.extra_union_types:
        lines.append(f"{pad}  # also: {' | '.join(field.extra_union_types)}")
    if field.enum_values:
        lines.append(
            f"{pad}  # also (scalar): {' | '.join(str(v) for v in field.enum_values)}"
        )

    return lines


def _as_list_item(inner: list[str], indent: int) -> list[str]:
    """Reformat lines rendered at indent+4 as a YAML block-sequence item at indent.

    The first field gets the '- ' prefix; subsequent fields are already at the
    correct indentation for a mapping under a sequence item.
    """
    if not inner:
        return [" " * indent + "  - {}"]
    # inner lines are at indent+4; a list item's '- ' sits at indent+2,
    # and the mapping content starts at indent+4 — so only the first line changes.
    first = inner[0]
    content = first[indent + 4 :]  # strip the indent+4 leading spaces
    result = [" " * indent + "  - " + content]
    result.extend(inner[1:])
    return result


def render_yaml_schema(schema: AuthorableSchema) -> str:
    """Render an AuthorableSchema as a human-readable nested YAML tree.

    Produces two YAML documents (separated by ---):
      1. The root model (AuthoredBoard) — full board YAML spec
      2. Style — full style/theme spec

    No descriptions are included. Data types appear inline. Recursive model
    references are noted as comments rather than expanded again.
    """
    board_lines = _render_tree(schema.root, schema, frozenset(), 0)
    parts: list[str] = ["# Board schema", ""] + board_lines

    style_name = "Style" if "Style" in schema.models else None
    if style_name:
        style_lines = _render_tree(style_name, schema, frozenset(), 0)
        parts += ["", "---", "", "# Style schema", ""] + style_lines

    return "\n".join(parts)
