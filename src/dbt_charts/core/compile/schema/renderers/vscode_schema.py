"""VS Code JSON Schema renderer — decorates the strict dbt charts YAML schema.

Calls render_yaml_schema(ir) for the IR-driven core, then adds the IDE-specific
extras that have no Pydantic counterpart (fileMatch, LayoutItem definition).
The theme enum is part of the base schema itself (render_yaml_schema injects
it), not an IDE-only extra.

Entry point: render_vscode_schema(schema) -> dict
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.schema.introspection import AuthorableSchema
from dbt_charts.core.compile.schema.renderers.json_schema import render_yaml_schema


def _extract_description(prop: dict[str, Any]) -> str:
    if "description" in prop:
        return prop["description"]
    for branch in prop.get("anyOf", []):
        if "description" in branch:
            return branch["description"]
    return ""


def _build_layout_item_def() -> dict[str, Any]:
    return {
        "oneOf": [
            {"type": "string", "description": "Chart name reference."},
            {"$ref": "#/$defs/AuthoredChart"},
            {
                "type": "object",
                "description": "Nested layout.",
                "properties": {
                    "rows": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/LayoutItem"},
                        "description": "Nested vertical layout items.",
                    },
                    "cols": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/LayoutItem"},
                        "description": "Nested horizontal layout items.",
                    },
                    "grid": {
                        "allOf": [{"$ref": "#/$defs/GridLayout"}],
                        "description": "Nested grid layout.",
                    },
                    "tabs": {
                        "allOf": [{"$ref": "#/$defs/TabLayout"}],
                        "description": "Nested tab layout.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Section title.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "AI search/context metadata. Never rendered.",
                    },
                    "details": {
                        "anyOf": [
                            {
                                "type": "string",
                                "description": "String shorthand: sets the summary label.",
                            },
                            {
                                "type": "object",
                                "properties": {
                                    "summary": {
                                        "type": "string",
                                        "description": "Label shown when collapsed.",
                                    },
                                    "expanded_title": {
                                        "type": "string",
                                        "description": "Label shown when expanded.",
                                    },
                                    "expanded": {
                                        "type": "boolean",
                                        "default": False,
                                        "description": "Default open state.",
                                    },
                                },
                                "required": ["summary"],
                                "additionalProperties": False,
                            },
                        ],
                        "description": "Collapsible section. String shorthand → BoardDetails(summary=str).",
                    },
                    "style": {
                        "type": "object",
                        "description": "CSS-like style overrides for this section.",
                    },
                },
            },
        ]
    }


def _decorate_for_vscode(base: dict[str, Any]) -> dict[str, Any]:
    # tach-ignore(pre-existing compile->project coupling — accepted debt)
    from dbt_charts.core.project import PROJECT_CONFIG_NAME

    schema = dict(base)
    props = schema.setdefault("properties", {})
    defns = schema.setdefault("$defs", {})

    # IDE metadata
    schema["title"] = "dbt charts Board"
    schema["fileMatch"] = [
        PROJECT_CONFIG_NAME,
        "dbt_charts.yaml",
        "charts/*.yml",
        "charts/*.yaml",
        "charts/**/*.yml",
        "charts/**/*.yaml",
        "*.board.yml",
    ]
    # charts/variables/queries need no override here — render_yaml_schema's IR
    # now derives the ref/string arms itself (schema/introspection.py reads
    # json_schema_input_type off the QueryOrRef/ChartOrRef/VariableOrRef
    # annotations), so `base`'s properties are already fully typed.

    # Override rows/cols → typed items (IR has untyped list[str | dict])
    for key in ("rows", "cols"):
        if key in props:
            props[key] = {
                "type": "array",
                "description": _extract_description(props[key]),
                "items": {"$ref": "#/$defs/LayoutItem"},
            }

    # Widen CachePatch to its scalar authoring forms (`cache: 1h`, `cache: false`).
    # Every scope $refs this one definition, so decorating it here covers them all.
    # The IR emits only the block shape — the scalars are expanded by CachePatch's
    # before-validator, which JSON Schema generation cannot see.
    if "CachePatch" in defns:
        # Closed: `enabled` is not authorable, so the IDE offers only the
        # scalars and the keys the reference actually teaches. This is the
        # board-scope shape; the project root's extra `path` key lives in
        # `dbt_charts.yml`, which this board schema does not match.
        block_arm = {**defns["CachePatch"], "additionalProperties": False}
        # `ttl` is Optional in Python because unset means "inherit", but the
        # validator rejects a written `ttl: null` as ambiguous — so the null
        # arm the generic renderer emits for every Optional field would have
        # the IDE green-light the one spelling the compiler goes out of its
        # way to refuse.
        ttl = block_arm["properties"]["ttl"]
        block_arm["properties"] = {
            **block_arm["properties"],
            "ttl": {
                **ttl,
                # The `forever` arm is a bare enum and carries no `type`.
                "anyOf": [arm for arm in ttl["anyOf"] if arm.get("type") != "null"],
            },
        }
        defns["CachePatch"] = {
            "oneOf": [
                block_arm,
                {
                    "type": "boolean",
                    "description": "false disables caching at this scope; true "
                    "enables it with the inherited ttl.",
                },
                {
                    "type": "string",
                    "description": "Cache ttl: a duration ('5m', '1h', '7d', "
                    "'1h30m') or 'forever' to never auto-expire.",
                },
            ]
        }

    defns["LayoutItem"] = _build_layout_item_def()

    return schema


def render_vscode_schema(schema: AuthorableSchema) -> dict[str, Any]:
    """Render an AuthorableSchema as a VS Code JSON Schema 2020-12.

    Calls render_yaml_schema for the IR-driven core and decorates with
    IDE-specific extras (fileMatch, layout constraints, etc.).
    """
    base = render_yaml_schema(schema)
    return _decorate_for_vscode(base)
