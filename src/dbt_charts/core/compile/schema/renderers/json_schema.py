"""JSON Schema renderer — Layer 2 of the two-layer schema IR.

Converts an AuthorableSchema IR to a JSON Schema 2020-12 dict.
Callers: IDE schema generation, validation tooling.

Entry point: render_yaml_schema(schema: AuthorableSchema) -> dict
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from typing import Any

from pydantic_core import PydanticUndefined

from dbt_charts.core.compile.schema.introspection import (
    AuthorableSchema,
    SchemaField,
    schema_path_to_yaml,
)

_PRIMITIVES: dict[str, dict[str, Any]] = {
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
    "None": {"type": "null"},
}


def _schema_for_token(token: str) -> dict[str, Any]:
    """Map a single non-union type token to a JSON Schema dict."""
    if token in _PRIMITIVES:
        return _PRIMITIVES[token]
    if token.startswith("list["):
        return {"type": "array"}
    if token.startswith("dict["):
        return {"type": "object"}
    return {}


def _split_union(s: str) -> list[str]:
    """Split s on ' | ' separators at bracket depth 0 (not inside [...])."""
    parts: list[str] = []
    depth = 0
    start = 0
    i = 0
    while i < len(s):
        if s[i] == "[":
            depth += 1
        elif s[i] == "]":
            depth -= 1
        elif depth == 0 and s[i : i + 3] == " | ":
            parts.append(s[start:i])
            start = i + 3
            i += 3
            continue
        i += 1
    parts.append(s[start:])
    return parts


def _ref(model_name: str, root: str) -> dict[str, str]:
    """Build a $ref to a model's $defs entry, or to the document root.

    render_yaml_schema inlines the root model's properties at the top level and
    never emits a $defs entry for it. A field that recurses back to the root
    (a nested board) must therefore $ref the document root ("#") rather than a
    "#/$defs/<root>" entry that will never exist.
    """
    if model_name == root:
        return {"$ref": "#"}
    return {"$ref": f"#/$defs/{model_name}"}


def sibling_container_tokens(type_repr: str) -> list[str]:
    """list[...]/dict[...] arm tokens sitting beside an enum or model arm in
    the same union (e.g. `ThemeName | str | list[str]`).

    Shared by this module's _type_schema (enum branch) and prompt.py's
    _type_cell — both need to recover a container sibling arm that
    field.extra_union_types drops (it recurses into a container's item/value
    type instead of reporting the container's own shape, by design for the
    field.container-wrapping case). One canonical extractor per
    `core/AGENTS.md`'s "one canonical mapper per concept" rule; not
    underscore-prefixed since prompt.py imports it across module boundaries.
    """
    base = type_repr.replace(" | None", "").strip()
    return [
        token.strip()
        for token in _split_union(base)
        if token.strip().startswith(("list[", "dict["))
    ]


def _array_schema(
    items: dict[str, Any],  # type-state: explicit_any — see _ref
    min_items: int | None,
) -> dict[str, Any]:  # type-state: explicit_any — see _ref
    """Build an array-shaped JSON Schema fragment, with an optional minItems."""
    schema: dict[str, Any] = {"type": "array"}  # type-state: explicit_any — see _ref
    schema["items"] = items
    if min_items is not None:
        schema["minItems"] = min_items
    return schema


def _primitive_schema(
    name: str, patterns: dict[str, str]
) -> dict[str, Any]:  # type-state: explicit_any — JSON Schema value, see _ref
    """Map a primitive name to its JSON Schema dict, honoring a pattern constraint.

    `_PRIMITIVES[name]` (e.g. {"type": "string"}) is the unconstrained shape;
    `patterns` (SchemaField.extra_union_type_patterns) names the ones that
    carry a regex constraint (VariableOrRef/ChartOrRef's cross-file-ref string
    arm) — copy so mutating the pattern key never touches the shared _PRIMITIVES
    dict other fields read from the same process-lifetime instance.
    """
    schema = dict(_PRIMITIVES[name])
    pattern = patterns.get(name)
    if pattern is not None:
        schema["pattern"] = pattern
    return schema


def _type_schema(field: SchemaField, root: str) -> dict[str, Any]:
    """Build the JSON Schema type object for a single field."""
    nullable = "None" in field.type_repr

    extra = [
        _primitive_schema(t, field.extra_union_type_patterns)
        for t in field.extra_union_types
        if t in _PRIMITIVES
    ]

    if field.tuple_item_reprs is not None:
        # A fixed-length tuple: one type_repr string per position, mapped
        # through the same _map_type_repr each bare list[X]/dict[K,V] field
        # already uses -- prefixItems is the one shape neither field.container
        # nor field.enum_values models (both assume every position shares one
        # type; a tuple's positions don't).
        tuple_schema = {
            "type": "array",
            "prefixItems": [_map_type_repr(r) for r in field.tuple_item_reprs],
            "minItems": len(field.tuple_item_reprs),
            "maxItems": len(field.tuple_item_reprs),
        }
        return {"anyOf": [tuple_schema, {"type": "null"}]} if nullable else tuple_schema

    if field.enum_values is not None:
        # Copy: the IR is a shared memoized instance; emitted schema must not
        # alias its lists.
        enum_item = {"enum": list(field.enum_values)}
        branches: list[dict[str, Any]]
        if field.nested_models:
            # Field accepts both model objects and scalar enum values (e.g. SparkConfig | SparkTypeLiteral).
            model_refs = [_ref(n, root) for n in field.nested_models]
            branches = model_refs + [enum_item] + extra
        else:
            branches = [enum_item] + extra
        # field.container is set only when the whole field is a single
        # list[...]/dict[...] (e.g. dict[str, PaletteName | str]) — wrap the
        # enum+extra branches as that container's items/additionalProperties,
        # mirroring the field.nested_models branch below.
        if field.container in ("list", "dict"):
            enum_inner = branches[0] if len(branches) == 1 else {"anyOf": branches}
            wrapped = (
                {"type": "array", "items": enum_inner}
                if field.container == "list"
                else {"type": "object", "additionalProperties": enum_inner}
            )
            return {"anyOf": [wrapped, {"type": "null"}]} if nullable else wrapped
        # Otherwise a list[...]/dict[...] may still sit as a *sibling* union
        # arm (e.g. `ThemeName | str | list[str]`) — add its bare shape so it
        # isn't dropped from the anyOf. Multiple sibling tokens can map to the
        # same schema (list[str] | list[float] both → {"type": "array"}), so
        # dedup on the mapped shape, not the token.
        for token in sibling_container_tokens(field.type_repr):
            arm = _schema_for_token(token)
            if arm and arm not in branches:
                branches.append(arm)
        if nullable:
            branches.append({"type": "null"})
        return {"anyOf": branches} if len(branches) > 1 else branches[0]

    if field.nested_models:
        refs: list[dict[str, Any]] = [_ref(n, root) for n in field.nested_models]
        branches = refs + extra
        inner: dict[str, Any] = (
            branches[0] if len(branches) == 1 else {"anyOf": branches}
        )
        if field.container == "list":
            if field.container_mapping_models:
                map_refs = [_ref(n, root) for n in field.container_mapping_models]
                if len(map_refs) == 1:
                    item_branches = [
                        inner,
                        {"type": "object", "additionalProperties": map_refs[0]},
                    ]
                else:
                    item_branches = [
                        inner,
                        {
                            "type": "object",
                            "additionalProperties": {"anyOf": map_refs},
                        },
                    ]
                inner = {"anyOf": item_branches}
            arr: dict[str, Any] = {"type": "array", "items": inner}
            parts: list[dict[str, Any]] = [arr]
            if nullable:
                parts.append({"type": "null"})
            return {"anyOf": parts} if len(parts) > 1 else parts[0]
        if field.container == "dict":
            obj: dict[str, Any] = {"type": "object", "additionalProperties": inner}
            parts = [obj]
            if nullable:
                parts.append({"type": "null"})
            return {"anyOf": parts} if len(parts) > 1 else parts[0]
        all_branches: list[dict[str, Any]] = branches
        if field.container_mapping_models:
            # A bare (non-list, non-dict) union can still carry a dict[str, Model]
            # sibling arm -- GridItem.item's `dict[str, AuthoredChart]` form, a
            # chart keyed by name rather than referenced by it. The
            # field.container == "list" branch above renders this same shape as
            # the *items'* additionalProperties; here it's the field's own.
            map_refs = [_ref(n, root) for n in field.container_mapping_models]
            if len(map_refs) == 1:
                all_branches.append(
                    {"type": "object", "additionalProperties": map_refs[0]}
                )
            else:
                all_branches.append(
                    {"type": "object", "additionalProperties": {"anyOf": map_refs}}
                )
        if field.container_list_models:
            # A bare (non-list, non-dict) union can also carry a list[...]
            # sibling arm -- `support_table:`'s `json_schema_input_type`
            # widening (list[ChartSupportTableEntry] | ChartSupportTable), the
            # bare-list shorthand for a whole support_table block. Names can be
            # authored models (rendered as $ref) or primitives (from
            # _PRIMITIVES) since the list item type is itself a union of both.
            item_schemas = [
                _PRIMITIVES[n] if n in _PRIMITIVES else _ref(n, root)
                for n in field.container_list_models
            ]
            item_schema = (
                item_schemas[0] if len(item_schemas) == 1 else {"anyOf": item_schemas}
            )
            all_branches.append(
                _array_schema(item_schema, field.container_list_min_items)
            )
        if nullable:
            all_branches.append({"type": "null"})
        return {"anyOf": all_branches} if len(all_branches) > 1 else all_branches[0]

    return _map_type_repr(field.type_repr)


def _map_type_repr(type_repr: str) -> dict[str, Any]:
    """Map a type_repr string to a JSON Schema type dict."""
    nullable = " | None" in type_repr
    base = type_repr.replace(" | None", "").strip()

    if base in _PRIMITIVES:
        schema = _PRIMITIVES[base]
    elif " | " in base:
        # Bracket-aware split so 'list[X | Y]' stays intact; dedup identical schemas
        # (e.g. list[str] | list[float] both map to {"type": "array"}).
        token_schemas = []
        for token in _split_union(base):
            s = _schema_for_token(token.strip())
            if s and s not in token_schemas:
                token_schemas.append(s)
        schema = {"anyOf": token_schemas} if token_schemas else {}
    elif base.startswith("list["):
        schema = {"type": "array"}
    elif base.startswith("dict["):
        schema = {"type": "object"}
    else:
        schema = {}

    if not nullable or not schema:
        return schema
    if "anyOf" in schema:
        schema = dict(schema)
        schema["anyOf"] = list(schema["anyOf"]) + [{"type": "null"}]
        return schema
    return {"anyOf": [schema, {"type": "null"}]}


def _inherit_note(field: SchemaField) -> str:
    """Return the fallback sentence for a field that carries an inheritance marker."""
    if field.inherit_slot:
        exception = ""
        if field.inherit_slot_exclude:
            names = ", ".join(f"`{n}`" for n in sorted(field.inherit_slot_exclude))
            exception = f" (except {names})"
        return f"Unset fields fall back to `{schema_path_to_yaml(field.inherit_slot)}`{exception}."
    if field.inherit_from:
        return f"Falls back to `{schema_path_to_yaml(field.inherit_from[0])}`."
    return ""


def _union_to_def(model_name: str, ir: AuthorableSchema) -> dict[str, Any]:
    """Build a JSON Schema $defs entry for a union IR model.

    Discriminated unions emit an if/then allOf structure. Structural unions
    emit anyOf refs so each branch retains its own required and forbidden keys.
    """
    model = ir.models[model_name]
    assert model.union is not None  # caller guarantee
    spec = model.union

    if spec.discriminator is None:
        variants = list(dict.fromkeys(spec.variants.values()))
        defn: dict[str, Any] = {
            "anyOf": [_ref(class_name, ir.root) for class_name in variants]
        }
        if model.doc:
            defn["description"] = model.doc
        return defn

    class_to_tags: dict[str, list[str]] = defaultdict(list)
    for tag, class_name in spec.variants.items():
        class_to_tags[class_name].append(tag)

    all_tags = sorted(spec.variants.keys())
    all_of = []
    for class_name, tags in class_to_tags.items():
        all_of.append(
            {
                "if": {
                    "properties": {spec.discriminator: {"enum": sorted(tags)}},
                    "required": [spec.discriminator],
                },
                "then": _ref(class_name, ir.root),
            }
        )

    defn = {
        "type": "object",
        "properties": {
            spec.discriminator: {
                "enum": all_tags,
                "description": f"Chart family selector. One of: {', '.join(all_tags)}.",
            }
        },
        "required": [spec.discriminator],
        "allOf": all_of,
    }
    if model.doc:
        defn["description"] = model.doc
    return defn


def _model_to_def(model_name: str, ir: AuthorableSchema) -> dict[str, Any]:
    """Build a JSON Schema $defs entry for one AuthorableModel."""
    model = ir.models[model_name]
    if model.union is not None:
        return _union_to_def(model_name, ir)

    required = [f.name for f in model.fields if f.required and not f.is_extra_key]
    properties: dict[str, Any] = {}
    additional_properties: dict[str, Any] | None = None
    for field in model.fields:
        prop: dict[str, Any] = _type_schema(field, ir.root)
        note = _inherit_note(field)
        desc = " ".join(filter(None, [field.description, note]))
        if desc:
            prop = dict(prop)
            prop["description"] = desc
        if (
            not field.required
            and field.default is not dataclasses.MISSING
            and field.default is not None
            and field.default is not PydanticUndefined
        ):
            prop = dict(prop)
            prop["default"] = field.default
        if field.is_extra_key:
            additional_properties = prop
        else:
            properties[field.name] = prop

    defn: dict[str, Any] = {"type": "object", "properties": properties}
    if model.doc:
        defn["description"] = model.doc
    if required:
        defn["required"] = required
    if additional_properties is not None:
        defn["additionalProperties"] = additional_properties
    else:
        defn["additionalProperties"] = False
    return defn


def render_yaml_schema(schema: AuthorableSchema) -> dict[str, Any]:
    """Render the strict JSON Schema 2020-12 dbt charts YAML schema.

    Produces a self-contained schema with the root model's properties
    inlined and all reachable authored models in $defs. Author-defined model
    objects are closed; only declared keyed collections use typed additional
    properties.
    """
    root = _model_to_def(schema.root, schema)
    defs = {
        name: _model_to_def(name, schema)
        for name in schema.models
        if name != schema.root
    }

    result: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": schema.root,
    }
    if root.get("description"):
        result["description"] = root["description"]
    result["type"] = "object"
    result["properties"] = root["properties"]
    result["additionalProperties"] = root["additionalProperties"]
    if root.get("required"):
        result["required"] = root["required"]
    if defs:
        result["$defs"] = defs

    return result


render_json_schema = render_yaml_schema
