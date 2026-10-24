"""An annotation style takes only the fields and values the engine draws."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.models.chart.authored._annotation import Annotation
from dbt_charts.core.compile.models.factories import AUTHORED_INPUT
from dbt_charts.core.compile.schema.introspection import introspect
from dbt_charts.core.compile.schema.renderers.json_schema import render_yaml_schema

_ANCHORS: dict[str, dict[str, object]] = {
    "rule": {"y": 1},
    "band": {"y": {"from": 1, "to": 2}},
    "note": {"x": 1, "y": 1},
    "trend": {"fit": "linear()"},
}


def _validate(kind: str, marks: dict[str, object]) -> None:
    TypeAdapter(Annotation).validate_python(
        {"type": kind, **_ANCHORS[kind], "style": {"marks": marks}},
        context=AUTHORED_INPUT,
    )


@pytest.mark.parametrize("kind", ["note", "trend"])
@pytest.mark.parametrize("curve", ["linear", "natural"])
def test_an_annotation_line_takes_the_curves_it_draws(kind: str, curve: str) -> None:
    _validate(kind, {"line": {"curve": curve}})


@pytest.mark.parametrize("kind", ["note", "trend"])
@pytest.mark.parametrize(
    "curve", ["monotone", "basis", "cardinal", "step", "step-before", "step-after"]
)
def test_an_annotation_line_rejects_the_curves_it_cannot_draw(
    kind: str, curve: str
) -> None:
    with pytest.raises(ValidationError, match="linear.*natural"):
        _validate(kind, {"line": {"curve": curve}})


def test_the_annotation_curve_enum_is_linear_and_natural() -> None:
    defs = render_yaml_schema(introspect())["$defs"]
    lines = {
        name: d
        for name, d in defs.items()
        if name.endswith("LineMarkStylePatch") and name != "LineMarkStylePatch"
    }
    assert {"NoteLineMarkStylePatch", "AnnotationLineMarkStylePatch"} <= set(lines)
    for name, line in lines.items():
        curve = line["properties"]["curve"]
        options = curve.get("anyOf", [curve])
        assert [o["enum"] for o in options if "enum" in o] == [["linear", "natural"]], (
            name
        )


_UNREAD_FIELDS = [
    ("trend", {"line": {"halo_multiplier": 2}}),
    ("trend", {"line": {"connect": False}}),
    ("trend", {"line": {"disconnected_cap": "round"}}),
    ("trend", {"line": {"disconnected_width": 0.5}}),
    ("trend", {"line": {"labels": {"visible": True}}}),
    ("trend", {"area": {"curve": "step"}}),
    ("trend", {"area": {"backdrop": True}}),
    ("trend", {"area": {"stacked": {"opacity": 0.5}}}),
    ("trend", {"line": {"stroke": {"cap": "round"}}}),
    ("note", {"line": {"stroke": {"join": "bevel"}}}),
    ("rule", {"rule": {"stroke": {"cap": "round"}}}),
    ("band", {"rect": {"stroke": {"join": "round"}}}),
    ("note", {"circle": {"stroke": {"cap": "square"}}}),
    ("rule", {"text": {"align": "left"}}),
    ("rule", {"text": {"font": {"decoration": "underline"}}}),
    ("rule", {"text": {"font": {"case": "upper"}}}),
    ("rule", {"text": {"box": {"stroke": {"cap": "round"}}}}),
    ("rule", {"text": {"box": {"stroke": {"join": "round"}}}}),
    ("rule", {"text": {"box": {"stroke": {"dasharray": "2 2"}}}}),
]


@pytest.mark.parametrize(("kind", "marks"), _UNREAD_FIELDS)
def test_an_annotation_rejects_a_field_the_engine_never_reads(
    kind: str, marks: dict[str, object]
) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        _validate(kind, marks)


_AUTO_VALUES = [
    ("rule", {"rule": {"opacity": "auto"}}),
    ("rule", {"rule": {"stroke": {"width": "auto"}}}),
    ("band", {"rect": {"opacity": "auto"}}),
    ("note", {"circle": {"opacity": "auto"}}),
    ("note", {"circle": {"stroke": None}}),
    ("rule", {"text": {"box": {"stroke": {"width": "auto", "color": "red"}}}}),
    ("rule", {"text": {"position": {"edge": "auto"}}}),
    ("rule", {"text": {"position": {"align": "auto"}}}),
    ("rule", {"text": {"position": {"overlay": "auto"}}}),
]


@pytest.mark.parametrize(("kind", "marks"), _AUTO_VALUES)
def test_an_annotation_rejects_auto_where_the_engine_needs_a_value(
    kind: str, marks: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        _validate(kind, marks)


@pytest.mark.parametrize("dasharray", ["banana", "4 x", "-1 2"])
def test_a_dasharray_is_auto_or_numbers(dasharray: str) -> None:
    with pytest.raises(ValidationError, match="dasharray"):
        _validate("rule", {"rule": {"stroke": {"dasharray": dasharray}}})


@pytest.mark.parametrize("dasharray", ["auto", "4 2", "4,2"])
def test_a_dasharray_of_auto_or_numbers_is_accepted(dasharray: str) -> None:
    _validate("rule", {"rule": {"stroke": {"dasharray": dasharray}}})


def test_a_font_weight_is_a_number_or_a_css_keyword() -> None:
    for weight in ("bold", "normal", "lighter", 600, "600"):
        _validate("rule", {"text": {"font": {"weight": weight}}})
    with pytest.raises(ValidationError, match="weight"):
        _validate("rule", {"text": {"font": {"weight": "heavy"}}})
