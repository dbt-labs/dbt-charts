"""Cross-field rules a model declares once, for parse and the JSON Schema alike.

A model lists its rules in ``__constraints__``. ``ConstrainedModel`` checks
every rule declared along its MRO after field validation, base classes first,
and the JSON Schema renderer emits the same rules, so an editor and the parser
cannot disagree about them.

Keys are YAML keys (a field's alias where it has one); a dotted key reaches
into a nested object. A key is *set* when its value is present and not null; a
key a rule lists in ``nonempty`` must also not be an empty string, list, or
object. A rule naming a key no field carries fails at import.

A message is a ``str.format`` template: ``{set}`` is the list of set keys
(``AtMostOneOf``), ``{item}`` the offending item (``ItemsMatch``), and any other
name is the value of that key.
"""

from __future__ import annotations

import dataclasses
import re
from typing import ClassVar, get_args

from pydantic import BaseModel, ConfigDict, model_validator

JsonScalar = str | int | float | bool


@dataclasses.dataclass(frozen=True)
class IsSet:
    """Holds when ``key`` is set."""

    key: str


@dataclasses.dataclass(frozen=True)
class ValueIn:
    """Holds when the value at ``key`` is one of ``values`` (or, negated, is not).
    An absent key reads as ``None``."""

    key: str
    values: tuple[JsonScalar | None, ...]
    negate: bool = False


@dataclasses.dataclass(frozen=True)
class IsList:
    """Holds when the value at ``key`` is a list."""

    key: str


Condition = IsSet | ValueIn | IsList


@dataclasses.dataclass(frozen=True)
class AtLeastOneOf:
    keys: tuple[str, ...]
    message: str
    nonempty: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class AtMostOneOf:
    """At most one key is set. A key in ``auto`` counts as unset while it reads
    ``auto``: the engine decides it, so it is not a second authored value."""

    keys: tuple[str, ...]
    message: str
    nonempty: tuple[str, ...] = ()
    auto: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class Requires:
    """When every condition holds, every key must be set."""

    keys: tuple[str, ...]
    message: str
    when: tuple[Condition, ...] = ()
    nonempty: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class Forbids:
    """When every condition holds, no key may be set."""

    keys: tuple[str, ...]
    message: str
    when: tuple[Condition, ...] = ()
    nonempty: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class ItemsMatch:
    """When every condition holds, each item of the list at ``key`` is a string
    fully matching ``pattern`` (ECMA-262 compatible, anchored with ``^...$``)."""

    key: str
    pattern: str
    message: str
    when: tuple[Condition, ...] = ()


@dataclasses.dataclass(frozen=True)
class NotNull:
    """No key may be written as null: omit it instead.

    YAML reads a bare ``key:`` as null, which a nullable field would take for
    "unset" and quietly apply the default.
    """

    keys: tuple[str, ...]
    message: str


Constraint = AtLeastOneOf | AtMostOneOf | Requires | Forbids | ItemsMatch | NotNull


def declared_constraints(model_cls: type[BaseModel]) -> tuple[Constraint, ...]:
    """Every rule declared on ``model_cls`` and its bases, base classes first."""
    return tuple(
        constraint
        for klass in reversed(model_cls.__mro__)
        for constraint in vars(klass).get(
            "__constraints__", ()
        )  # type-state: silent_fallback — a class declaring no rules adds none
    )


def _value_at(
    model: BaseModel, key: str
) -> object:  # type-state: object_annotation — any authored field value, narrowed by isinstance at use
    value: object = model  # type-state: object_annotation — any authored field value, narrowed by isinstance at use
    for part in key.split("."):
        # A non-object (null, or a scalar arm like `mirror: true`) has no keys.
        if not isinstance(value, BaseModel):
            return None
        fields = type(value).model_fields
        name = next(n for n, f in fields.items() if (f.alias or n) == part)
        value = getattr(value, name)
    return value


def _models_in(
    annotation: object,  # type-state: object_annotation — any typing construct
) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [m for arg in get_args(annotation) for m in _models_in(arg)]


def _resolves(model_cls: type[BaseModel], key: str) -> bool:
    """Whether ``key`` names a field on ``model_cls`` and, past a dot, on every
    model arm of that field's type."""
    classes = [model_cls]
    for part in key.split("."):
        if not classes:
            return False
        names = [
            next((n for n, f in c.model_fields.items() if (f.alias or n) == part), "")
            for c in classes
        ]
        if not all(names):
            return False
        classes = [
            m
            for c, name in zip(classes, names, strict=True)
            for m in _models_in(c.model_fields[name].annotation)
        ]
    return True


def _keys(constraint: Constraint) -> tuple[str, ...]:
    match constraint:
        case ItemsMatch(key, _, _, when):
            return (key, *(c.key for c in when))
        case NotNull(keys, _):
            return keys
        case AtMostOneOf(keys, _, nonempty, auto):
            return (*keys, *nonempty, *auto)
        case AtLeastOneOf(keys, _, nonempty):
            return (*keys, *nonempty)
        case Requires(keys, _, when, nonempty) | Forbids(keys, _, when, nonempty):
            return (*keys, *(c.key for c in when), *nonempty)


def _is_set(model: BaseModel, key: str, nonempty: bool, auto: bool = False) -> bool:
    value = _value_at(model, key)
    if value is None or (auto and value == "auto"):
        return False
    if not nonempty:
        return True
    if isinstance(value, BaseModel):
        return bool(value.model_fields_set)
    if isinstance(value, (str, list, tuple, dict)):
        return len(value) > 0
    return True


def _holds(model: BaseModel, condition: Condition) -> bool:
    match condition:
        case IsSet(key):
            return _is_set(model, key, nonempty=False)
        case ValueIn(key, values, negate):
            return (_value_at(model, key) in values) != negate
        case IsList(key):
            return isinstance(_value_at(model, key), (list, tuple))


class _Placeholders(dict[str, object]):
    def __init__(
        self,
        model: BaseModel,
        **known: object,  # type-state: object_annotation — str.format values
    ) -> None:
        super().__init__(known)
        self._model = model

    def __missing__(
        self, key: str
    ) -> object:  # type-state: object_annotation — any authored field value, narrowed by isinstance at use
        return _value_at(self._model, key)


def _fail(
    model: BaseModel,
    message: str,
    **known: object,  # type-state: object_annotation — str.format values
) -> ValueError:
    return ValueError(message.format_map(_Placeholders(model, **known)))


def check_constraints(model: BaseModel) -> None:
    """Raise ``ValueError`` with the first violated rule's message."""
    for constraint in declared_constraints(type(model)):
        match constraint:
            case AtLeastOneOf(keys, message, nonempty):
                if not any(_is_set(model, k, k in nonempty) for k in keys):
                    raise _fail(model, message)
            case AtMostOneOf(keys, message, nonempty, auto):
                set_keys = [
                    k for k in keys if _is_set(model, k, k in nonempty, k in auto)
                ]
                if len(set_keys) > 1:
                    raise _fail(model, message, set=set_keys)
            case Requires(keys, message, when, nonempty):
                if all(_holds(model, c) for c in when) and not all(
                    _is_set(model, k, k in nonempty) for k in keys
                ):
                    raise _fail(model, message)
            case Forbids(keys, message, when, nonempty):
                if all(_holds(model, c) for c in when) and any(
                    _is_set(model, k, k in nonempty) for k in keys
                ):
                    raise _fail(model, message)
            case NotNull(keys, message):
                for key in keys:
                    name = next(
                        n
                        for n, f in type(model).model_fields.items()
                        if (f.alias or n) == key
                    )
                    if name in model.model_fields_set and getattr(model, name) is None:
                        raise _fail(model, message)
            case ItemsMatch(key, pattern, message, when):
                items = _value_at(model, key)
                if not isinstance(items, (list, tuple)) or not all(
                    _holds(model, c) for c in when
                ):
                    continue
                for item in items:
                    if not isinstance(item, str) or not re.fullmatch(pattern, item):
                        raise _fail(model, message, item=item)


class ConstrainedModel(BaseModel):
    """Base for a model that declares ``__constraints__``."""

    model_config = ConfigDict(extra="forbid")

    __constraints__: ClassVar[tuple[Constraint, ...]] = ()

    @classmethod
    def __pydantic_init_subclass__(
        cls,
        **kwargs: object,  # type-state: object_annotation — pydantic passthrough kwargs
    ) -> None:
        """Reject a rule naming a key no field carries, so a typo fails at import.

        Every key, and every segment of a dotted key, must name a field. A
        field-less mixin (``ScaleDomainValidationMixin``) carries rules for the
        models that inherit it, so its keys are checked on those models.
        """
        super().__pydantic_init_subclass__(**kwargs)
        if not cls.model_fields:
            return
        for constraint in declared_constraints(cls):
            if isinstance(constraint, NotNull) and any(
                "." in k for k in constraint.keys
            ):
                raise TypeError(
                    f"{cls.__name__}: NotNull takes top-level keys, got {constraint.keys}"
                )
            missing = [k for k in _keys(constraint) if not _resolves(cls, k)]
            if missing:
                raise TypeError(
                    f"{cls.__name__} declares a constraint on {missing}, "
                    "which name no field"
                )

    @model_validator(mode="after")
    def _check_constraints(self) -> ConstrainedModel:
        check_constraints(self)
        return self
