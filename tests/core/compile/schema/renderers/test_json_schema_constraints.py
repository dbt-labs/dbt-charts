"""The rendered JSON Schema keeps the constraints Pydantic enforces.

Each case validates one value against a throwaway model and against the schema
rendered from it; the two verdicts must agree.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import pytest
from jsonschema.validators import validator_for
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)

from dbt_charts.core.compile.schema.introspection import (
    AuthorableModel,
    AuthorableSchema,
    SchemaField,
    _arm_length_constraints,
    _collect_models,
    introspect,
)
from dbt_charts.core.compile.schema.renderers.json_schema import (
    _constraint_keywords,
    render_yaml_schema,
)


class _Containers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tags: list[str] | None = None
    headers: dict[str, str] | None = None
    rows: list[dict[str, str | int | None]] | None = None
    mixed: list[str | int] | None = None
    nested: list[list[int]] | None = None


class _Bounds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int | None = Field(default=None, gt=0)
    opacity: float | None = Field(default=None, ge=0, le=1)
    fraction: float | None = Field(default=None, gt=0, lt=1)
    entries: list[str] | None = Field(default=None, min_length=1, max_length=2)
    mapping: dict[str, str] | None = Field(default=None, min_length=1)
    code: str | None = Field(default=None, pattern="^a", min_length=2, max_length=3)
    pixels: Annotated[int, Field(gt=0)] | str | None = None


class _PatternArm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gap: float | Annotated[str, StringConstraints(pattern=r"^\d+px$")] | None = None


class _Sized(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: set[str] | None = Field(default=None, min_length=1)
    frozen_ids: frozenset[str] | None = Field(default=None, min_length=1)


class _ItemBounds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Annotated[int, Field(gt=0)]] | None = None
    items_or_int: list[Annotated[int, Field(gt=0)]] | int | None = None
    pair: tuple[Annotated[int, Field(gt=0)], Annotated[int, Field(gt=0)]] | None = None
    bare_items: list[Annotated[int, Field(gt=0)]] = []
    bare_pair: tuple[Annotated[int, Field(gt=0)], Annotated[int, Field(gt=0)]] = (
        1,
        1,
    )


class _ArmLength(BaseModel):
    model_config = ConfigDict(extra="forbid")
    one: str | Annotated[list[str], Field(min_length=1, max_length=1)] | None = None
    name: Annotated[str, Field(min_length=2)] | list[str] | None = None


class _Entries(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[str] = Field(min_length=1)


def _wrap_bare_list(value: object) -> object:
    return {"entries": value} if isinstance(value, list) else value


class _WidenedArm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block: (
        Annotated[
            _Entries,
            BeforeValidator(
                _wrap_bare_list,
                json_schema_input_type=Annotated[list[str], Field(min_length=1)]
                | _Entries,
            ),
        ]
        | None
    ) = None


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")
    a: int | None = None


class _Ignoring(BaseModel):
    model_config = ConfigDict(extra="ignore")
    a: int | None = None


class _IntEnum(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clock: Literal[24, 12] | None = None


def _schema_accepts(model: type[BaseModel], board: dict[str, Any]) -> bool:
    collected: dict[str, AuthorableModel] = {}
    _collect_models(model, collected)
    schema = render_yaml_schema(AuthorableSchema(root=model.__name__, models=collected))
    return validator_for(schema)(schema).is_valid(board)


def _pydantic_accepts(model: type[BaseModel], board: dict[str, Any]) -> bool:
    try:
        model.model_validate(board)
    except ValidationError:
        return False
    return True


_CASES: list[tuple[type[BaseModel], dict[str, Any]]] = [
    (_Containers, {"tags": ["a"]}),
    (_Containers, {"tags": [1]}),
    (_Containers, {"headers": {"a": "b"}}),
    (_Containers, {"headers": {"a": 1}}),
    (_Containers, {"rows": [{"a": 1, "b": None, "c": "x"}]}),
    (_Containers, {"rows": [1]}),
    (_Containers, {"rows": [{"a": [1]}]}),
    (_Containers, {"mixed": ["a", 1]}),
    (_Containers, {"mixed": [None]}),
    (_Containers, {"nested": [[1]]}),
    (_Containers, {"nested": [["a"]]}),
    (_Bounds, {"width": 1}),
    (_Bounds, {"width": 0}),
    (_Bounds, {"opacity": 0}),
    (_Bounds, {"opacity": 1}),
    (_Bounds, {"opacity": 1.5}),
    (_Bounds, {"opacity": -0.1}),
    (_Bounds, {"fraction": 0.5}),
    (_Bounds, {"fraction": 1}),
    (_Bounds, {"fraction": 0}),
    (_Bounds, {"entries": ["a"]}),
    (_Bounds, {"entries": []}),
    (_Bounds, {"entries": ["a", "b", "c"]}),
    (_Bounds, {"mapping": {"a": "b"}}),
    (_Bounds, {"mapping": {}}),
    (_Bounds, {"code": "ab"}),
    (_Bounds, {"code": "ba"}),
    (_Bounds, {"code": "a"}),
    (_Bounds, {"code": "abcd"}),
    (_Bounds, {"pixels": 1}),
    (_Bounds, {"pixels": 0}),
    (_Bounds, {"pixels": "10px"}),
    (_PatternArm, {"gap": 4}),
    (_PatternArm, {"gap": "4px"}),
    (_PatternArm, {"gap": "4em"}),
    (_Sized, {"ids": ["a"]}),
    (_Sized, {"ids": []}),
    (_Sized, {"frozen_ids": ["a"]}),
    (_Sized, {"frozen_ids": []}),
    (_ItemBounds, {"items": [1]}),
    (_ItemBounds, {"items_or_int": 0}),
    (_ItemBounds, {"pair": [1, 1]}),
    (_ItemBounds, {"bare_items": [1]}),
    (_ItemBounds, {"bare_pair": [1, 1]}),
    (_ArmLength, {"one": "ab"}),
    (_ArmLength, {"one": ["a"]}),
    (_ArmLength, {"one": ["a", "b"]}),
    (_ArmLength, {"one": []}),
    (_ArmLength, {"name": "a"}),
    (_ArmLength, {"name": "ab"}),
    (_ArmLength, {"name": ["a"]}),
    (_WidenedArm, {"block": ["a"]}),
    (_WidenedArm, {"block": []}),
    (_WidenedArm, {"block": {"entries": ["a"]}}),
    (_Open, {"a": 1, "b": "anything"}),
    (_Ignoring, {"a": 1, "b": "anything"}),
    (_IntEnum, {"clock": 12}),
    (_IntEnum, {"clock": None}),
    (_IntEnum, {"clock": 13}),
    (_IntEnum, {"clock": "x"}),
]


@pytest.mark.parametrize(
    ("model", "board"), _CASES, ids=[f"{m.__name__}-{b}" for m, b in _CASES]
)
def test_schema_verdict_matches_pydantic(
    model: type[BaseModel], board: dict[str, Any]
) -> None:
    assert _schema_accepts(model, board) == _pydantic_accepts(model, board)


def test_bound_on_one_of_two_numeric_arms_raises() -> None:
    class _Ambiguous(BaseModel):
        model_config = ConfigDict(extra="forbid")
        size: Annotated[int, Field(gt=0)] | float | None = None

    with pytest.raises(TypeError, match="size"):
        _collect_models(_Ambiguous, {})


def test_length_bound_on_one_of_two_same_kind_arms_raises() -> None:
    class _Ambiguous(BaseModel):
        model_config = ConfigDict(extra="forbid")
        ids: Annotated[list[str], Field(min_length=1)] | list[int] | None = None

    with pytest.raises(TypeError, match="ids"):
        _collect_models(_Ambiguous, {})


def test_length_bound_in_a_sole_arm_widening_is_kept() -> None:
    widened = Annotated[
        _Entries,
        BeforeValidator(
            _wrap_bare_list,
            json_schema_input_type=Annotated[list[str], Field(min_length=1)],
        ),
    ]
    assert _arm_length_constraints("block", widened) == {"list": {"min_length": 1}}


def _has_bare_container(schema: Any) -> bool:  # noqa: ANN401 — any JSON value
    if isinstance(schema, list):
        return any(_has_bare_container(s) for s in schema)
    if not isinstance(schema, dict):
        return False
    if schema.get("type") == "array" and not {"items", "prefixItems"} & set(schema):
        return True
    if schema.get("type") == "object" and not {
        "properties",
        "additionalProperties",
    } & set(schema):
        return True
    return any(_has_bare_container(v) for v in schema.values())


def test_board_schema_types_every_typed_container() -> None:
    ir = introspect()
    schema = render_yaml_schema(ir)
    rendered = {**schema["$defs"], ir.root: schema}
    bare = [
        f"{model.name}.{field.name}: {field.type_repr}"
        for model in ir.models.values()
        if model.name in rendered
        for field in model.fields
        if "Any" not in field.type_repr
        and _has_bare_container(rendered[model.name]["properties"].get(field.name))
    ]
    assert not bare


def test_item_bounds_stay_off_the_field() -> None:
    collected: dict[str, AuthorableModel] = {}
    _collect_models(_ItemBounds, collected)
    assert all(
        not f.bounds and not f.constraints for f in collected["_ItemBounds"].fields
    )


def _field(type_repr: str, constraints: dict[str, int | str]) -> SchemaField:
    return SchemaField(
        name="x",
        description="",
        type_repr=type_repr,
        required=False,
        default=None,
        default_repr=None,
        enum_values=None,
        nested_models=[],
        constraints=constraints,
    )


def test_length_bound_without_a_sized_arm_raises() -> None:
    with pytest.raises(ValueError, match="no sized arm"):
        _constraint_keywords(_field("int | None", {"min_length": 1}))


def test_constraint_colliding_with_the_type_schema_raises() -> None:
    class _FixedPair(BaseModel):
        model_config = ConfigDict(extra="forbid")
        pair: tuple[int, int] = Field(min_length=1)

    collected: dict[str, AuthorableModel] = {}
    _collect_models(_FixedPair, collected)
    with pytest.raises(ValueError, match="minItems"):
        render_yaml_schema(AuthorableSchema(root="_FixedPair", models=collected))


def test_projection_accepts_a_name_or_an_object() -> None:
    schema = render_yaml_schema(introspect())
    for family in ("GeoshapeChart", "PointMapChart"):
        projection = schema["$defs"][family]["properties"]["projection"]
        validator = validator_for(schema)({**projection, "$defs": schema["$defs"]})
        assert validator.is_valid("mercator")
        assert validator.is_valid({"type": "mercator", "center": [0, 0]})
        assert validator.is_valid(None)
        assert not validator.is_valid(1)
