"""JSON Schema renderer — Layer 2 of the two-layer schema IR.

Converts an AuthorableSchema IR to a JSON Schema 2020-12 dict.
Callers: IDE schema generation, validation tooling.

Entry point: render_yaml_schema(schema: AuthorableSchema) -> dict
"""

from __future__ import annotations

import dataclasses
import itertools
from collections import defaultdict
from typing import Any

from pydantic_core import PydanticUndefined, to_jsonable_python

from dbt_charts.core.compile.models.constraints import (
    AtLeastOneOf,
    AtMostOneOf,
    Condition,
    Constraint,
    Forbids,
    IsList,
    IsSet,
    ItemsMatch,
    NotNull,
    Requires,
    ValueIn,
)
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


def _schema_for_token(
    token: str, keywords: dict[str, dict[str, str]]
) -> dict[str, Any]:  # type-state: explicit_any — JSON Schema value, see _ref
    """Map a single non-union type token to a JSON Schema dict.

    `keywords` annotates primitive items of a list/dict token the same way it
    annotates a top-level arm (`_map_type_repr`).
    """
    if token in _PRIMITIVES:
        return _PRIMITIVES[token]
    if token.startswith(("list[", "set[", "frozenset[")):
        item_repr = token[token.index("[") + 1 : -1]
        return _container_schema("array", "items", _map_type_repr(item_repr, keywords))
    if token.startswith("dict["):
        value_repr = ", ".join(_split_union(token[5:-1], ", ")[1:])
        return _container_schema(
            "object", "additionalProperties", _map_type_repr(value_repr, keywords)
        )
    return {}


def _container_schema(
    kind: str,
    inner_key: str,
    inner: dict[str, Any],  # type-state: explicit_any — JSON Schema value, see _ref
) -> dict[str, Any]:  # type-state: explicit_any — JSON Schema value, see _ref
    """An array/object schema, typed by `inner` unless it is empty."""
    if not inner:
        return {"type": kind}
    return {"type": kind, inner_key: inner}


def _split_union(s: str, sep: str = " | ") -> list[str]:
    """Split s on sep at bracket depth 0 (not inside [...])."""
    parts: list[str] = []
    depth = 0
    start = 0
    i = 0
    while i < len(s):
        if s[i] == "[":
            depth += 1
        elif s[i] == "]":
            depth -= 1
        elif depth == 0 and s.startswith(sep, i):
            parts.append(s[start:i])
            start = i + len(sep)
            i += len(sep)
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
    return [
        token.strip()
        for token in _split_union(type_repr)
        if token.strip().startswith(("list[", "dict["))
    ]


def _array_schema(
    items: dict[str, Any],  # type-state: explicit_any — see _ref
) -> dict[str, Any]:  # type-state: explicit_any — see _ref
    """Build an array-shaped JSON Schema fragment."""
    return {"type": "array", "items": items}


def _primitive_schema(
    name: str, keywords: dict[str, dict[str, str]]
) -> dict[str, Any]:  # type-state: explicit_any — JSON Schema value, see _ref
    """Map a primitive name to its JSON Schema dict plus the arm's own
    keywords (SchemaField.extra_union_type_keywords: `pattern`, `description`)."""
    schema = dict(_PRIMITIVES[name])
    if name in keywords:
        schema.update(keywords[name])
    return schema


def _type_schema(field: SchemaField, root: str) -> dict[str, Any]:
    """Build the JSON Schema type object for a single field."""
    nullable = "None" in field.type_repr

    extra = [
        _primitive_schema(t, field.extra_union_type_keywords)
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
            "prefixItems": [_map_type_repr(r, {}) for r in field.tuple_item_reprs],
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
        # arm (e.g. `ThemeName | str | list[str]`) — add its shape so it
        # isn't dropped from the anyOf, deduped on the mapped shape.
        for token in sibling_container_tokens(field.type_repr):
            arm = _schema_for_token(token, field.extra_union_type_keywords)
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
            all_branches.append(_array_schema(item_schema))
        if nullable:
            all_branches.append({"type": "null"})
        return {"anyOf": all_branches} if len(all_branches) > 1 else all_branches[0]

    return _map_type_repr(field.type_repr, field.extra_union_type_keywords)


def _map_type_repr(
    type_repr: str, keywords: dict[str, dict[str, str]]
) -> dict[str, Any]:  # type-state: explicit_any — JSON Schema value, see _ref
    """Map a type_repr string to a JSON Schema type dict.

    `keywords` (SchemaField.extra_union_type_keywords) annotates a top-level
    primitive arm, e.g. the str arm of `float | <patterned str>`.

    An arm with no schema (`Any`, a model this IR does not walk) admits
    anything, so the whole union does: dropping that arm would reject values
    Pydantic accepts.
    """
    schemas: list[dict[str, Any]] = []  # type-state: explicit_any — see _ref
    for token in (t.strip() for t in _split_union(type_repr)):
        schema = (
            _primitive_schema(token, keywords)
            if token in _PRIMITIVES
            else _schema_for_token(token, keywords)
        )
        if not schema:
            return {}
        if schema not in schemas:
            schemas.append(schema)
    return schemas[0] if len(schemas) == 1 else {"anyOf": schemas}


_BOUND_KEYWORDS = {
    "gt": "exclusiveMinimum",
    "ge": "minimum",
    "lt": "exclusiveMaximum",
    "le": "maximum",
}
_LENGTH_KEYWORD_SUFFIX = {
    "str": "Length",
    "list": "Items",
    "tuple": "Items",
    "set": "Items",
    "frozenset": "Items",
    "dict": "Properties",
}


def _constraint_keywords(
    field: SchemaField,
) -> dict[str, Any]:  # type-state: explicit_any — JSON Schema value, see _ref
    """JSON Schema keywords for field.bounds and field.constraints, set beside
    its type schema.

    Each keyword only constrains its own JSON type, so it leaves other arms free.
    """
    keywords: dict[str, Any] = {  # type-state: explicit_any — see _ref
        _BOUND_KEYWORDS[name]: value for name, value in field.bounds.items()
    }
    for name, value in field.constraints.items():
        if name == "pattern":
            keywords["pattern"] = value
            continue
        prefix = name.removesuffix("_length")
        suffixes = {
            _LENGTH_KEYWORD_SUFFIX[kind]
            for kind in (
                token.strip().split("[", 1)[0]
                for token in _split_union(field.type_repr)
            )
            if kind in _LENGTH_KEYWORD_SUFFIX
        }
        if not suffixes:
            raise ValueError(
                f"field {field.name!r} ({field.type_repr}) has {name} but no sized arm"
            )
        keywords |= {f"{prefix}{suffix}": value for suffix in suffixes}
    for kind, lengths in field.arm_length_constraints.items():
        for name, value in lengths.items():
            prefix = name.removesuffix("_length")
            keywords[f"{prefix}{_LENGTH_KEYWORD_SUFFIX[kind]}"] = value
    return keywords


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


_EMPTY_VALUES: list[Any] = [None, "", [], {}]  # type-state: explicit_any — JSON values


def _at(
    key: str,
    schema: dict[str, Any],  # type-state: explicit_any — JSON Schema value, see _ref
    required: bool = True,
) -> dict[str, Any]:  # type-state: explicit_any — JSON Schema value, see _ref
    """Apply ``schema`` to the value at dotted ``key`` of an object.

    Each level is typed ``object``: ``required``/``properties`` hold vacuously on
    a null, which would make an absent nested key read as present.
    """
    for part in reversed(key.split(".")):
        wrapped: dict[str, Any] = {  # type-state: explicit_any — see _ref
            "type": "object",
            "properties": {part: schema},
        }
        if required:
            wrapped["required"] = [part]
        schema = wrapped
    return schema


def _is_set(
    key: str, nonempty: bool, auto: bool = False
) -> dict[str, Any]:  # type-state: explicit_any — JSON Schema value, see _ref
    unset: dict[str, Any] = (  # type-state: explicit_any — see _ref
        {"enum": _EMPTY_VALUES} if nonempty else {"type": "null"}
    )
    if auto:
        unset = {"anyOf": [unset, {"const": "auto"}]}
    return _at(key, {"not": unset})


def _condition(
    condition: Condition,
) -> dict[str, Any]:  # type-state: explicit_any — JSON Schema value, see _ref
    match condition:
        case IsSet(key):
            return _is_set(key, nonempty=False)
        case ValueIn(key, values, negate):
            matches = _at(key, {"enum": list(values)})
            if None in values:
                # An absent key (or object on its path) reads as null.
                matches = {"anyOf": [matches, {"not": _is_set(key, nonempty=False)}]}
            return {"not": matches} if negate else matches
        case IsList(key):
            return _at(key, {"type": "array"})


def _when(
    when: tuple[Condition, ...],
    then: dict[str, Any],  # type-state: explicit_any — JSON Schema value, see _ref
) -> dict[str, Any]:  # type-state: explicit_any — JSON Schema value, see _ref
    if not when:
        return then
    return {"if": {"allOf": [_condition(c) for c in when]}, "then": then}


def _constraint(
    constraint: Constraint,
) -> dict[str, Any]:  # type-state: explicit_any — JSON Schema value, see _ref
    match constraint:
        case AtLeastOneOf(keys, _, nonempty):
            return {"anyOf": [_is_set(k, k in nonempty) for k in keys]}
        case AtMostOneOf(keys, _, nonempty, auto):
            pairs = itertools.combinations(keys, 2)
            return {
                "not": {
                    "anyOf": [
                        {
                            "allOf": [
                                _is_set(a, a in nonempty, a in auto),
                                _is_set(b, b in nonempty, b in auto),
                            ]
                        }
                        for a, b in pairs
                    ]
                }
            }
        case Requires(keys, _, when, nonempty):
            return _when(when, {"allOf": [_is_set(k, k in nonempty) for k in keys]})
        case Forbids(keys, _, when, nonempty):
            return _when(
                when, {"not": {"anyOf": [_is_set(k, k in nonempty) for k in keys]}}
            )
        case NotNull(keys, _):
            return {
                "allOf": [
                    _at(k, {"not": {"type": "null"}}, required=False) for k in keys
                ]
            }
        case ItemsMatch(key, pattern, _, when):
            items = {"items": {"type": "string", "pattern": pattern}}
            return _when(when, _at(key, items, required=False))


# Annotation keyword marking an `allOf` entry as a cross-field rule. Validators
# ignore unknown keywords; `grammar_only` keys on it to drop the rules exactly.
CROSS_FIELD_RULE = "x-dbt-charts-cross-field-rule"


def constraint_schema(
    constraints: tuple[Constraint, ...],
) -> dict[str, Any]:  # type-state: explicit_any — JSON Schema value, see _ref
    """JSON Schema keywords stating a model's cross-field constraints."""
    if not constraints:
        return {}
    return {"allOf": [{CROSS_FIELD_RULE: True, **_constraint(c)} for c in constraints]}


def grammar_only(
    node: Any,  # type-state: explicit_any — any JSON Schema value
) -> Any:  # type-state: explicit_any — same shape as the input
    """``node`` without its cross-field rules: the key shapes alone."""
    if isinstance(node, list):
        return [grammar_only(item) for item in node]
    if not isinstance(node, dict):
        return node
    stripped = {key: grammar_only(value) for key, value in node.items()}
    rules = stripped.get("allOf")
    if isinstance(rules, list):
        kept = [r for r in rules if not (isinstance(r, dict) and CROSS_FIELD_RULE in r)]
        if kept:
            stripped["allOf"] = kept
        else:
            del stripped["allOf"]
    return stripped


def _model_to_def(model_name: str, ir: AuthorableSchema) -> dict[str, Any]:
    """Build a JSON Schema $defs entry for one AuthorableModel."""
    model = ir.models[model_name]
    if model.union is not None:
        return _union_to_def(model_name, ir)

    required = [f.name for f in model.fields if f.required and not f.is_extra_key]
    properties: dict[str, Any] = {}
    additional_properties: dict[str, Any] | None = None
    for field in model.fields:
        prop = _type_schema(field, ir.root)
        keywords = _constraint_keywords(field)
        if clash := prop.keys() & keywords.keys():
            raise ValueError(
                f"{model_name}.{field.name}: constraint keywords {sorted(clash)} "
                "collide with its type schema"
            )
        prop = {**prop, **keywords}
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
            prop["default"] = to_jsonable_python(field.default)
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
    elif model.closed:
        defn["additionalProperties"] = False
    defn.update(constraint_schema(model.constraints))
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
    if "additionalProperties" in root:
        result["additionalProperties"] = root["additionalProperties"]
    if root.get("required"):
        result["required"] = root["required"]
    if root.get("allOf"):
        result["allOf"] = root["allOf"]
    if defs:
        result["$defs"] = defs

    return result


render_json_schema = render_yaml_schema
