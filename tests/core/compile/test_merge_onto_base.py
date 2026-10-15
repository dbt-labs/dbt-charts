"""TDD tests for merge_onto_base — the base-terminal merge engine.

Covers the contract for the single patch-onto-base merge:

  - ``model_fields_set`` is the sole presence contract.
  - Omitted fields inherit; explicit ``None`` clears or fails target validation.
  - Nested BaseModel fields merged recursively.
  - dict fields merged key-wise (patch wins, base keys survive).
  - Scalars replaced wholesale when the patch field is set.
  - Base-None model children seed from only the patch's set fields.
  - Terminal: ``type(base).model_validate(result)`` — result is validated and
    base-typed with accurate field-set provenance.
"""

from __future__ import annotations

from typing import Any, get_args, get_origin

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from dbt_charts.core.compile.merge import _combine_field, merge_onto_base

# ---------------------------------------------------------------------------
# Minimal test models (no dependency on style/board models)
# ---------------------------------------------------------------------------


class _Inner(BaseModel):
    """Compiled inner model — all fields have defaults."""

    model_config = ConfigDict(extra="forbid")
    x: str = "default_x"
    y: int = 0


class _InnerPatch(BaseModel):
    """Patch counterpart — all Optional."""

    model_config = ConfigDict(extra="forbid")
    x: str | None = None
    y: int | None = None


class _WithRequired(BaseModel):
    """Compiled type that has a REQUIRED field (no default for `label`)."""

    model_config = ConfigDict(extra="forbid")
    label: str  # required
    value: int = 0


class _WithRequiredPatch(BaseModel):
    """Patch counterpart for _WithRequired."""

    model_config = ConfigDict(extra="forbid")
    label: str | None = None
    value: int | None = None


class _AllOptional(BaseModel):
    """Compiled type where ALL fields have defaults — seedable from a patch."""

    model_config = ConfigDict(extra="forbid")
    color: str = "black"
    size: int = 12


class _AllOptionalPatch(BaseModel):
    """Patch counterpart for _AllOptional."""

    model_config = ConfigDict(extra="forbid")
    color: str | None = None
    size: int | None = None


class _Base(BaseModel):
    """Main base model for most tests."""

    model_config = ConfigDict(extra="forbid")
    name: str = "base"
    count: int = 0
    note: str | None = "base-note"
    data: dict[str, str] = {}
    inner: _Inner = _Inner()
    # Optional nested — for seeding tests
    maybe_seedable: _AllOptional | None = None
    maybe_required: _WithRequired | None = None


class _BasePatch(BaseModel):
    """Patch counterpart for _Base."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    count: int | None = None
    note: str | None = None
    data: dict[str, str] | None = None
    inner: _InnerPatch | None = None
    maybe_seedable: _AllOptionalPatch | None = None
    maybe_required: _WithRequiredPatch | None = None


class _ConcreteDefaultPatch(BaseModel):
    """Synthetic patch proving an omitted concrete default is not authored."""

    model_config = ConfigDict(extra="forbid")
    count: int = 99


class _PatchWithExtra(BaseModel):
    """Patch shape with a field absent from _Base."""

    model_config = ConfigDict(extra="forbid")
    extra: str | None = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPresenceSemantics:
    """Field-set membership distinguishes omission from an explicit value."""

    def test_none_patch_returns_base(self) -> None:
        base = _Base(name="alice", count=7)
        result = merge_onto_base(base, None)
        assert result is base

    def test_omitted_fields_inherit_base_scalars(self) -> None:
        base = _Base(name="alice", count=7)
        patch = _BasePatch()
        result = merge_onto_base(base, patch)
        assert result.name == "alice"
        assert result.count == 7

    def test_omitted_concrete_patch_default_inherits(self) -> None:
        base = _Base(count=7)
        result = merge_onto_base(base, _ConcreteDefaultPatch())
        assert result.count == 7

    def test_explicit_none_clears_nullable_field(self) -> None:
        base = _Base(note="present")
        result = merge_onto_base(base, _BasePatch(note=None))
        assert result.note is None
        assert "note" in result.model_fields_set

    def test_explicit_none_fails_non_nullable_field(self) -> None:
        base = _Base(name="alice")
        with pytest.raises(ValidationError, match="Input should be a valid string"):
            merge_onto_base(base, _BasePatch(name=None))

    def test_omitted_synthesized_value_is_inert(self) -> None:
        base = _Base(inner=_Inner(x="hello", y=42))
        patch = _BasePatch.model_construct(
            _fields_set=set(),
            inner=_InnerPatch(x="not-authored"),
        )
        result = merge_onto_base(base, patch)
        assert result is base


class TestScalarOverride:
    """Non-None scalar in patch replaces base value."""

    def test_scalar_override(self) -> None:
        base = _Base(name="base_name", count=5)
        patch = _BasePatch(name="patched", count=99)
        result = merge_onto_base(base, patch)
        assert result.name == "patched"
        assert result.count == 99

    def test_partial_override_leaves_other_fields(self) -> None:
        base = _Base(name="alice", count=5)
        patch = _BasePatch(name="bob")
        result = merge_onto_base(base, patch)
        assert result.name == "bob"
        assert result.count == 5


class TestDictMerge:
    """Dict fields merged key-wise: patch keys win, base keys survive."""

    def test_patch_key_wins(self) -> None:
        base = _Base(data={"a": "1", "b": "2"})
        patch = _BasePatch(data={"b": "patched", "c": "new"})
        result = merge_onto_base(base, patch)
        assert result.data == {"a": "1", "b": "patched", "c": "new"}

    def test_base_keys_survive(self) -> None:
        base = _Base(data={"x": "base_x", "y": "base_y"})
        patch = _BasePatch(data={"x": "patch_x"})
        result = merge_onto_base(base, patch)
        assert result.data["y"] == "base_y"

    def test_explicit_none_dict_patch_fails_target_validation(self) -> None:
        base = _Base(data={"k": "v"})
        patch = _BasePatch(data=None)
        with pytest.raises(ValidationError, match="Input should be a valid dictionary"):
            merge_onto_base(base, patch)


class TestNestedModel:
    """Nested BaseModel fields merged recursively field-by-field."""

    def test_nested_partial_override(self) -> None:
        base = _Base(inner=_Inner(x="orig_x", y=10))
        patch = _BasePatch(inner=_InnerPatch(x="new_x"))
        result = merge_onto_base(base, patch)
        assert result.inner.x == "new_x"  # patch wins
        assert result.inner.y == 10

    def test_omitted_nested_patch_inherits(self) -> None:
        base = _Base(inner=_Inner(x="orig_x", y=10))
        patch = _BasePatch()
        result = merge_onto_base(base, patch)
        assert result.inner.x == "orig_x"
        assert result.inner.y == 10

    def test_explicit_empty_nested_patch_preserves_child(self) -> None:
        inner = _Inner(x="orig_x", y=10)
        base = _Base(inner=inner)
        result = merge_onto_base(base, _BasePatch(inner=_InnerPatch()))
        assert result.inner == inner
        assert result.inner.model_fields_set == inner.model_fields_set

    def test_deep_recursion(self) -> None:
        """Three levels: _Base → _Inner. Both x and y of inner partially patched."""
        base = _Base(inner=_Inner(x="A", y=100))
        patch = _BasePatch(inner=_InnerPatch(y=200))  # x=None → inherit
        result = merge_onto_base(base, patch)
        assert result.inner.x == "A"
        assert result.inner.y == 200


class TestRequiredFieldSeeding:
    """When base_val is None and patch_val is a BaseModel.

    The patch contributes only fields in ``model_fields_set``. Complete shapes
    validate; incomplete shapes fail instead of disappearing.
    """

    def test_seedable_type_seeded_from_patch(self) -> None:
        """Target compiled type has no required fields → seeded via model_dump."""
        base = _Base(maybe_seedable=None)
        patch = _BasePatch(maybe_seedable=_AllOptionalPatch(color="red"))
        result = merge_onto_base(base, patch)
        # Seeded: the patch provided color; size inherits from _AllOptional default.
        assert result.maybe_seedable is not None
        assert result.maybe_seedable.color == "red"

    def test_complete_required_type_is_seeded(self) -> None:
        base = _Base(maybe_required=None)
        patch = _BasePatch(maybe_required=_WithRequiredPatch(label="hi"))
        result = merge_onto_base(base, patch)
        assert result.maybe_required is not None
        assert result.maybe_required.label == "hi"
        assert result.maybe_required.value == 0

    def test_incomplete_required_type_fails(self) -> None:
        base = _Base(maybe_required=None)
        patch = _BasePatch(maybe_required=_WithRequiredPatch(value=3))
        with pytest.raises(ValidationError, match="Field required"):
            merge_onto_base(base, patch)


class TestResultType:
    """Result is an instance of type(base), validated (not model_construct)."""

    def test_returns_base_type(self) -> None:
        base = _Base(name="bob")
        patch = _BasePatch(name="alice")
        result = merge_onto_base(base, patch)
        assert type(result) is _Base

    def test_validated_result(self) -> None:
        """model_validate was called — extra fields would raise; defaults apply."""
        base = _Inner(x="a", y=1)
        patch = _InnerPatch(x="b")
        result = merge_onto_base(base, patch)
        assert isinstance(result, _Inner)
        assert result.x == "b"
        assert result.y == 1  # inherited, not default-reset


class TestResultFieldSets:
    def test_multiple_layers_union_base_and_patch_presence(self) -> None:
        base = _Base(name="base")
        first = merge_onto_base(base, _BasePatch(note=None))
        second = merge_onto_base(first, _BasePatch(count=7))

        assert second.model_fields_set == {"name", "note", "count"}
        assert second.model_dump(exclude_unset=True) == {
            "name": "base",
            "note": None,
            "count": 7,
        }

    def test_nested_field_sets_survive_roundtrip(self) -> None:
        base = _Base(inner=_Inner(x="base"))
        merged = merge_onto_base(base, _BasePatch(inner=_InnerPatch(y=7)))
        roundtripped = _Base.model_validate(merged.model_dump(exclude_unset=True))

        assert merged.inner.model_fields_set == {"x", "y"}
        assert roundtripped.model_fields_set == merged.model_fields_set
        assert roundtripped.inner.model_fields_set == merged.inner.model_fields_set


class TestPatchOnlyFields:
    def test_unset_patch_only_field_is_inert(self) -> None:
        base = _Base(name="base")
        assert merge_onto_base(base, _PatchWithExtra()) is base

    def test_set_patch_only_field_fails(self) -> None:
        base = _Base(name="base")
        with pytest.raises(TypeError, match="sets fields absent from _Base: extra"):
            merge_onto_base(base, _PatchWithExtra(extra="authored"))


class TestCombineField:
    """_combine_field is the ONE shared dispatch ladder for both merge engines."""

    def test_both_model_recurses(self) -> None:
        calls: list[tuple] = []

        def spy(a: Any, b: Any) -> Any:
            calls.append((a, b))
            return a  # return value not important here

        lo = _Inner(x="lo", y=1)
        hi = _InnerPatch(x="hi")
        _combine_field(lo, hi, recurse=spy)
        assert calls == [(lo, hi)]

    def test_both_dict_merges(self) -> None:
        result = _combine_field({"a": 1}, {"b": 2}, recurse=lambda a, b: None)
        assert result == {"a": 1, "b": 2}

    def test_strat_override_skips_isinstance(self) -> None:
        """Explicit override: returns hi even when lo/hi are BaseModels."""
        lo = _Inner(x="lo", y=1)
        hi = _InnerPatch(x="hi")
        called: list[bool] = []
        result = _combine_field(
            lo,
            hi,
            recurse=lambda a, b: called.append(True) or a,
            strat="override",
        )
        assert result is hi
        assert not called, "recurse must not be called for override"

    def test_scalar_fallback_returns_hi(self) -> None:
        result = _combine_field("old", "new", recurse=lambda a, b: None)
        assert result == "new"

    def test_merge_patches_routes_through_combine_field(self) -> None:
        """Both wrappers use _combine_field: verify merge_patches result matches."""
        from pydantic import ConfigDict

        from dbt_charts.core.compile.merge import merge_patches

        class _P(BaseModel):
            model_config = ConfigDict(extra="forbid")
            x: str | None = None
            y: int | None = None

        lo = _P(x="lo_x", y=10)
        hi = _P(x="hi_x")  # y unset → inherit lo
        result = merge_patches(lo, hi)
        assert result.x == "hi_x"
        assert result.y == 10  # lo inherited (y not set in hi)

    def test_merge_onto_base_routes_through_combine_field(self) -> None:
        """merge_onto_base + _combine_field: same nested-BaseModel dispatch."""
        lo = _Inner(x="lo_x", y=5)
        hi = _InnerPatch(x="hi_x")
        result = merge_onto_base(lo, hi)
        assert result.x == "hi_x"
        assert result.y == 5


class TestStyleCascadeRegression:
    """Regression: merge_onto_base on real style models inherits unset fields."""

    def test_axis_patch_partial_merge(self) -> None:
        """merge_onto_base on AxisXStyle + AxisXStylePatch inherits unset fields."""
        from dbt_charts.core.compile.config import get_theme_style, reset_config
        from dbt_charts.core.compile.models.style.authored import (
            AxisXStylePatch,
            DimensionLabelStylePatch,
        )

        reset_config()
        compiled = get_theme_style()
        bar = compiled.charts.bar
        if bar.axis_x is None:
            pytest.skip("Theme has no bar.axis_x — not a valid regression target")
        reset_config()

        existing_axis_x = bar.axis_x
        patch = AxisXStylePatch(labels=DimensionLabelStylePatch(padding=99))
        merged = merge_onto_base(existing_axis_x, patch)

        # patch field wins
        assert merged.labels is not None
        assert merged.labels.padding == 99
        # unset fields inherit from base
        # title and grid are complex; just check result is same type as base
        assert type(merged) is type(existing_axis_x)


def test_no_dict_field_value_admits_none() -> None:
    """A `key: null` dict entry has no meaning in the merge, so none may type it."""
    from dbt_charts.core.compile.models.board.patch import BoardPatch
    from dbt_charts.core.compile.models.style.authored import StylePatch

    seen: set[type[BaseModel]] = set()

    def nullable_dicts(model: type[BaseModel], path: str) -> list[str]:
        if model in seen:
            return []
        seen.add(model)
        found: list[str] = []
        for name, field in model.model_fields.items():
            for arg in _walk_annotation(field.annotation):
                if isinstance(arg, type) and issubclass(arg, BaseModel):
                    found += nullable_dicts(arg, f"{path}.{name}")
                if get_origin(arg) is dict and type(None) in _walk_annotation(
                    get_args(arg)[1]
                ):
                    found.append(f"{path}.{name}")
        return found

    found = [
        *nullable_dicts(StylePatch, "StylePatch"),
        *nullable_dicts(BoardPatch, "BoardPatch"),
    ]
    # A custom chart's option value may legitimately be null.
    assert found == ["BoardPatch.charts.options"]


def _walk_annotation(annotation: Any) -> list[Any]:
    args = get_args(annotation)
    if get_origin(annotation) is None and not args:
        return [annotation]
    return [annotation, *(a for arg in args for a in _walk_annotation(arg))]
