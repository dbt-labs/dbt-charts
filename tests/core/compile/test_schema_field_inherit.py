"""Tests for SchemaField inherit metadata and style models in the introspection IR."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict

from dbt_charts.core.compile.models.markers import Inherit, InheritSlot
from dbt_charts.core.compile.schema.introspection import (
    SchemaField,
    _build_schema_field,
)

# ── SchemaField inherit metadata ──────────────────────────────────────────────


def test_schema_field_has_inherit_from_attribute():

    import dataclasses

    names = {f.name for f in dataclasses.fields(SchemaField)}
    assert "inherit_from" in names, "SchemaField is missing inherit_from field"
    assert "inherit_slot" in names, "SchemaField is missing inherit_slot field"


def test_build_schema_field_reads_inherit_marker():
    """_build_schema_field populates inherit_from from an Inherit marker."""

    class _Inner(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        color: str

    class _Root(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        inner: _Inner
        color: Annotated[str, Inherit(from_path="inner.color")]

    fi = _Root.model_fields["color"]
    sf = _build_schema_field("color", fi, fi.annotation, rejects_null=False)

    assert sf.inherit_from == ("inner.color",)
    assert sf.inherit_slot is None


def test_build_schema_field_reads_inherit_slot_marker():
    """_build_schema_field populates inherit_slot from an InheritSlot marker."""

    class _AxisFixture(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        visible: bool

    class _ChartsFixture(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        axis: _AxisFixture
        axis_x: Annotated[_AxisFixture, InheritSlot(from_path="charts.axis")]

    fi = _ChartsFixture.model_fields["axis_x"]
    sf = _build_schema_field("axis_x", fi, fi.annotation, rejects_null=False)

    assert sf.inherit_slot == "charts.axis"
    assert sf.inherit_from == ()


def test_build_schema_field_no_markers_leaves_inherit_empty():
    """Fields without Inherit/InheritSlot markers have empty/None inherit fields."""

    class _Plain(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        name: str

    fi = _Plain.model_fields["name"]
    sf = _build_schema_field("name", fi, fi.annotation, rejects_null=False)

    assert sf.inherit_from == ()
    assert sf.inherit_slot is None


# ── Style model collection in introspection IR ────────────────────────────────


def test_compiled_style_models_in_ir():
    """Compiled style models from theme.py must appear in the introspection IR."""
    from dbt_charts.core.compile.schema.introspection import introspect

    ir = introspect()
    # Style is the root compiled model — it must be present.
    assert "Style" in ir.models, (
        "Style not found in IR — _is_authored_model should accept style.theme models"
    )


def test_style_chart_style_in_ir():
    """ChartsStyle (a nested compiled style model) must appear in the IR."""
    from dbt_charts.core.compile.schema.introspection import introspect

    ir = introspect()
    assert "ChartsStyle" in ir.models
