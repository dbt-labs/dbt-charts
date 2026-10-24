"""Tests for hand-owned Vega-Lite contracts and emission boundary validation."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.models.chart.authored import AuthoredChart

_chart_patch_adapter = TypeAdapter(AuthoredChart)
from dbt_charts.core.compile.models.vega_lite.contracts import (
    Axis,
    Mark,
    Scale,
    TopLevelCompositeSpec,
    TopLevelParameter,
    TopLevelUnitSpec,
    Transform,
    VLConfig,
)
from dbt_charts.core.compile.vega_lite.validation import (
    TOP_LEVEL_SPEC_PASSTHROUGH_FIELDS,
)


class TestContractStructure:
    """Hand-owned contracts have the right structural properties."""

    def test_all_contracts_allow_extra(self) -> None:
        for model in (
            VLConfig,
            Scale,
            Axis,
            Transform,
            TopLevelParameter,
            TopLevelUnitSpec,
            TopLevelCompositeSpec,
        ):
            assert model.model_config.get("extra") == "allow", (
                f"{model.__name__} missing extra='allow'"
            )

    def test_mark_type_literal_covers_expected_types(self) -> None:
        from typing import get_args

        mark_types = set(get_args(Mark))
        for expected in (
            "arc",
            "area",
            "bar",
            "line",
            "point",
            "rect",
            "rule",
            "text",
            "tick",
            "circle",
            "square",
            "geoshape",
            "trail",
        ):
            assert expected in mark_types, f"Mark literal missing: {expected}"

    def test_top_level_unit_spec_has_passthrough_fields(self) -> None:
        field_names = set(TopLevelUnitSpec.model_fields.keys())
        for expected in (
            "align",
            "autosize",
            "background",
            "description",
            "height",
            "name",
            "title",
            "usermeta",
            "view",
            "width",
        ):
            assert expected in field_names, (
                f"TopLevelUnitSpec missing field: {expected}"
            )

    def test_top_level_unit_spec_has_system_owned_fields(self) -> None:
        field_names = set(TopLevelUnitSpec.model_fields.keys())
        for expected in (
            "schema_",
            "data",
            "mark",
            "encoding",
            "config",
            "projection",
            "transform",
            "params",
        ):
            assert expected in field_names, (
                f"TopLevelUnitSpec missing system field: {expected}"
            )

    def test_passthrough_fields_exclude_system_owned(self) -> None:
        system_owned = {
            "$schema",
            "data",
            "mark",
            "encoding",
            "config",
            "projection",
            "transform",
            "params",
        }
        assert not (TOP_LEVEL_SPEC_PASSTHROUGH_FIELDS & system_owned)

    def test_passthrough_plus_system_covers_all_spec_fields(self) -> None:
        from dbt_charts.core.compile.vega_lite.validation import vl_field_name

        all_fields = {vl_field_name(f) for f in TopLevelUnitSpec.model_fields}
        system_owned = {
            "mark",
            "encoding",
            "config",
            "projection",
            "transform",
            "params",
        }
        supported = system_owned | set(TOP_LEVEL_SPEC_PASSTHROUGH_FIELDS)
        composite_fields = {
            vl_field_name(f) for f in TopLevelCompositeSpec.model_fields
        }
        assert supported == (all_fields | composite_fields) - {"$schema", "data"}

    def test_top_level_parameter_requires_name(self) -> None:
        assert "name" in TopLevelParameter.model_fields


class TestContractUsability:
    """Contracts are usable for validation and serialization."""

    def test_axis_accepts_properties(self) -> None:
        a = Axis(title="My Axis", labelAngle=-45, grid=False)
        d = a.model_dump(exclude_none=True)
        assert d["title"] == "My Axis"

    def test_scale_accepts_properties(self) -> None:
        s = Scale(type="log", domain=[1, 1000])
        d = s.model_dump(exclude_none=True)
        assert d["type"] == "log"

    def test_json_schema_derivable(self) -> None:
        for model in (VLConfig, Axis, Scale):
            schema = model.model_json_schema()
            assert "properties" in schema or "additionalProperties" in schema


class TestRejectedChartFields:
    """VL passthrough and escape-hatch fields are rejected on AuthoredChart."""

    @pytest.mark.parametrize(
        "field_name",
        [
            "spec",
            "config",
            "transform",
            "params",
            "resolve",
            "hconcat",
            "vconcat",
            "concat",
            "repeat",
        ],
    )
    def test_chart_rejects_passthrough_field(self, field_name: str) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _chart_patch_adapter.validate_python(
                {
                    "type": "line",
                    "x": "date",
                    "y": "value",
                    field_name: {"foo": "bar"},
                }
            )
