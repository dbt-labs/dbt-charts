"""Pydantic model introspection — Layer 1 of the two-layer schema IR.

Walks AuthoredBoard and all reachable authored models once, producing an
AuthorableSchema IR. Renderers (json_schema, prompt, markdown, erd) consume
this IR — no rendering logic lives here.

Entry point: introspect() -> AuthorableSchema
"""

from __future__ import annotations

import dataclasses
import functools
import types
import typing
from collections.abc import Iterable
from enum import Enum
from typing import Annotated, Any, ForwardRef, Literal, Union, get_args, get_origin

from annotated_types import Ge, GroupedMetadata, Gt, Le, Lt, MaxLen, MinLen
from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    StringConstraints,
    Tag,
    ValidationError,
)
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined, PydanticUndefinedType

from dbt_charts.core.compile.models.board.authored import (
    AuthoredBoard,
    AuthoredBoardInput,
)
from dbt_charts.core.compile.models.chart.authored import (
    AUTHORED_CHART_VARIANTS,
    AuthoredChart,
)
from dbt_charts.core.compile.models.config import ExecutionConfig
from dbt_charts.core.compile.models.constraints import (
    Constraint,
    declared_constraints,
)
from dbt_charts.core.compile.models.factories import FontSlotBase, _PatchBase
from dbt_charts.core.compile.models.markers import Facet, SchemaSugar
from dbt_charts.core.compile.models.query.authored import (
    AuthoredCompactValuesQuery,
    AuthoredHttpQuery,
    AuthoredQuery,
    AuthoredSchemaQuery,
    AuthoredSqlQuery,
    AuthoredValuesQuery,
)
from dbt_charts.core.compile.models.source import SourceConfig
from dbt_charts.core.compile.models.style.authored import PaginationConfig


@dataclasses.dataclass
class SchemaField:
    name: str
    description: str
    type_repr: str
    required: bool
    default: Any
    default_repr: (
        str | None
    )  # human-readable default for display (None = no meaningful default)
    enum_values: list[str | int | bool] | None
    nested_models: list[str]  # class names of nested AuthorableModels (empty = none)
    container: str | None = None  # "list" or "dict" if the model is in a container
    extra_union_types: list[str] = dataclasses.field(
        default_factory=list
    )  # primitive type names alongside models or enums in a union
    # JSON Schema keywords (pattern, description) a primitive arm in
    # extra_union_types declares, e.g. {"str": {"pattern": VAR_REF_RE.pattern}},
    # keyed by the same name so the renderer can look them up.
    extra_union_type_keywords: dict[str, dict[str, str]] = dataclasses.field(
        default_factory=dict
    )
    container_mapping_models: list[str] = dataclasses.field(default_factory=list)
    # Item-type branch names (models and/or primitives) for a list[...] sibling
    # arm beside a non-container arm in the same union — the mirror of
    # container_mapping_models for a list rather than a dict sibling.
    container_list_models: list[str] = dataclasses.field(default_factory=list)
    # Per-position type_repr strings for a FIXED-length tuple field (e.g.
    # tuple[int, float] -> ("int", "float")); None for every other shape,
    # including a variadic tuple[X, ...] (which is list-shaped, not fixed).
    tuple_item_reprs: tuple[str, ...] | None = None
    # Numeric bounds Pydantic enforces, keyed gt, ge, lt, le.
    bounds: dict[str, int | float] = dataclasses.field(default_factory=dict)
    # Other value constraints: min_length, max_length, pattern.
    constraints: dict[str, int | str] = dataclasses.field(default_factory=dict)
    # min_length/max_length declared on one union arm, keyed by that arm's kind
    # ("str", "list", "dict", ...), so they bind only values of that kind.
    arm_length_constraints: dict[str, dict[str, int]] = dataclasses.field(
        default_factory=dict
    )
    is_extra_key: bool = (
        False  # True for synthetic '<name>' fields from __pydantic_extra__
    )
    inherit_from: tuple[str, ...] = ()  # ordered fallback dot-paths (Inherit marker)
    inherit_slot: str | None = None  # source subtree path (InheritSlot marker)
    # Semantic facets declared on the field — what the value *means*, which the
    # type and the description cannot say. See `Facet`'s subclasses.
    facets: tuple[Facet, ...] = ()
    # False on an authored patch field that takes no explicit null: the schema
    # then omits the null arm the Optional annotation would add.
    null_allowed: bool = True


@dataclasses.dataclass
class UnionSpec:
    """Variant metadata for a synthetic union entry like AuthoredChart."""

    # None means the variants are structurally distinguished. This is used for
    # authored queries whose type tag may be inferred from variant-only keys.
    discriminator: str | None
    variants: dict[str, str]  # tag -> model name (e.g. "bar" -> "BarChart")


@dataclasses.dataclass
class AuthorableModel:
    name: str
    doc: str
    fields: list[SchemaField]
    generated: bool = False  # True for _PatchBase subclasses (build_patch_model output)
    union: UnionSpec | None = (
        None  # set on synthetic union entries (e.g. AuthoredChart)
    )
    # True when the model validates with nothing set, so an editor can bring the
    # object into existence one field at a time. False means it cannot: a
    # required field (`BoardDetails.summary`) or a model validator
    # (`MultiplesConfig`, which demands `rows` or `columns`) refuses the
    # single-key object, and only a form that writes the whole thing will do.
    # Synthetic union entries have no class to ask, and default to False.
    empty_is_valid: bool = False
    constraints: tuple[Constraint, ...] = ()
    # False when the model keeps unknown keys (extra="allow" or "ignore").
    closed: bool = True


@dataclasses.dataclass
class AuthorableSchema:
    root: str
    models: dict[str, AuthorableModel]


def schema_path_to_yaml(abs_path: str) -> str:
    """Convert an InheritGraph dot-path to its authored YAML key path.

    InheritGraph paths use the model class name as root: ``"Style.charts.axis"``.
    Authored YAML uses the lowercase key: ``"style.charts.axis"``.
    """
    dot = abs_path.index(".")
    return abs_path[:dot].lower() + abs_path[dot:]


# Registry mapping union alias objects to their IR model names.
# Used in _nested_model_names to short-circuit discriminated union traversal.
_UNION_ALIASES = {
    AuthoredChart: "AuthoredChart",
    AuthoredQuery: "AuthoredQuery",
}


def _union_alias_variant_classes(
    alias: Any,  # type-state: explicit_any — arbitrary typing construct, matches _nested_model_names/_extra_union_types
) -> list[type]:
    """Concrete variant classes behind a `_UNION_ALIASES` entry, in declared order.

    `AuthoredChart`/`AuthoredQuery` resolve to one opaque IR name so a field
    typed with the alias doesn't flatten into every variant (that is the
    whole point of `_UNION_ALIASES`) -- but `_collect_models` still needs to
    walk each variant's own fields eventually. `_find_nested_class` can't do
    that for an alias name (it searches for a *class* literally named
    "AuthoredChart", and there is no such class -- the name is synthetic).
    Deriving the variants directly from the alias's own raw union keeps
    traversal order (and therefore which class wins an ambiguous shared name
    like `TableChartStylePatch`) identical regardless of which field reaches
    the alias, or how.
    """
    raw_union = alias.__origin__
    variants: list[type] = []
    for arm in get_args(raw_union):
        # A Tag-wrapped arm has __origin__ (its bare class); a bare class
        # has none at all, so arm itself is already what we want.
        cls = getattr(
            arm, "__origin__", arm
        )  # type-state: silent_fallback — duck-typed
        if not isinstance(cls, type):
            raise TypeError(
                f"{alias!r} union arm {arm!r} did not resolve to a class — "
                "a shape _union_alias_variant_classes cannot handle silently "
                "dropped it from the collected variant list"
            )
        if cls not in variants:
            variants.append(cls)
    return variants


_UNION_ALIAS_VARIANTS: dict[str, list[type]] = {
    name: _union_alias_variant_classes(alias) for alias, name in _UNION_ALIASES.items()
}
assert {cls.__name__ for cls in _UNION_ALIAS_VARIANTS["AuthoredChart"]} == set(
    AUTHORED_CHART_VARIANTS.values()
), "AuthoredChart's derived variants drifted from its own declared tag->class mapping"

# Public: renderers/prompt.py's BFS needs the same variant names (not the
# classes themselves — the renderer walks the IR by name, never touching a
# live model class) to descend an alias edge without giving the alias itself
# a heading.
UNION_ALIAS_VARIANT_NAMES: dict[str, list[str]] = {
    name: [cls.__name__ for cls in variants]
    for name, variants in _UNION_ALIAS_VARIANTS.items()
}


def _without_tag(
    annotation: Any,  # type-state: explicit_any — arbitrary typing construct, matches _nested_model_names
) -> Any:  # type-state: explicit_any — same boundary as the parameter above
    """Reconstruct annotation with any `Tag` metadata stripped, recovering a
    union alias buried one level deeper than `_UNION_ALIASES` looks.

    A discriminated-union arm like `Annotated[AuthoredChart, Tag("@inline")]`
    (authored.py's ChartOrRef's "@inline" arm) flattens at construction time
    into `Annotated[<raw chart union>, Discriminator(...), Tag("@inline")]` —
    AuthoredChart's own Discriminator metadata and the outer Tag share one
    metadata tuple, so the wrapped object no longer equals bare AuthoredChart
    by identity or structural equality. Stripping Tag and reconstructing
    `Annotated[<raw union>, Discriminator(...)]` from what's left recovers
    something structurally identical to AuthoredChart itself (same origin,
    same remaining metadata), which `_UNION_ALIASES` can match by equality.
    """
    if not hasattr(annotation, "__metadata__"):
        return annotation
    kept = tuple(m for m in annotation.__metadata__ if not isinstance(m, Tag))
    if not kept or len(kept) == len(annotation.__metadata__):
        # A bare origin with no metadata can never match a _UNION_ALIASES entry.
        return annotation
    return Annotated[(annotation.__origin__, *kept)]


def _unwrap_annotated(annotation: Any) -> Any:
    """Strip Annotated[X, *metadata] → X. Idempotent on plain types.

    A `BeforeValidator` carrying `json_schema_input_type` describes a
    `mode="before"` coercion JSON Schema cannot otherwise see (e.g. a bare
    string desugared into a query/ref dict — refs.py, authored.py's
    QueryOrRef/ChartOrRef/VariableOrRef). When present, that widened input
    type is what gets walked instead of the bare output type `X`, so the
    coercion's accepted shapes surface as real anyOf/$ref/primitive arms
    everywhere this IR is consumed — including the migrator's schema gate.
    """
    if not hasattr(annotation, "__metadata__"):
        return annotation
    for meta in annotation.__metadata__:
        # Most metadata objects (Merge, Content, Tag, ...) have no
        # json_schema_input_type attribute at all; PydanticUndefined is the same
        # "unset" sentinel pydantic itself uses for BeforeValidator's own default.
        input_type = getattr(
            meta, "json_schema_input_type", PydanticUndefined
        )  # type-state: silent_fallback — sentinel check, not a data fallback (see comment above)
        if not isinstance(input_type, PydanticUndefinedType):
            return input_type
    return annotation.__origin__


def _is_authored_model(cls: Any) -> bool:
    """Return True if cls should be included in the IR.

    Includes authored.* models, primitives, _PatchBase subclasses, compiled
    style models from style/theme/, models.refs (VariableRef/QueryRef/ChartRef —
    the cross-file ref pointer models, authored wherever a "@ref" union arm
    admits one), models.source (source configs and their nested blocks),
    vega_lite.contracts, and *Patch models
    from models.cache (CachePatch is the authored overlay for the cache block).
    """
    if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
        return False
    if issubclass(cls, _PatchBase):
        return True
    module = getattr(cls, "__module__", "")
    return (
        ".authored" in module
        or module.endswith("primitives")
        or module.endswith("style.theme")
        or ".style.theme." in module
        or module.endswith(".models.refs")
        or module.endswith(".models.source")
        or module.endswith(".vega_lite.contracts")
        or (module.endswith(".models.cache") and cls.__name__.endswith("Patch"))
    )


def _extract_enum_values(
    annotation: Any,  # type-state: explicit_any — arbitrary typing construct, matches _nested_model_names
) -> list[str | int | bool] | None:  # type-state: optional — None: not an enum
    """Return enum values if annotation is an Enum or Literal, else None.

    Values are returned as their native Python types (str, int, bool, etc.) so
    the JSON Schema renderer emits the correct JSON type (true, not "True").
    """
    annotation = _unwrap_annotated(annotation)
    origin = get_origin(annotation)
    args = get_args(annotation)

    # Literal["foo", "bar", ...] or Literal[True]
    if origin is Literal:
        return [a for a in args if isinstance(a, (str, int, bool))]

    # str Enum subclass
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return [e.value for e in annotation]

    # Union — look for a Literal or Enum inside (e.g. ChartType | None)
    if origin is type(None) or origin is None:
        return None
    # handle Union (typing.Union has origin = types.UnionType or typing.Union)
    if args:
        for arg in args:
            result = _extract_enum_values(arg)
            if result:
                return result

    return None


def _nested_model_names(annotation: Any) -> list[str]:
    """Return all authored model class names reachable from annotation.

    Returns an empty list if no authored models are found.
    For plain types, returns a one-element list; for discriminated unions,
    returns one entry per branch. Union aliases (e.g. AuthoredChart) resolve
    to a single opaque name without expanding their branches.
    """
    if annotation in _UNION_ALIASES:
        return [_UNION_ALIASES[annotation]]
    stripped = _without_tag(annotation)
    if stripped in _UNION_ALIASES:
        return [_UNION_ALIASES[stripped]]
    annotation = _unwrap_annotated(annotation)
    if annotation in _UNION_ALIASES:
        return [_UNION_ALIASES[annotation]]
    if _is_authored_model(annotation):
        return [annotation.__name__]

    origin = get_origin(annotation)
    args = get_args(annotation)
    if not args:
        return []

    # dict[K, V] — recurse into the value type only
    if origin is dict and len(args) == 2:
        return _nested_model_names(args[1])

    # list[SomeModel] or Union/Optional — collect all authored branches, dedup by class name.
    # A list[...]/dict[...] arm sitting beside another *non-container* arm (not
    # the Optional-single-container case: len(non_none_args) > 1) is a mixed
    # union like `list[ChartSupportTableEntry] | ChartSupportTable` — its
    # item/value models are handled separately (_container_list_models /
    # _container_mapping_models); flattening them here would make them look
    # like bare arms of the outer field (accepting `support_table: {source:
    # ...}` directly, which is not a valid shape — only wrapped in a list or
    # under the ChartSupportTable object form).
    non_none_args = [a for a in args if a is not type(None)]
    skip_containers = len(non_none_args) > 1
    result: list[str] = []
    for arg in args:
        # Unwrap before checking origin: a constrained container sibling
        # (`Annotated[list[X], Field(min_length=1)]`, e.g. ChartSupportTableOrList's
        # json_schema_input_type) reports get_origin() == Annotated, not list,
        # until the Field wrapper is stripped.
        if skip_containers and get_origin(_unwrap_annotated(arg)) in (list, dict):
            continue
        result.extend(_nested_model_names(arg))
    return list(dict.fromkeys(result))


def _all_model_names_in_annotation(annotation: Any) -> list[str]:
    """Return class names of ALL BaseModel subclasses reachable from annotation.

    Unlike _nested_model_names, this is not limited to "authored" modules.
    Used for __pydantic_extra__ synthetic fields where the value type may
    reference source connector configs (models.source module).
    """
    annotation = _unwrap_annotated(annotation)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation.__name__]

    origin = get_origin(annotation)
    args = get_args(annotation)
    if not args:
        return []

    if origin is dict and len(args) == 2:
        return _all_model_names_in_annotation(args[1])

    result: list[str] = []
    for arg in args:
        if arg is not type(None):
            result.extend(_all_model_names_in_annotation(arg))
    return result


def _type_repr(annotation: Any) -> str:
    """Human-readable type string for display in docs/prompts."""
    annotation = _unwrap_annotated(annotation)
    if annotation is None or annotation is type(None):
        return "None"
    if isinstance(annotation, ForwardRef):
        return annotation.__forward_arg__

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Literal:
        values = [repr(a) for a in args]
        return f"one of: {', '.join(values)}"

    # Guard: only match plain types, not generic aliases (list[X], dict[K,V])
    if origin is None and isinstance(annotation, type):
        if issubclass(annotation, Enum):
            return f"one of: {', '.join(e.value for e in annotation)}"
        return annotation.__name__

    if origin is list:
        if args:
            return f"list[{_type_repr(args[0])}]"
        return "list"

    if origin is dict:
        if len(args) == 2:
            return f"dict[{_type_repr(args[0])}, {_type_repr(args[1])}]"
        return "dict"

    if origin in (set, frozenset):
        return f"{origin.__name__}[{_type_repr(args[0])}]"

    if origin is tuple:
        if not args:
            return "tuple"
        if len(args) == 2 and args[1] is Ellipsis:
            return f"tuple[{_type_repr(args[0])}, ...]"
        return f"tuple[{', '.join(_type_repr(a) for a in args)}]"

    # Union types (X | Y | None) — deduplicate branches (discriminated unions may
    # have the same class under multiple Tag() aliases, e.g. BarChart for bar+histogram)
    if args:
        non_none = [a for a in args if a is not type(None)]
        nullable = any(a is type(None) for a in args)
        parts = list(dict.fromkeys(_type_repr(a) for a in non_none))
        result = " | ".join(parts)
        if nullable and len(non_none) > 0:
            result += " | None"
        return result

    if annotation is Any:
        return "Any"
    return str(annotation)


def _get_container(annotation: Any) -> str | None:
    """Return 'list' or 'dict' if annotation wraps a nested model in a container."""
    annotation = _unwrap_annotated(annotation)
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is list:
        return "list"
    if origin is dict:
        return "dict"
    if args:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _get_container(non_none[0])
    return None


def _tuple_item_reprs(
    annotation: Any,  # type-state: explicit_any — arbitrary typing construct, matches _get_container/_type_repr
) -> tuple[str, ...] | None:
    """Return per-position type_repr strings for a FIXED-length tuple field.

    None for every other shape, including a variadic tuple[X, ...] (that's
    list-shaped: one item type repeated, not one type per position).
    """
    annotation = _unwrap_annotated(annotation)
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is tuple:
        if not args or (len(args) == 2 and args[1] is Ellipsis):
            return None
        return tuple(_type_repr(a) for a in args)
    if args:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _tuple_item_reprs(non_none[0])
    return None


_PRIMITIVE_NAMES = frozenset({"str", "int", "float", "bool"})


def _default_repr(field_info: FieldInfo) -> str | None:
    """Return a human-readable default string for display, or None if no meaningful default."""
    if field_info.is_required():
        return None
    default = field_info.default
    if isinstance(default, PydanticUndefinedType):
        factory = field_info.default_factory
        if factory is dict:
            return "{}"
        if factory is list:
            return "[]"
        return None
    if default is None:
        return None
    if isinstance(default, bool):
        return str(default).lower()
    if isinstance(default, str):
        return f'"{default}"'
    if isinstance(default, (int, float)):
        return str(default)
    return None


def _extra_union_types(annotation: Any) -> list[str]:
    """Primitive type names that sit alongside models or enums in a union.

    Returns the names (e.g. ["str", "bool"]) so the renderer can include
    them in anyOf alongside $ref or enum branches.
    Container values recurse because ``list[str | AuthoredChart]`` needs the
    same scalar branch on its item schema as a bare union does. A union arm
    can itself unwrap to another union (a `json_schema_input_type` widening
    nested inside one arm of an outer discriminated union, e.g.
    `ChartSupportTableEntry`'s "source" arm, or a chart-level `ChartQuery`
    reached through an outer `ChartQuery | None`) — recursing on any arg with
    an origin, not just list/dict, flattens that case the same way; skipping
    it would silently drop a bare `str` sibling arm.
    """
    annotation = _unwrap_annotated(annotation)
    origin = get_origin(annotation)
    args = get_args(annotation)
    if not args:
        return []

    if origin in (list, dict):
        return _extra_union_types(args[-1])

    # A list[...]/dict[...] sibling beside another *non-container* arm (not the
    # Optional-single-container case) that itself carries authored models is a
    # mixed union like `list[ChartSupportTableEntry] | ChartSupportTable`: its
    # item type's primitive arms belong inside that container's items, handled
    # by _container_list_models, not as a bare sibling of the outer field. A
    # scalar-only container sibling (`ThemeName | str | list[str]`) keeps the
    # existing recurse-and-flatten behavior — that one has no separate
    # container-model mechanism to hand it off to.
    non_none_args = [a for a in args if a is not type(None)]
    skip_containers = len(non_none_args) > 1

    result: list[str] = []
    for arg in args:
        # Unwrap per-arm: a validated primitive alias (Duration =
        # Annotated[str, AfterValidator]) is still a str arm of the union, and
        # dropping it makes a `Literal[...] | Duration` field look like a closed
        # enum in every generated schema.
        arg = _unwrap_annotated(arg)
        if arg is type(None):
            continue
        if _is_authored_model(arg):
            continue
        arg_origin = get_origin(arg)
        if skip_containers and arg_origin in (list, dict) and _nested_model_names(arg):
            continue
        if arg_origin is Literal:
            continue
        if isinstance(arg, type) and issubclass(arg, Enum):
            continue
        if (
            arg_origin is None
            and isinstance(arg, type)
            and arg.__name__ in _PRIMITIVE_NAMES
        ):
            result.append(arg.__name__)
            continue
        # Recurse into a container (list/dict — a sibling arm one level
        # further in than the top-level early-return covers) or a genuine
        # nested union (a json_schema_input_type widening surfaced one arm
        # deeper, e.g. ChartSupportTableEntry's "source" arm) — never a tuple:
        # fixed-length tuple positions are covered by tuple_item_reprs, a
        # wholly separate SchemaField mechanism.
        if arg_origin in (list, dict, Union, types.UnionType):
            result.extend(_extra_union_types(arg))
    return list(dict.fromkeys(result))


def _arm_keywords(
    annotation: Any,  # type-state: explicit_any — arbitrary typing construct, matches _nested_model_names/_extra_union_types
) -> dict[str, str]:
    """`pattern`/`description` an `Annotated[str, ...]` arm declares."""
    keywords: dict[str, str] = {}
    if get_origin(annotation) is not Annotated:
        return keywords
    for meta in annotation.__metadata__:
        if isinstance(meta, StringConstraints) and isinstance(meta.pattern, str):
            keywords["pattern"] = meta.pattern
        if isinstance(meta, FieldInfo) and meta.description:
            keywords["description"] = meta.description
    return keywords


def _extra_union_type_keywords(
    annotation: Any,  # type-state: explicit_any — arbitrary typing construct, matches _extra_union_types
) -> dict[str, dict[str, str]]:
    """JSON Schema keywords a primitive arm in extra_union_types declares.

    Mirrors _extra_union_types' traversal exactly (same skip_containers guard,
    same container recursion) but collects `_arm_keywords`, e.g.
    VariableOrRef/ChartOrRef's cross-file-ref string arm pattern
    (`VAR_REF_PATTERN_STR`/`CHART_REF_PATTERN_STR`, board/authored.py). A
    plain `str` arm (queries: has one on purpose, since
    normalize_query_value treats any string failing QUERY_REF_RE as SQL)
    contributes nothing here.
    """
    annotation = _unwrap_annotated(annotation)
    origin = get_origin(annotation)
    args = get_args(annotation)
    if not args:
        return {}

    if origin in (list, dict):
        item = args[-1]
        keywords = _arm_keywords(item)
        if keywords and _unwrap_annotated(item) is str:
            return {"str": keywords}
        return _extra_union_type_keywords(item)

    non_none_args = [a for a in args if a is not type(None)]
    skip_containers = len(non_none_args) > 1

    result: dict[str, dict[str, str]] = {}
    for arg in args:
        raw_arg = arg
        arg = _unwrap_annotated(arg)
        if arg is type(None):
            continue
        if _is_authored_model(arg):
            continue
        arg_origin = get_origin(arg)
        if skip_containers and arg_origin in (list, dict) and _nested_model_names(arg):
            continue
        if arg_origin is Literal:
            continue
        if isinstance(arg, type) and issubclass(arg, Enum):
            continue
        if (
            arg_origin is None
            and isinstance(arg, type)
            and arg.__name__ in _PRIMITIVE_NAMES
        ):
            keywords = _arm_keywords(raw_arg)
            if keywords:
                result[arg.__name__] = keywords
            continue
        # Same container/union-only recursion as _extra_union_types — a tuple
        # arm's positions are not union members to widen through.
        if arg_origin in (list, dict, Union, types.UnionType):
            result.update(_extra_union_type_keywords(arg))
    return result


def _container_mapping_models(annotation: type) -> list[str]:
    """Return named-map value models nested in a list item union."""
    annotation = _unwrap_annotated(annotation)
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is dict and len(args) == 2:
        return _nested_model_names(args[1])
    if origin is list and args:
        return _container_mapping_models(args[0])
    result: list[str] = []
    for arg in args:
        result.extend(_container_mapping_models(arg))
    return list(dict.fromkeys(result))


def _container_list_models(
    annotation: Any,  # type-state: explicit_any — arbitrary typing construct, matches _nested_model_names/_extra_union_types
) -> list[str]:
    """Return item-type branch names for a list[...] sibling arm in a union.

    Symmetric to _container_mapping_models (dict-sibling), for a mixed union
    like `list[ChartSupportTableEntry] | ChartSupportTable` — the
    `json_schema_input_type` widening on `support_table:`
    (chart/authored/_support_table.py's `ChartSupportTableOrList`). Includes
    both authored model names (rendered as $ref) and primitive type names
    (rendered via `_PRIMITIVES`), since the list item type can itself be a
    union of both (`ChartSupportTableEntry`'s "source" tag admits a bare
    string) — `_nested_model_names`/`_extra_union_types` never collide on
    naming, so the renderer can dispatch each name by checking `_PRIMITIVES`.
    """
    annotation = _unwrap_annotated(annotation)
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is list and args:
        item = args[0]
        return list(
            dict.fromkeys([*_nested_model_names(item), *_extra_union_types(item)])
        )
    if origin is dict and len(args) == 2:
        return []
    result: list[str] = []
    for arg in args:
        result.extend(_container_list_models(arg))
    return list(dict.fromkeys(result))


# The (private) class Field(pattern=) and StringConstraints put a pattern on.
_PATTERN_METADATA = type(Field(pattern="x").metadata[0])


def _json_number(
    bound: object,  # type-state: object_annotation — annotated_types bounds are any comparable
) -> int | float:
    """A numeric bound as JSON Schema can state it; dates and the like cannot."""
    if not isinstance(bound, (int, float)):
        raise TypeError(f"bound {bound!r} is not a number JSON Schema can state")
    return bound


def _value_bounds(
    metadata: Iterable[Any],  # type-state: explicit_any — arbitrary metadata
) -> dict[str, int | float]:
    """Numeric bounds in annotation metadata, keyed like `SchemaField.bounds`.

    `Annotated[X, Field(...)]` keeps the FieldInfo unexpanded in `__metadata__`,
    and `StringConstraints` groups its markers, so both are opened up.
    """
    found: dict[str, int | float] = {}
    for meta in metadata:
        if isinstance(meta, FieldInfo):
            found |= _value_bounds(meta.metadata)
        elif isinstance(meta, GroupedMetadata):
            found |= _value_bounds(meta)
        elif isinstance(meta, Gt):
            found["gt"] = _json_number(meta.gt)
        elif isinstance(meta, Ge):
            found["ge"] = _json_number(meta.ge)
        elif isinstance(meta, Lt):
            found["lt"] = _json_number(meta.lt)
        elif isinstance(meta, Le):
            found["le"] = _json_number(meta.le)
    return found


def _value_constraints(
    metadata: Iterable[Any],  # type-state: explicit_any — arbitrary metadata
) -> dict[str, int | str]:
    """Length and pattern constraints in annotation metadata, keyed like
    `SchemaField.constraints`; opened up like `_value_bounds`."""
    found: dict[str, int | str] = {}
    for meta in metadata:
        if isinstance(meta, FieldInfo):
            found |= _value_constraints(meta.metadata)
        elif isinstance(meta, GroupedMetadata):
            found |= _value_constraints(meta)
        elif isinstance(meta, MinLen):
            found["min_length"] = meta.min_length
        elif isinstance(meta, MaxLen):
            found["max_length"] = meta.max_length
        elif isinstance(meta, _PATTERN_METADATA) and "pattern" in vars(meta):
            found["pattern"] = vars(meta)["pattern"]
        elif isinstance(meta, Facet) and meta.pattern is not None:
            found["pattern"] = meta.pattern
    return found


def _field_bounds(
    name: str,
    field_info: FieldInfo,
    annotation: Any,  # type-state: explicit_any — arbitrary typing construct, matches _nested_model_names
) -> dict[str, int | float]:
    """Field-level bounds plus a bound on the sole numeric union arm.

    Numeric keywords ignore non-numbers, so hoisting leaves the other arms free.
    """
    found = _value_bounds(field_info.metadata)
    if get_origin(annotation) not in (Union, types.UnionType):
        return found
    numeric_arms = [
        arm for arm in get_args(annotation) if _unwrap_annotated(arm) in (int, float)
    ]
    for arm in numeric_arms:
        if not hasattr(arm, "__metadata__"):
            continue
        bounds = _value_bounds(arm.__metadata__)
        if bounds and len(numeric_arms) > 1:
            raise TypeError(
                f"field {name!r}: a numeric bound on one of several numeric union "
                "arms cannot be stated in JSON Schema; bound every numeric arm "
                "through Field(...) instead"
            )
        found |= bounds
    return found


_SIZED_ARM_KINDS: dict[type, str] = {
    str: "str",
    list: "list",
    tuple: "tuple",
    set: "set",
    frozenset: "frozenset",
    dict: "dict",
}


def _authored_arms(
    annotation: Any,  # type-state: explicit_any — arbitrary typing construct, matches _nested_model_names
) -> list[Any]:  # type-state: explicit_any — the arms are typing constructs too
    """The union arms an author can write, each with its own metadata kept.

    A `json_schema_input_type` widening is walked in place of the type it
    widens, the same substitution `_unwrap_annotated` makes.
    """
    bare = _unwrap_annotated(annotation)
    if hasattr(annotation, "__metadata__") and bare is not annotation.__origin__:
        return _authored_arms(bare)
    if get_origin(bare) in (Union, types.UnionType):
        return [arm for arg in get_args(bare) for arm in _authored_arms(arg)]
    return [annotation]


def _arm_length_constraints(
    name: str,
    annotation: Any,  # type-state: explicit_any — arbitrary typing construct, matches _nested_model_names
) -> dict[str, dict[str, int]]:
    """Length bounds on individual union arms, keyed by the arm's kind.

    A length keyword binds only its own JSON type, so an arm's bound can sit on
    the field as long as no sibling arm maps to the same keyword unbounded. An
    unwidened single-arm field's bounds are field-level and live in
    `constraints`.
    """
    arms = _authored_arms(annotation)
    if arms == [annotation]:
        return {}
    found: dict[str, dict[str, int]] = {}
    kinds: list[str] = []
    for arm in arms:
        bare = _unwrap_annotated(arm)
        kind = _SIZED_ARM_KINDS.get(get_origin(bare) or bare)
        if kind is None:
            continue
        kinds.append(kind)
        if not hasattr(arm, "__metadata__"):
            continue
        lengths = {
            key: value
            for key, value in _value_constraints(arm.__metadata__).items()
            if key in ("min_length", "max_length") and isinstance(value, int)
        }
        if lengths:
            found[kind] = lengths
    for kind in found:
        if kinds.count(kind) > 1:
            raise TypeError(
                f"field {name!r}: a length bound on one of several {kind} union "
                "arms cannot be stated in JSON Schema; bound them all through "
                "Field(...) instead"
            )
    return found


def _names_a_font_slot(
    annotation: Any,  # type-state: explicit_any — a typing annotation tree
) -> bool:
    """Whether the field holds a model that takes a registry font name."""
    for model_name in _nested_model_names(annotation):
        model = _find_in_annotation(annotation, model_name)
        if (
            isinstance(model, type)
            and issubclass(model, FontSlotBase)
            and model.accepts_name
        ):
            return True
    return False


def _authored_annotation(
    field_info: FieldInfo,
) -> Any:  # type-state: explicit_any — arbitrary typing construct
    """The annotation an author writes against.

    A field-level ``Annotated[X, BeforeValidator(f, json_schema_input_type=T)]``
    leaves ``X`` in ``field_info.annotation`` and the validator in its metadata;
    ``T`` is the shape the coercion accepts, so it is what gets walked.
    """
    for meta in field_info.metadata:
        if isinstance(meta, BeforeValidator) and not isinstance(
            meta.json_schema_input_type, PydanticUndefinedType
        ):
            return meta.json_schema_input_type
    return field_info.annotation


def _is_schema_skipped(
    arm: Any,  # type-state: explicit_any — arbitrary typing construct
) -> bool:
    """Whether a union arm is ``SkipJsonSchema[...]``.

    Pydantic's type-checking stub types SkipJsonSchema as an alias, so the
    metadata instance can only be recognized by its runtime class name.
    """
    if get_origin(arm) is not Annotated:
        return False
    return any(type(m).__name__ == "SkipJsonSchema" for m in get_args(arm)[1:])


def _drop_schema_skipped_arms(
    annotation: Any,  # type-state: explicit_any — arbitrary typing construct
) -> Any:  # type-state: explicit_any — arbitrary typing construct
    """Remove ``SkipJsonSchema[...]`` arms from a top-level union.

    Python accepts the arm but the authored surface must not state it.
    """
    if get_origin(annotation) not in (Union, types.UnionType):
        return annotation
    args = get_args(annotation)
    kept = tuple(a for a in args if not _is_schema_skipped(a))
    if len(kept) == len(args):
        return annotation
    return Union[kept]  # noqa: UP007 — built from a runtime tuple


def _build_schema_field(
    name: str,
    field_info: FieldInfo,
    annotation: Any,  # type-state: explicit_any — a runtime type annotation
    *,
    rejects_null: bool,
) -> SchemaField:
    """Convert one Pydantic field into a SchemaField."""
    annotation = _drop_schema_skipped_arms(annotation)
    from dbt_charts.core.compile.models.markers import (
        Absent,
        AutoUnset,
        Inherit,
        InheritSlot,
    )

    required = field_info.is_required()
    default = field_info.default if not required else dataclasses.MISSING
    metadata = field_info.metadata or []
    if any(isinstance(m, AutoUnset) for m in metadata):
        annotation = Union[annotation, Literal["auto"]]  # noqa: UP007 — built at runtime
    inherit = next((m for m in metadata if isinstance(m, Inherit)), None)
    inherit_slot = next((m for m in metadata if isinstance(m, InheritSlot)), None)
    extra_union_types = _extra_union_types(annotation)
    if "str" not in extra_union_types and _names_a_font_slot(annotation):
        # A font slot's bare-name form is a `mode="before"` coercion
        # (FontSlotBase) that JSON Schema cannot see.
        extra_union_types.append("str")
    return SchemaField(
        name=name,
        description=field_info.description or "",
        type_repr=_type_repr(annotation),
        required=required,
        default=default,
        default_repr=_default_repr(field_info),
        enum_values=_extract_enum_values(annotation),
        nested_models=_nested_model_names(annotation),
        container=_get_container(annotation),
        extra_union_types=extra_union_types,
        extra_union_type_keywords=_extra_union_type_keywords(annotation),
        container_mapping_models=_container_mapping_models(annotation),
        container_list_models=_container_list_models(annotation),
        tuple_item_reprs=_tuple_item_reprs(annotation),
        bounds=_field_bounds(name, field_info, annotation),
        constraints=_value_constraints(field_info.metadata),
        arm_length_constraints=_arm_length_constraints(name, annotation),
        inherit_from=(inherit.from_path,) if inherit is not None else (),
        inherit_slot=inherit_slot.from_path if inherit_slot is not None else None,
        facets=tuple(m for m in metadata if isinstance(m, Facet)),
        null_allowed=not rejects_null or any(isinstance(m, Absent) for m in metadata),
    )


def _collect_models(
    model_cls: type[BaseModel],
    collected: dict[str, AuthorableModel],
) -> None:
    """Recursively walk model_cls and all reachable authored sub-models."""
    name = model_cls.__name__
    if name in collected:
        return

    # Use __doc__ directly (not inspect.getdoc) to avoid MRO walk that pulls in
    # Pydantic BaseModel's "[Models](../concepts/models.md)" doc for generated patches.
    raw_doc = model_cls.__doc__ or ""
    doc = ""
    for line in raw_doc.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith(("!!! ", "??? ", ":::")):
            doc = stripped
            break

    fields: list[SchemaField] = []

    for field_name, field_info in model_cls.model_fields.items():
        # `internal` keeps a field out of the schema: derived values the
        # compiler sets, which authored input must not carry. `exclude=True` only
        # hides a field from serialization; the key is still authorable.
        extra = field_info.json_schema_extra
        if isinstance(extra, dict) and extra.get("internal"):
            continue
        if (
            field_name == "extends"
            and issubclass(model_cls, FontSlotBase)
            and not model_cls.accepts_name
        ):
            continue
        annotation = _authored_annotation(field_info)
        display_name = field_info.alias or field_name
        sf = _build_schema_field(
            display_name,
            field_info,
            annotation,
            rejects_null=issubclass(model_cls, _PatchBase) and model_cls.rejects_null,
        )
        fields.append(sf)

    # Synthesize a '<name>' field for models with typed __pydantic_extra__.
    # get_type_hints() is needed because from __future__ import annotations turns
    # annotations into strings; vars() gives the raw (unresolved) form.
    try:
        hints = typing.get_type_hints(model_cls)
        extra_annotation = hints.get("__pydantic_extra__")
    except NameError:
        extra_annotation = None
    if extra_annotation is not None:
        ea_origin = get_origin(extra_annotation)
        ea_args = get_args(extra_annotation)
        if ea_origin is dict and len(ea_args) == 2:
            value_annotation = ea_args[1]
            fields.append(
                SchemaField(
                    name="<name>",
                    description="Additional named entry.",
                    type_repr=_type_repr(value_annotation),
                    required=False,
                    default=None,
                    default_repr=None,
                    enum_values=None,
                    nested_models=_all_model_names_in_annotation(value_annotation),
                    is_extra_key=True,
                )
            )

    collected[name] = AuthorableModel(
        name=name,
        doc=doc,
        fields=fields,
        generated=issubclass(model_cls, _PatchBase),
        empty_is_valid=_empty_is_valid(model_cls),
        constraints=declared_constraints(model_cls),
        closed=model_cls.model_config.get("extra") == "forbid",
    )

    # An alias name (e.g. "AuthoredChart") has no single class
    # _find_nested_class could locate -- expand it to its known variants
    # instead, so traversal order from this field is identical to a field
    # that names the variants directly.
    for sf in fields:
        for model_name in sf.nested_models:
            if model_name in collected:
                continue
            if model_name in _UNION_ALIAS_VARIANTS:
                for variant_cls in _UNION_ALIAS_VARIANTS[model_name]:
                    _collect_models(variant_cls, collected)
                continue
            nested_cls = _find_nested_class(model_cls, model_name)
            if nested_cls is not None and _is_authored_model(nested_cls):
                _collect_models(nested_cls, collected)


def _empty_is_valid(model_cls: type[BaseModel]) -> bool:
    """Whether `model_cls()` validates — asked of the model, not restated here.

    Required fields are already on `SchemaField`, but a model validator is not:
    `MultiplesConfig` declares every field optional and then refuses an object
    with neither `rows` nor `columns`. Constructing one is the only answer that
    cannot drift from the validator that owns the rule.
    """
    try:
        model_cls()
    except ValidationError:
        return False
    return True


def _find_nested_class(parent_cls: type[BaseModel], class_name: str) -> type | None:
    """Find a nested model class by name from a parent model's field annotations."""
    for field_info in parent_cls.model_fields.values():
        annotation = field_info.annotation
        cls = _find_in_annotation(annotation, class_name)
        if cls is not None:
            return cls
    return None


def _find_in_annotation(annotation: Any, class_name: str) -> type | None:
    """Recursively search annotation tree for a class with the given name."""
    if isinstance(annotation, type) and annotation.__name__ == class_name:
        return annotation
    args = get_args(annotation)
    for arg in args:
        result = _find_in_annotation(arg, class_name)
        if result is not None:
            return result
    return None


@functools.lru_cache(maxsize=1)
def introspect() -> AuthorableSchema:
    """Walk AuthoredBoard and all reachable authored models, returning an IR.

    The returned AuthorableSchema is the single source of truth consumed by
    render_json_schema, render_prompt, render_markdown, and render_erd.

    Memoized: the walk depends only on the installed dbt_charts code, not on
    any input, so the result is a shared read-only instance for the life of
    the process. Callers that need to mutate it must deep-copy first.
    """
    collected: dict[str, AuthorableModel] = {}
    _collect_models(AuthoredBoard, collected)
    # AuthoredBoardInput's SchemaSugar metadata declares authoring-sugar keys
    # (theme:) that have no backing Pydantic field -- a BeforeValidator on the
    # wrapper folds them into a real field ahead of validation, so
    # _collect_models never sees them walking AuthoredBoard.model_fields.
    # Append a synthetic SchemaField per marker so the sugar key still reaches
    # every IR-consuming renderer (JSON Schema, docs, highlight manifest).
    for meta in get_args(AuthoredBoardInput)[1:]:
        if isinstance(meta, SchemaSugar):
            collected["AuthoredBoard"].fields.append(
                SchemaField(
                    name=meta.name,
                    description=meta.description,
                    type_repr=meta.type_repr,
                    required=False,
                    default=None,
                    default_repr=None,
                    enum_values=list(meta.enum_values)
                    if meta.enum_values is not None
                    else None,
                    nested_models=[],
                )
            )
    # Walk extra roots: some (chart/query family classes) already arrived via
    # _UNION_ALIAS_VARIANTS expansion above and are no-ops here (_collect_models
    # returns immediately for a name already in `collected`); others — source
    # connector configs (reached via sources: dict[str, Any], no type
    # annotation to walk) and per-family chart *patch* classes — have no
    # annotation path to AuthoredBoard at all and are only ever reached here.
    from dbt_charts.core.compile.models.chart.authored import (  # noqa: PLC0415
        AreaChart,
        BarChart,
        CalloutChart,
        GeoshapeChart,
        HeatmapChart,
        KpiChart,
        LineChart,
        PieChart,
        PointMapChart,
        ScatterChart,
        SparkBarChart,
        TableChart,
    )
    from dbt_charts.core.compile.models.refs import (  # noqa: PLC0415
        ChartRef,
        QueryRef,
        VariableRef,
    )
    from dbt_charts.core.compile.models.style.theme import Style  # noqa: PLC0415

    for extra in (
        VariableRef,
        QueryRef,
        ChartRef,
        BarChart,
        LineChart,
        AreaChart,
        ScatterChart,
        HeatmapChart,
        PieChart,
        KpiChart,
        TableChart,
        PointMapChart,
        GeoshapeChart,
        CalloutChart,
        SparkBarChart,
        AuthoredSqlQuery,
        AuthoredHttpQuery,
        AuthoredValuesQuery,
        AuthoredCompactValuesQuery,
        AuthoredSchemaQuery,
        PaginationConfig,
        *get_args(SourceConfig),
        # Compiled style root — walks style/theme/ types so inherit-chain metadata
        # (Inherit/InheritSlot markers) is visible in the schema IR.
        Style,
    ):
        _collect_models(extra, collected)
    # Build a synthetic "AuthoredChart" IR entry as a proper discriminated union.
    # fields=[] because the variant family classes (BarChart, LineChart, etc.) are
    # the authorable surface; this entry is a dispatch node only.
    collected["AuthoredChart"] = AuthorableModel(
        name="AuthoredChart",
        doc=(
            "Discriminated union of per-family chart patches. "
            "type: is mandatory; missing or unknown type raises ValidationError."
        ),
        fields=[],
        union=UnionSpec(
            discriminator="type",
            variants=dict(AUTHORED_CHART_VARIANTS),
        ),
    )
    # Query tags are optional in YAML because normalize_query_value infers them
    # from variant-only keys. Keep the synthetic editor/schema node as a union of
    # the concrete shapes instead of flattening their fields; flattening turns a
    # requirement from one variant (such as HTTP url) into a global requirement.
    query_variants = (
        "AuthoredSqlQuery",
        "AuthoredHttpQuery",
        "AuthoredValuesQuery",
        "AuthoredCompactValuesQuery",
        "AuthoredSchemaQuery",
    )
    if all(name in collected for name in query_variants):
        collected["AuthoredQuery"] = AuthorableModel(
            name="AuthoredQuery",
            doc=(
                "Union of per-type authored query shapes. "
                "type: is inferred from key presence when omitted (bare SQL string, "
                "sql: -> sql; "
                "url: -> http; rows:/values: -> values)."
            ),
            fields=[],
            union=UnionSpec(
                discriminator=None,
                variants={name: name for name in query_variants},
            ),
        )
    return AuthorableSchema(root="AuthoredBoard", models=collected)


@functools.lru_cache(maxsize=1)
def introspect_project_config() -> AuthorableSchema:
    """Walk ExecutionConfig — the `dbt_charts.yml` keys — into its own IR.

    Deliberately not part of introspect(): that walk also feeds the board JSON
    Schema, the highlighting catalog and the completion catalog, none of which
    should learn project-config keys. Only the generated field reference reads
    this one.
    """
    collected: dict[str, AuthorableModel] = {}
    _collect_models(ExecutionConfig, collected)
    # Every config field is required on the *merged* model, but dbt_charts.yml
    # is deep-merged over defaults/default_config.yml — what an author writes
    # is a patch, so nothing here is required of them.
    for model in collected.values():
        for field in model.fields:
            field.required = False
    return AuthorableSchema(root="ExecutionConfig", models=collected)
