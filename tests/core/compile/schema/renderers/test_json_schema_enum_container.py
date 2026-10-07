"""TDD tests for _type_schema's enum branch combined with a container shape.

_extract_enum_values recurses through dict/list containers and through
sibling union arms, so a Literal buried inside a dict value type or sitting
alongside a list[...] arm is found — but _type_schema's enum branch used to
return immediately once it found enum values, never checking field.container
or the field's other union arms. That silently dropped:
  - the object/additionalProperties wrapping for `dict[str, Literal | str]`
  - the list[...] arm of a top-level `Literal | str | list[str]` union

These are isolated unit tests against small throwaway Pydantic models —
independent of any specific production field — that pin the fix.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.schema.introspection import _build_schema_field
from dbt_charts.core.compile.schema.renderers.json_schema import _type_schema

_Choice = Literal["a", "b"]


class _DictEnumValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roles: dict[str, _Choice | str] | None = Field(default=None, description="x")


class _SiblingListArm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extends: _Choice | str | list[str] | None = Field(default=None, description="x")


class _TwoSiblingListArms(BaseModel):
    """Two list[...] arms with different item types."""

    model_config = ConfigDict(extra="forbid")
    palette: _Choice | list[str] | list[float] | str | None = Field(
        default=None, description="x"
    )


class _SiblingDictArm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thing: _Choice | dict[str, str] | None = Field(default=None, description="x")


def _schema_for(model: type[BaseModel], field_name: str) -> dict[str, Any]:
    fi = model.model_fields[field_name]
    sf = _build_schema_field(field_name, fi, fi.annotation)
    return _type_schema(sf, root=model.__name__)


class TestDictValuedEnumField:
    def test_wraps_as_object_with_additional_properties(self) -> None:
        schema = _schema_for(_DictEnumValue, "roles")
        # nullable: dict[...] | None → anyOf[object, null]
        obj = next(b for b in schema["anyOf"] if b.get("type") == "object")
        assert "additionalProperties" in obj

    def test_value_schema_keeps_both_enum_and_string_arms(self) -> None:
        schema = _schema_for(_DictEnumValue, "roles")
        obj = next(b for b in schema["anyOf"] if b.get("type") == "object")
        value_schema = obj["additionalProperties"]
        branches = value_schema.get("anyOf", [value_schema])
        assert any(b.get("enum") == ["a", "b"] for b in branches)
        assert any(b.get("type") == "string" for b in branches)


class TestSiblingListArmNotDropped:
    def test_list_arm_present_alongside_enum(self) -> None:
        schema = _schema_for(_SiblingListArm, "extends")
        branches = schema["anyOf"]
        assert any(b.get("enum") == ["a", "b"] for b in branches)
        assert any(b.get("type") == "string" for b in branches)
        assert any(b.get("type") == "array" for b in branches), (
            f"list[str] arm dropped from enum union: {branches}"
        )
        assert any(b.get("type") == "null" for b in branches)


class TestSiblingDictArmNotDropped:
    def test_dict_arm_present_alongside_enum(self) -> None:
        schema = _schema_for(_SiblingDictArm, "thing")
        branches = schema["anyOf"]
        assert any(b.get("enum") == ["a", "b"] for b in branches)
        assert any(b.get("type") == "object" for b in branches), (
            f"dict[...] arm dropped from enum union: {branches}"
        )


class TestTwoSiblingContainerArmsKeepTheirItemTypes:
    def test_each_list_arm_keeps_its_item_type(self) -> None:
        schema = _schema_for(_TwoSiblingListArms, "palette")
        branches = schema["anyOf"]
        array_arms = [b for b in branches if b.get("type") == "array"]
        assert array_arms == [
            {"type": "array", "items": {"type": "string"}},
            {"type": "array", "items": {"type": "number"}},
        ]
        assert any(b.get("enum") == ["a", "b"] for b in branches)
        assert any(b.get("type") == "string" for b in branches)
