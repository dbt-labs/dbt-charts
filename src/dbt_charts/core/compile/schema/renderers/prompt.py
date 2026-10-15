"""Prompt renderer — Layer 2 of the two-layer schema IR.

Converts an AuthorableSchema IR to a concise text reference for AI prompts.
Replaces the hand-written schema.get_schema_for_prompt().

Entry point: render_prompt(schema: AuthorableSchema) -> str
"""

from __future__ import annotations

import re
from typing import get_args

from dbt_charts.core.compile.models.markers import ExplicitTag
from dbt_charts.core.compile.models.source import SourceConfig
from dbt_charts.core.compile.schema.introspection import (
    UNION_ALIAS_VARIANT_NAMES,
    AuthorableSchema,
    SchemaField,
    schema_path_to_yaml,
)
from dbt_charts.core.compile.schema.renderers.json_schema import (
    sibling_container_tokens,
)

# Map from relative style path (after "Style.") to model class name.
# Built once per render_prompt call and threaded through _render_model.
_StylePathMap = dict[str, str]

# Source connector configs are leaf models with no cross-references; emit them last.
# Derived from the SourceConfig union — no manual maintenance needed.
_SOURCE_CONFIG_NAMES = [m.__name__ for m in get_args(SourceConfig)]


def _display_name(class_name: str) -> str:
    """Strip 'Authored' prefix and 'Patch' suffix for user-facing model headings."""
    name = class_name
    if name.startswith("Authored"):
        name = name[len("Authored") :]
    if name.endswith("Patch"):
        name = name[: -len("Patch")]
    return name


def _bfs(
    start: str, schema: AuthorableSchema, seen: set[str], order: list[str]
) -> None:
    queue = [start]
    while queue:
        name = queue.pop(0)
        if name in seen or name not in schema.models:
            continue
        seen.add(name)
        order.append(name)
        model = schema.models[name]
        for field in model.fields:
            for nested in field.nested_models:
                # An opaque union alias (e.g. "AuthoredChart") is pre-seeded
                # into `seen` below so it never gets its own heading — checked
                # first, unconditionally, so that pre-seeding does not also
                # block descending into the family it names.
                variant_names = UNION_ALIAS_VARIANT_NAMES.get(nested)
                if variant_names is not None:
                    for class_name in variant_names:
                        if class_name not in seen:
                            queue.append(class_name)
                elif nested not in seen:
                    queue.append(nested)


def _ordered_model_names(schema: AuthorableSchema) -> list[str]:
    """Return model names in a sensible top-down order.

    BFS from AuthoredBoard. Source connector configs are pre-seeded so they
    don't interrupt the Variable → VariableOptions / Chart → style reading
    flow; they appear grouped at the end instead.

    Excluded from the reference:
    - AuthoredChart: synthetic union — per-family charts are the authored surface.
    - AuthoredQuery: synthetic union — per-type queries are the authored surface.
    - Base models when a Patch counterpart exists: the Patch is the authored
      overlay (all-Optional); the base is the compiled form and would produce
      a duplicate heading and anchor.
    """
    order: list[str] = []
    seen: set[str] = set()

    # Base models that have a Patch version: only the Patch appears in the reference.
    patch_bases = {
        name[: -len("Patch")] for name in schema.models if name.endswith("Patch")
    }
    excluded = {"AuthoredChart", "AuthoredQuery"} | (patch_bases & schema.models.keys())

    source_config_set = set(_SOURCE_CONFIG_NAMES)
    seen.update(excluded & schema.models.keys())
    seen.update(source_config_set & schema.models.keys())

    _bfs(schema.root, schema, seen, order)

    for name in _SOURCE_CONFIG_NAMES:
        if name in schema.models:
            order.append(name)

    # Extras added by introspect() that are not reachable from AuthoredBoard via
    # type annotations (e.g. future entries in the introspect() extras loop).
    for name in schema.models:
        if name not in seen and name not in set(order):
            order.append(name)

    return order


_ENUM_CELL_TRUNCATE_AT = (
    20  # Variable.input (14) is the largest pre-existing enum — stays whole.
)


def _enum_str(values: list[str | int | bool]) -> str:
    """ "enum: ..." cell text, truncated past _ENUM_CELL_TRUNCATE_AT members.

    A long closed set (e.g. ScaleTargetConfig.palette's ~116 names) blown out
    inline makes this doc's row unreadable and (the field reference is served
    as an MCP resource) needlessly bloats agent context — the full
    machine-readable list lives in the JSON Schema, where completion actually
    reads it; the docs table only needs enough to show the shape.
    """
    if len(values) == 1:
        return f"const: {_enum_member(values[0])}"
    if len(values) <= _ENUM_CELL_TRUNCATE_AT:
        return "enum: " + ", ".join(_enum_member(v) for v in values)
    shown = ", ".join(_enum_member(v) for v in values[:_ENUM_CELL_TRUNCATE_AT])
    return f"enum: {shown}, … ({len(values) - _ENUM_CELL_TRUNCATE_AT} more; see the JSON Schema)"


def _enum_member(value: str | int | bool) -> str:
    # Bool and int literals are authored bare in YAML, not quoted strings.
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    return f'"{value}"'


def _family_display_names(model_name: str, schema: AuthorableSchema) -> list[str]:
    """Display names a model arm links to: its own, or a union's variants."""
    model = schema.models.get(model_name)
    if model is not None and model.union is not None:
        return [_display_name(n) for n in dict.fromkeys(model.union.variants.values())]
    return [_display_name(model_name)]


def _type_cell(field: SchemaField, schema: AuthorableSchema) -> str:
    # field.container means the WHOLE field is a single list[...]/dict[...]
    # (e.g. dict[str, PaletteName | str]) — type_repr below already spells
    # that out correctly (dict[str, one of: ... | str]), so the "enum: ..."
    # prefix format below is for the top-level-scalar-enum case only.
    if field.enum_values is not None and field.container is None:
        enum_str = _enum_str(field.enum_values)
        # Model arms belong here too. `extra_union_types` skips authored models
        # on purpose, so a field that is both — `format: FormatAlias | str |
        # FormatConfig | None` — used to lose its `FormatConfig` link the moment
        # it gained an enum arm, orphaning that section and contradicting the
        # JSON Schema, which keeps the `$ref` beside the enum.
        extras = [
            *field.extra_union_types,
            *sibling_container_tokens(field.type_repr),
            *(
                f"[{display}](#{_anchor(display)})"
                for name in field.nested_models
                for display in _family_display_names(name, schema)
            ),
        ]
        if extras:
            return " \\| ".join(dict.fromkeys(extras)) + " \\| " + enum_str
        return enum_str
    t = re.sub(r"\s*\|\s*None\b", "", field.type_repr)
    t = re.sub(r"\bNone\s*\|\s*", "", t)
    for model_name in field.nested_models:
        # For opaque union aliases, link to each distinct family class instead.
        union_model = schema.models.get(model_name)
        if union_model is not None and union_model.union is not None:
            family_names = list(dict.fromkeys(union_model.union.variants.values()))
            if re.search(r"\b" + re.escape(model_name) + r"\b", t):
                # type_repr collapsed the union to the opaque alias name
                # (e.g. "AuthoredChart"), so replace it with the family links.
                links = " \\| ".join(
                    f"[{_display_name(n)}](#{_anchor(_display_name(n))})"
                    for n in family_names
                )
                t = re.sub(r"\b" + re.escape(model_name) + r"\b", links, t)
            else:
                # type_repr already spelled out the family class names
                # verbatim instead of the alias, so link each one directly.
                for class_name in family_names:
                    display = _display_name(class_name)
                    t = re.sub(
                        r"\b" + re.escape(class_name) + r"\b",
                        f"[{display}](#{_anchor(display)})",
                        t,
                    )
            continue
        display = _display_name(model_name)
        # Use word-boundary replacement to avoid matching model_name as a substring
        # of a longer class name (e.g. "BarChart" inside "SparkBarChart").
        t = re.sub(
            r"\b" + re.escape(model_name) + r"\b",
            f"[{display}](#{_anchor(display)})",
            t,
        )
    return t.replace("|", "\\|")


def _anchor(display_name: str) -> str:
    return display_name.lower()


def _build_style_path_map(schema: AuthorableSchema) -> _StylePathMap:
    """Walk the Style model tree and map relative paths to model class names.

    ``""`` maps to the Style root itself; ``"charts"`` → ``"ChartsStylePatch"``;
    ``"charts.axis"`` → ``"BaseAxisStylePatch"``.  Used to resolve the *containing*
    section for fallback links — both InheritSlot and leaf Inherit link to the
    section where the fallback field lives, not to the type model it resolves to.
    """
    result: _StylePathMap = {}
    seen: set[str] = set()

    def _walk(model_name: str, prefix: str) -> None:
        if model_name in seen:
            return
        seen.add(model_name)
        model = schema.models.get(model_name)
        if model is None:
            return
        for field in model.fields:
            path = f"{prefix}.{field.name}" if prefix else field.name
            for nested_name in field.nested_models:
                result[path] = nested_name
                _walk(nested_name, path)

    style_root = next((n for n in schema.models if _display_name(n) == "Style"), None)
    if style_root:
        result[""] = style_root  # root itself — for paths like "Style.font"
        _walk(style_root, "")
    return result


def _fallback_link(
    field: SchemaField,
    abs_path: str,
    path_map: _StylePathMap,
    current_model: str,
) -> str | None:
    """Return a markdown link or plain-code string for an inherit fallback path.

    Both InheritSlot and leaf Inherit link to the *container section* that holds
    the fallback field — the place the user goes to set the shared default — not
    to the type model the field resolves to (the type column already links there).

    Return values:
    - ``None``: suppress entirely.  Only for leaf self-references where the target
      field is in the same section as the source (e.g. ``ChartsStyle.aspect_ratio``
      inheriting from ``style.charts.aspect_ratio`` — same table, same field).
    - Link string ``"[`path`](#anchor)"``: target is in a different section.
    - Plain code string `` "`path`" ``: target is in the *same* section but a
      different field (e.g. ``ChartsStyle.axis_x`` → ``style.charts.axis`` — both
      in ChartsStyle).  Still informative; no link needed since you are already there.
    """
    user_path = schema_path_to_yaml(abs_path)
    rel = abs_path[abs_path.index(".") + 1 :]  # drop "Style." prefix
    # Both slot and leaf: resolve the *parent container* of the fallback path.
    parent_rel = rel.rsplit(".", 1)[0] if "." in rel else ""
    parent_model = path_map.get(parent_rel)
    if parent_model is None:
        return f"`{user_path}`"
    if parent_model == current_model:
        if field.inherit_slot:
            # Same section, different field — still worth showing, just not linkable.
            return f"`{user_path}`"
        else:
            # Leaf self-reference — suppress entirely; the note adds no information.
            return None
    anchor = _anchor(_display_name(parent_model))
    return f"[`{user_path}`](#{anchor})"


def _render_model(name: str, schema: AuthorableSchema, path_map: _StylePathMap) -> str:
    model = schema.models[name]
    display = _display_name(name)
    lines = [f'<a id="{_anchor(display)}"></a>', f"## {display}"]
    if model.doc:
        lines.append(model.doc)
    lines.append("")

    required = [f for f in model.fields if f.required]
    optional = [f for f in model.fields if not f.required]

    def field_row(field: SchemaField) -> str:
        desc = (
            (field.description or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("|", "\\|")
        )
        # A single-value literal tag says nothing its const cell doesn't —
        # unless the field is marked ExplicitTag (a tag the normalizer never
        # infers, so its description is the author's only warning).
        if (
            field.enum_values is not None
            and len(field.enum_values) == 1
            and field.name == "type"
            and not any(isinstance(f, ExplicitTag) for f in field.facets)
        ):
            desc = ""
        if field.inherit_slot:
            link = _fallback_link(field, field.inherit_slot, path_map, name)
            if link is not None:
                desc += f" Unset fields fall back to {link}."
        elif field.inherit_from:
            link = _fallback_link(field, field.inherit_from[0], path_map, name)
            if link is not None:
                desc += f" Falls back to {link}."
        return f"| `{field.name}` | {_type_cell(field, schema)} | {desc} |"

    def table(fields: list[SchemaField]) -> list[str]:
        return [
            "| Field | Type | Description |",
            "|-------|------|-------------|",
            *(field_row(f) for f in fields),
        ]

    # Any required field gets the **Required** label — the header legend
    # promises that an unlabeled table means every field is optional.
    if required and optional:
        lines += ["**Required**", "", *table(required), "", "**Optional**", ""]
        lines += table(optional)
    elif required:
        lines += ["**Required**", "", *table(required)]
    else:
        lines += table(model.fields)

    return "\n".join(lines)


def render_prompt(schema: AuthorableSchema) -> str:
    """Render an AuthorableSchema IR as a YAML schema reference table.

    Suitable for inclusion in AI prompts. Each authored model is rendered as a
    markdown table with required fields first, then optional fields.
    Models are ordered top-down: BFS from AuthoredBoard, then source connector
    configs grouped at the end.
    """
    order = _ordered_model_names(schema)
    path_map = _build_style_path_map(schema)
    parts = [
        "# dbt Charts YAML Schema Reference",
        "",
        "Fields are optional unless they appear under a **Required** label.",
        "",
    ]
    for i, name in enumerate(order):
        if i > 0:
            parts.append("")
        parts.append(_render_model(name, schema, path_map))
    return "\n".join(parts)


def render_project_config(schema: AuthorableSchema) -> str:
    """Render the project-config IR (`dbt_charts.yml`) as a reference section.

    Empty style path map: project config has no inherit chain to link into.
    """
    parts = [
        "# dbt charts Project Config Reference: execution settings",
        "",
        "Settings authored under `execution:` in `dbt_charts.yml` at the "
        "project root, not in a board. The file's other sections (`sources:`, "
        "`server:`, `cache:`, ...) are not generated here yet.",
        "",
    ]
    for i, name in enumerate(_ordered_model_names(schema)):
        if i > 0:
            parts.append("")
        parts.append(_render_model(name, schema, {}))
    return "\n".join(parts)
