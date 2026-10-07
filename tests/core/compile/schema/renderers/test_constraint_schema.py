"""Cross-field constraints render to JSON Schema that agrees with the Python check."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from dbt_charts.core.compile.models.constraints import (
    AtLeastOneOf,
    AtMostOneOf,
    ConstrainedModel,
    Constraint,
    Forbids,
    IsList,
    IsSet,
    ItemsMatch,
    Requires,
    ValueIn,
)
from dbt_charts.core.compile.schema.renderers.json_schema import constraint_schema


class _Inner(ConstrainedModel):
    c: str | None = None


class _Probe(ConstrainedModel):
    __constraints__: ClassVar[tuple[Constraint, ...]] = (
        AtLeastOneOf(("a", "inner.c", "items"), "need one", nonempty=("items",)),
        AtMostOneOf(("a", "b"), "not both: {set}"),
        Requires(("b",), "kind x needs b", when=(ValueIn("kind", ("x",)),)),
        Forbids(
            ("a",),
            "list items forbid a unless kind y",
            when=(IsList("items"), ValueIn("kind", ("y",), negate=True)),
            nonempty=("a",),
        ),
        Requires(("kind",), "flag needs kind", when=(IsSet("flag"),)),
        ItemsMatch(
            "items", "^[0-9]+$", "{item!r} is not digits", when=(IsList("items"),)
        ),
    )

    a: str | None = None
    b: str | None = None
    kind: str | None = None
    flag: bool | None = None
    inner: _Inner | None = None
    items: list[str] | str | None = None


_VALIDATOR = Draft202012Validator(
    {
        "type": "object",
        "properties": {"inner": {"type": ["object", "null"]}},
        **constraint_schema(_Probe.__constraints__),
    }
)

_CASES: list[dict[str, Any]] = [
    {},
    {"a": "x"},
    {"a": ""},
    {"a": None, "inner": {"c": "x"}},
    {"inner": None},
    {"inner": {}},
    {"a": "x", "b": "y"},
    {"b": "y", "kind": "x"},
    {"a": "x", "kind": "x"},
    {"items": ["1"], "a": "x"},
    {"items": [], "a": ""},
    {"items": []},
    {"items": ["1"], "a": "x", "kind": "y"},
    {"items": ["1", "z"]},
    {"items": "z"},
    {"a": "x", "flag": False},
    {"a": "x", "flag": False, "kind": "z"},
]


@pytest.mark.parametrize("payload", _CASES, ids=[str(c) for c in _CASES])
def test_schema_verdict_matches_python(payload: dict[str, Any]) -> None:
    try:
        _Probe.model_validate(payload)
        python_ok = True
    except ValidationError:
        python_ok = False
    assert _VALIDATOR.is_valid(payload) == python_ok


def test_messages_fill_placeholders() -> None:
    with pytest.raises(ValidationError, match=r"not both: \['a', 'b'\]"):
        _Probe.model_validate({"a": "x", "b": "y"})
    with pytest.raises(ValidationError, match="'z' is not digits"):
        _Probe.model_validate({"items": ["1", "z"]})


def test_unconstrained_model_renders_nothing() -> None:
    assert constraint_schema(()) == {}


def test_misspelled_constraint_key_fails_at_class_creation() -> None:
    with pytest.raises(TypeError, match=r"constraint on \['inner.d'\]"):

        class _Typo(ConstrainedModel):
            __constraints__: ClassVar[tuple[Constraint, ...]] = (
                AtLeastOneOf(("a", "inner.d"), "need one"),
            )

            a: str | None = None
            inner: _Inner | None = None


def test_single_key_rule_typo_fails_at_class_creation() -> None:
    with pytest.raises(TypeError, match=r"constraint on \['colums'\]"):

        class _Typo(ConstrainedModel):
            __constraints__: ClassVar[tuple[Constraint, ...]] = (
                AtLeastOneOf(("colums",), "need columns"),
            )

            columns: str | None = None


def test_fieldless_mixin_rules_are_checked_on_the_inheriting_model() -> None:
    class _Mixin(ConstrainedModel):
        __constraints__: ClassVar[tuple[Constraint, ...]] = (
            AtLeastOneOf(("a",), "need a"),
        )

    class _Ok(_Mixin):
        a: str | None = None

    with pytest.raises(TypeError, match=r"constraint on \['a'\]"):

        class _Missing(_Mixin):
            b: str | None = None


class _OtherInner(ConstrainedModel):
    d: str | None = None


def test_dotted_key_must_resolve_on_every_union_arm() -> None:
    with pytest.raises(TypeError, match=r"constraint on \['inner.c'\]"):

        class _Partial(ConstrainedModel):
            __constraints__: ClassVar[tuple[Constraint, ...]] = (
                AtLeastOneOf(("inner.c",), "need c"),
            )

            inner: _Inner | _OtherInner | None = None


def test_misspelled_nonempty_key_fails_at_class_creation() -> None:
    with pytest.raises(TypeError, match=r"constraint on \['bb'\]"):

        class _Typo(ConstrainedModel):
            __constraints__: ClassVar[tuple[Constraint, ...]] = (
                AtLeastOneOf(("b",), "need b", nonempty=("bb",)),
            )

            b: str | None = None
