"""Generic patch-model factory for compiled style/config models.

This module owns two public factories:

``build_patch_model(compiled_cls)``
    All-Optional authored-overlay variant.  Nested BaseModel fields are
    recursively converted to their Patch versions
    (e.g. ``PaddingStyle → PaddingStylePatch | None``).  Use for ``*Patch``
    types in ``authored.py``.

``build_patch_model_ext(compiled_cls, is_recursive)``
    Parameterized variant.  Pass ``is_recursive=False`` to make every field
    ``T | None = None`` while keeping nested BaseModel fields as their
    original compiled types (e.g. ``PaddingStyle | None``).  Use for
    cascade-sentinel bases where cascade fills fields with compiled values.

"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from functools import cache
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Union

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationInfo,
    create_model,
    model_validator,
)

from dbt_charts.core.compile.models.conditional_predicate import (
    PREDICATE_KEYS,
    ConditionalPredicate,
)
from dbt_charts.core.compile.models.constraints import AtLeastOneOf, Constraint
from dbt_charts.core.compile.models.markers import (
    AUTO,
    Absent,
    AutoUnset,
    ChartLocal,
    Color,
    Conditionable,
    InheritSlot,
    NullHint,
    ResetOnSet,
)

FontAlias = Annotated[str, Field(description="Name of a ``style.fonts`` entry.")]


AUTHORED_INPUT = {"authored": True}
# A board re-validated once meta.yml and `extends:` fold under it: each file was
# validated as authored input on its own, and leaves from different files now
# share one mapping.
MERGED_AUTHORED_INPUT = {"authored": True, "merged": True}


class _PatchBase(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    # Only authored input (AUTHORED_INPUT context) rejects an explicit null:
    # internal round trips through a patch carry None on every unset leaf.
    rejects_null: ClassVar[bool] = True

    @model_validator(mode="before")
    @classmethod
    def _reject_null_on_valued_fields(
        cls,
        value: Any,  # type-state: explicit_any — raw pre-validation patch value
        info: ValidationInfo,
    ) -> Any:  # type-state: explicit_any — passes non-mapping input through
        if (
            not cls.rejects_null
            or not isinstance(value, dict)
            or not isinstance(info.context, dict)
            or not info.context.get("authored")
        ):
            return value
        for name, field in cls.model_fields.items():
            key = name if field.alias is None else field.alias
            if (
                key in value
                and value[key] is None
                and not any(isinstance(meta, Absent) for meta in field.metadata)
            ):
                hints = [m.instead for m in field.metadata if isinstance(m, NullHint)]
                raise ValueError(
                    f"{name}: null is not accepted here. Omit the key to leave "
                    f"the field unset. {' '.join(hints)}".rstrip()
                )
        return value


class ChartScopeBase(_PatchBase):
    """Base of ``ChartsStylePatch``, the theme and board scope of ``style.charts``.

    Refuses a ``ChartLocal()`` field of a family patch: it names things only one
    chart has, so it is authored on that chart's own ``style``.
    """

    @model_validator(mode="after")
    def _reject_chart_local_fields(self) -> ChartScopeBase:
        for family, family_patch in self:
            if isinstance(family_patch, BaseModel):
                if (path := _chart_local_path(family_patch)) is not None:
                    raise ValueError(
                        f"`{path}` is chart-local: author it under a chart's "
                        f"`style`, not at `style.charts.{family}`"
                    )
        return self


def _chart_local_path(model: BaseModel) -> str | None:
    """Dotted path of the first set ``ChartLocal()`` field in *model*, nested
    models and the models of a mapping field included."""
    for name, field in type(model).model_fields.items():
        value = getattr(model, name)
        if value is None:
            continue
        if any(isinstance(meta, ChartLocal) for meta in field.metadata):
            return name
        children = (
            {str(key): child for key, child in value.items()}
            if isinstance(value, dict)
            else {name: value}
        )
        for label, child in children.items():
            if (
                isinstance(child, BaseModel)
                and (nested := _chart_local_path(child)) is not None
            ):
                return (
                    f"{name}.{nested}" if child is value else f"{name}.{label}.{nested}"
                )
    return None


class BoardPatchBase(_PatchBase):
    """Base of ``BoardPatch``: a board-level null clears what a meta.yml or an
    ``extends`` parent set."""

    rejects_null: ClassVar[bool] = False


class FontSlotBase(_PatchBase):
    """Base of ``FontStyle`` and every patch synthesized from a subclass of it.

    A font slot takes a registry name: a bare string is ``{extends: name}``.
    """

    # False for the root font: it accepts no name and the schema offers no
    # ``extends`` on it.
    accepts_name: ClassVar[bool] = True

    # A slot naming a font is that font plus its own overrides, so a lower
    # layer's FontStyle fields do not survive; fields only a subclass declares
    # (``compact_size``, ``emoji``) are not part of a named font and do.
    merge_reset: ClassVar[ResetOnSet] = ResetOnSet(
        "extends",
        frozenset(
            {
                "case",
                "color",
                "decoration",
                "extends",
                "family",
                "line_height",
                "size",
                "style",
                "weight",
            }
        ),
    )

    if TYPE_CHECKING:
        # Declared as a field by FontStyle and by every patch synthesized from it.
        extends: FontAlias | None

    @model_validator(mode="before")
    @classmethod
    def _coerce_bare_name(
        cls,
        value: Any,  # type-state: explicit_any — raw pre-validation slot value
    ) -> Any:  # type-state: explicit_any — passes non-string input through
        return {"extends": value} if isinstance(value, str) else value


# Maps compiled class → its hand-written patch class, consulted before synthesis.
# Prevents build_patch_model from synthesizing a duplicate when a hand-written patch
# already exists for a compiled type (e.g. BorderStyle → BorderStylePatch).
_PATCH_REGISTRY: dict[type, type] = {}


def register_patch(compiled_cls: type[BaseModel]) -> Callable[[type], type]:
    """Decorator: register a hand-written patch class for compiled_cls.

    Apply to the patch class so the registration lives next to the definition:

        @register_patch(BorderStyle)
        class BorderStylePatch(BaseModel): ...
    """

    def decorator(patch_cls: type) -> type:
        _PATCH_REGISTRY[compiled_cls] = patch_cls
        return patch_cls

    return decorator


def register_as_own_patch(cls: type[BaseModel]) -> type[BaseModel]:
    """Register cls as its own patch (already all-optional, no compiled counterpart).

    Use for models that are authored-only patch shapes with no corresponding compiled
    class — e.g. ``BorderStylePatch``.  Prevents ``build_patch_model`` from recursively
    synthesizing a ``BorderStylePatchPatch`` when it encounters the type as a field.
    """
    _PATCH_REGISTRY[cls] = cls
    return cls


# A shorthand parser belongs to the patch type, not to any compiled field, so it
# (and the ``json_schema_input_type`` schema introspection reads) is attached
# wherever a patch field of that class is generated.
PATCH_SHORTHAND: dict[type, BeforeValidator] = {}


def register_shorthand(patch_cls: type, validator: BeforeValidator) -> None:
    PATCH_SHORTHAND[patch_cls] = validator


def _patch_or_none(
    compiled_cls: type[BaseModel],
) -> Any:  # type-state: explicit_any — runtime-built annotation
    patch_cls = build_patch_model_ext(compiled_cls, is_recursive=True)
    shorthand = PATCH_SHORTHAND.get(patch_cls)
    if shorthand is None:
        return patch_cls | None
    return Union[Annotated[patch_cls, shorthand], None]  # noqa: UP007


def _is_patch_shaped(cls: type[BaseModel]) -> bool:
    """True for a patch model: a ``_PatchBase`` whose every field defaults to None.

    A compiled class built on all-optional bases can lack required fields yet
    still carry real defaults (a ``default_factory`` section); recursing into
    it is what keeps its nested fields patchable.
    """
    return issubclass(cls, _PatchBase) and all(
        fi.default is None for fi in cls.model_fields.values()
    )


def _is_extra_allow_section(cls: type) -> bool:
    """True for a BaseModel that is an ``extra="allow"`` keyed-collection section.

    Such a section stores its real payload as named extras (``<field>.<name>: {...}``)
    and carries ``Merge(Strategy.BY_KEY)`` on the parent field — i.e. it merges as a
    dict keyed by name, not field-by-field. The faithful patch shape for such a
    section is a plain ``dict``: it accepts any named entry (extra="forbid" patches
    would reject them) and ``merge_patches`` merges it natively via the by_key
    branch (``{**lower, **upper}``).
    """
    return issubclass(cls, BaseModel) and cls.model_config.get("extra") == "allow"


def _is_optional(annotation: Any) -> bool:
    """Return True if annotation already includes NoneType."""
    args = getattr(annotation, "__args__", ())
    return type(None) in args


def _unwrap_optional_base_model(annotation: Any) -> type[BaseModel] | None:
    """If annotation is `T | None` where T is a single BaseModel subclass, return T; else None."""
    if not _is_optional(annotation):
        return None
    inner_types = [a for a in annotation.__args__ if a is not type(None)]
    if len(inner_types) != 1:
        return None
    inner = inner_types[0]
    if isinstance(inner, type) and issubclass(inner, BaseModel):
        return inner
    return None


def _make_patch_annotation(annotation: Any, *, is_recursive: bool) -> Any:
    """Make annotation Optional, optionally recursing into nested BaseModel fields."""
    if annotation is None or annotation is type(None):
        return type(None)
    # Any | None ≡ Any — wrapping is vacuous and misleads type-checkers.
    if annotation is Any:
        return Any
    if is_recursive:
        # T | None where T is a BaseModel: recurse to produce PatchT | None.
        # Without this, the compiled T (with required fields) ends up in the patch
        # annotation instead of the all-Optional patch variant.
        if inner := _unwrap_optional_base_model(annotation):
            if _is_extra_allow_section(inner):
                return dict[str, Any] | None
            if _is_patch_shaped(inner):
                return inner | None
            return _patch_or_none(inner)
        if _is_optional(annotation):
            # Union with None but inner is not a single BaseModel — keep as-is.
            return annotation
        # Bare BaseModel subclass → recurse to get its patch version.
        # Patch-shaped types are returned as-is; a compiled type with a required
        # field (RootFontStyle's emoji) or a real default is recursed into.
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if _is_extra_allow_section(annotation):
                return dict[str, Any] | None
            if _is_patch_shaped(annotation):
                return annotation | None
            return _patch_or_none(annotation)
    else:
        # Non-recursive: already-optional annotations pass through unchanged;
        # everything else falls to the `annotation | None` below.
        if _is_optional(annotation):
            return annotation
    # Scalars, list[X], Union types without None, and (when non-recursive) BaseModel
    # subclasses all land here — just wrap with None.
    return annotation | None


@cache
def build_patch_model_ext(
    compiled_cls: type[BaseModel],
    is_recursive: bool = True,
    base_cls: type[BaseModel] | None = None,
) -> type[BaseModel]:
    """Generate an all-Optional model from a compiled model.

    Args:
        compiled_cls: The compiled Pydantic model to patch.
        is_recursive: When True (default), nested BaseModel fields are
            recursively converted to their Patch versions
            (e.g. ``PaddingStyle → PaddingStylePatch | None``).
            When False, every field becomes ``T | None = None`` while keeping
            original compiled types for nested BaseModels
            (e.g. ``PaddingStyle | None``).  Use False for cascade-sentinel
            bases where the cascade fills fields with compiled-type values.
        base_cls: Override the generated model's base class. Defaults to
            ``_PatchBase``. Pass a subclass of ``_PatchBase`` to inject
            validators into the generated patch model (e.g.
            ``ScaleDomainValidationMixin``, ``ScaleTargetPaletteValidationMixin``).

    Returns:
        A dynamically created BaseModel subclass named ``<OriginalName>Patch``
        with all fields Optional and defaulting to None.
    """
    if is_recursive and compiled_cls in _PATCH_REGISTRY:
        return _PATCH_REGISTRY[compiled_cls]
    fields: dict[str, Any] = {}
    for name, field_info in compiled_cls.model_fields.items():
        if field_info.exclude:
            continue
        patch_annotation = _make_patch_annotation(
            field_info.annotation, is_recursive=is_recursive
        )
        # A registered shorthand already rides on the recursed patch type;
        # applying it twice re-desugars its own output.
        metadata = [
            m
            for m in field_info.metadata
            if not (is_recursive and m in PATCH_SHORTHAND.values())
        ]
        if metadata:
            patch_annotation = Annotated[patch_annotation, *metadata]
        fields[name] = (
            patch_annotation,
            Field(
                default=None,
                description=field_info.description,
                alias=field_info.alias,
            ),
        )

    if base_cls is not None:
        actual_base = base_cls
    elif issubclass(compiled_cls, FontSlotBase):
        actual_base = FontSlotBase
    else:
        actual_base = _PatchBase
    patch_name = f"{compiled_cls.__name__}Patch"
    patch_cls = create_model(
        patch_name,
        __base__=actual_base,
        __module__=compiled_cls.__module__,
        **fields,
    )
    compiled_doc = (compiled_cls.__doc__ or "").strip()
    patch_cls.__doc__ = (
        f"Authored overlay for {compiled_cls.__name__}. {compiled_doc}".strip()
    )
    return patch_cls


def build_patch_model(compiled_cls: type[BaseModel]) -> type[BaseModel]:
    """Generate an all-Optional recursive patch model from a compiled model.

    Equivalent to ``build_patch_model_ext(compiled_cls, is_recursive=True)``.
    Nested BaseModel fields are recursively converted to their Patch versions.
    """
    return build_patch_model_ext(compiled_cls, is_recursive=True)


def _model_arm(
    annotation: Any,  # type-state: explicit_any — a field annotation, any typing construct
) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return _unwrap_optional_base_model(annotation)


def _without_auto(
    marker: Any,  # type-state: explicit_any — one annotated-metadata item, any type
) -> Any:  # type-state: explicit_any — the same item
    """A rule sets a value a row carries, never ``auto``: a color keyword is dropped."""
    if isinstance(marker, Color):
        return dataclasses.replace(
            marker, keywords=tuple(k for k in marker.keywords if k != AUTO)
        )
    return marker


def _refuse_auto(
    value: Any,  # type-state: explicit_any — any rule value, returned unchanged
) -> Any:  # type-state: explicit_any — the value it was given
    # A free-form color string would otherwise take `auto` as a color name.
    if value == AUTO:
        raise ValueError(f"a conditional rule names a value; {AUTO!r} is not one")
    return value


@cache
def _conditional_class(
    style_cls: type[BaseModel],
    only: tuple[str, ...],
    whole: bool,
    base_cls: type[BaseModel],
    class_name: str | None = None,
) -> type[BaseModel] | None:
    """The ``Conditionable`` part of ``style_cls`` as an all-optional model, or None.

    ``whole`` keeps every field (named in ``only``, when given) of a subtree a
    ``Conditionable()`` marker claimed; otherwise only marked fields and the
    parents leading to them are kept.
    """
    fields: dict[str, Any] = {}  # type-state: explicit_any — create_model field specs
    required: dict[str, Any] = {}  # type-state: explicit_any — runtime annotations
    for name, field_info in style_cls.model_fields.items():
        # A font slot can always name a registered font, as in static styling.
        names_font = name == "extends" and issubclass(style_cls, FontSlotBase)
        if (only and name not in only and not names_font) or field_info.exclude:
            continue
        annotation: Any  # type-state: explicit_any — a runtime-built annotation
        marker = next(
            (m for m in field_info.metadata if isinstance(m, Conditionable)), None
        )
        keep_whole = whole or marker is not None
        nested = _model_arm(field_info.annotation)
        if marker is not None and marker.rule_type is not None:
            annotation = marker.rule_type
        elif nested is None:
            if not keep_whole:
                continue
            annotation = field_info.annotation
        else:
            kept = _conditional_class(
                nested,
                marker.only if marker is not None else (),
                keep_whole,
                FontSlotBase if issubclass(nested, FontSlotBase) else _PatchBase,
            )
            if kept is None:
                continue
            annotation = kept
        metadata = [
            _without_auto(m)
            for m in field_info.metadata
            if not isinstance(m, Conditionable | InheritSlot | AutoUnset)
        ]
        if any(
            isinstance(m, Color) and AUTO in m.keywords for m in field_info.metadata
        ):
            metadata.append(AfterValidator(_refuse_auto))
        required[name] = Annotated[annotation, *metadata] if metadata else annotation
        if not _is_optional(annotation):
            annotation = annotation | None
        if metadata:
            annotation = Annotated[annotation, *metadata]
        fields[name] = (
            annotation,
            Field(
                default=None,
                description=field_info.description,
                alias=field_info.alias,
            ),
        )
    if not fields:
        return None
    if issubclass(base_cls, ConditionalPredicate):
        # A rule that restyles nothing is a mistake.
        styles = (
            *(
                n
                for n in base_cls.model_fields
                if n not in ConditionalPredicate.model_fields
            ),
            *(n for n in fields if n not in PREDICATE_KEYS),
        )
        if len(styles) == 1 and styles[0] in fields:
            # One style key cannot be "at least one of": it is required outright.
            info = style_cls.model_fields[styles[0]]
            fields[styles[0]] = (
                required[styles[0]],
                Field(description=info.description, alias=info.alias),
            )
        else:
            fields["__constraints__"] = (
                ClassVar[tuple[Constraint, ...]],
                (
                    AtLeastOneOf(
                        styles,
                        "A conditional rule requires at least one style to apply",
                    ),
                ),
            )
    return create_model(
        f"{style_cls.__name__}Conditional" if class_name is None else class_name,
        __base__=base_cls,
        __module__=style_cls.__module__,
        __doc__=f"The part of {style_cls.__name__} a conditional rule may set.",
        **fields,
    )


def build_conditional_model(
    style_cls: type[BaseModel],
    predicate_cls: type[ConditionalPredicate] = ConditionalPredicate,
    name: str | None = None,
) -> type[BaseModel]:
    """A conditional rule type for a chart family: one predicate plus a style body.

    The body is ``style_cls`` restricted to its ``Conditionable()`` fields, all
    optional, so a rule can name only what may vary per row. The class is named
    ``<style_cls>Conditional``, apart from ``<style_cls>Patch`` in the schema.
    ``predicate_cls`` is the rule's base: a ``ConditionalPredicate`` subclass adds
    the keys only a rule sets. ``name`` publishes the rule under a different class
    name, for a ``style_cls`` that is a private base of the class carrying the
    ``conditional`` field.
    """
    rule_cls = _conditional_class(style_cls, (), False, predicate_cls, name)
    if rule_cls is None:
        raise TypeError(f"{style_cls.__name__} has no Conditionable() field")
    return rule_cls


def build_conditional_body(
    style_cls: type[BaseModel], base_cls: type[BaseModel], name: str
) -> type[BaseModel]:
    """The ``Conditionable()`` part of ``style_cls`` with no predicate: the style a
    rule applies to one named target (a table row rule's ``columns.<col>``).

    ``base_cls`` carries the keys only a rule sets, as ``build_conditional_model``'s
    ``predicate_cls`` does.
    """
    body_cls = _conditional_class(style_cls, (), False, base_cls, name)
    if body_cls is None:
        raise TypeError(f"{style_cls.__name__} has no Conditionable() field")
    return body_cls
