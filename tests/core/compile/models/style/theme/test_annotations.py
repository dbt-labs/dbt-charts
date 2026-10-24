"""Annotation-only mark fields stay on the annotation slot."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.authored._annotation import Annotation
from dbt_charts.core.compile.models.style.authored import StylePatch


@pytest.mark.parametrize(
    ("mark", "field", "value"),
    [("rect", "fill", "red"), ("rect", "padding", 9), ("circle", "radius", 4)],
)
def test_annotation_only_mark_fields_are_rejected_on_the_global_marks(
    mark: str, field: str, value: object
) -> None:
    with pytest.raises(ValidationError, match=field):
        StylePatch.model_validate({"charts": {"marks": {mark: {field: value}}}})
    StylePatch.model_validate(
        {"annotations": {"note": {"marks": {mark: {field: value}}}}}
    )


_MARKS_PER_TYPE = {
    "rule": {"rule", "text"},
    "band": {"rect", "text"},
    "note": {"text", "line", "circle", "rect"},
    "trend": {"line", "area", "text"},
}
_ALL_MARKS = {"rule", "rect", "line", "area", "circle", "text"}
_ANCHORS = {
    "rule": {"y": 1},
    "band": {"y": {"from": 1, "to": 2}},
    "note": {"x": 1, "y": 1},
    "trend": {"fit": "linear()"},
}


def _annotation(kind: str, marks: dict[str, object]) -> dict[str, object]:
    return {"type": kind, **_ANCHORS[kind], "style": {"marks": marks}}


@pytest.mark.parametrize(
    ("kind", "mark"),
    [(k, m) for k, marks in _MARKS_PER_TYPE.items() for m in sorted(marks)],
)
def test_an_annotation_accepts_the_marks_its_type_draws(kind: str, mark: str) -> None:
    payload = {"dx": 1} if mark == "text" else {"opacity": 0.5}
    TypeAdapter(Annotation).validate_python(_annotation(kind, {mark: payload}))


@pytest.mark.parametrize(
    ("kind", "mark"),
    [
        (k, m)
        for k, marks in _MARKS_PER_TYPE.items()
        for m in sorted(_ALL_MARKS - marks)
    ],
)
def test_an_annotation_rejects_the_marks_its_type_never_draws(
    kind: str, mark: str
) -> None:
    with pytest.raises(ValidationError, match=mark):
        TypeAdapter(Annotation).validate_python(
            _annotation(kind, {mark: {"opacity": 0.5}})
        )


def test_only_a_note_rect_takes_padding() -> None:
    adapter = TypeAdapter(Annotation)
    adapter.validate_python(_annotation("note", {"rect": {"padding": 4}}))
    with pytest.raises(ValidationError, match="padding"):
        adapter.validate_python(_annotation("band", {"rect": {"padding": 4}}))


def test_a_note_style_takes_marks_only() -> None:
    with pytest.raises(ValidationError, match="frame"):
        TypeAdapter(Annotation).validate_python(
            {
                "type": "note",
                "text": "x",
                "style": {"frame": {"text": {"position": {"edge": "top"}}}},
            }
        )


@pytest.mark.parametrize("kind", sorted(_MARKS_PER_TYPE))
def test_a_theme_slot_exposes_only_its_types_marks(kind: str) -> None:
    slot = getattr(get_theme_style().annotations, kind)
    assert set(type(slot.marks).model_fields) == _MARKS_PER_TYPE[kind]
    with pytest.raises(ValidationError, match="Extra inputs"):
        StylePatch.model_validate(
            {
                "annotations": {
                    kind: {
                        "marks": {
                            sorted(_ALL_MARKS - _MARKS_PER_TYPE[kind])[0]: {
                                "opacity": 0.5
                            }
                        }
                    }
                }
            }
        )


_THEMES = ["stark", "clarity", "vivid", "paper", "neon"]


@pytest.mark.parametrize("theme", _THEMES)
def test_annotation_defaults_follow_the_design_brief(theme: str) -> None:
    ann = get_theme_style(theme).annotations
    rule, band, note, trend = (
        ann.rule.marks,
        ann.band.marks,
        ann.note.marks,
        ann.trend.marks,
    )
    assert rule.rule.stroke.width == (2 if theme == "stark" else 1.5)
    assert rule.rule.stroke.dasharray is None
    assert band.rect.opacity == 0.10
    assert note.text.font.style == "italic"
    for mark in (rule.text, band.text, trend.text):
        assert mark.font.style != "italic"
    for mark in (rule.text, band.text, note.text, trend.text):
        assert mark.font.size == 11
        assert mark.box.visible is False
    for mark in (note.line, note.circle, note.rect):
        assert mark.stroke.color == rule.rule.stroke.color
    frame = ann.note.frame.text.position
    assert (frame.edge, frame.align, frame.overlay) == ("bottom", "end", True)
    assert trend.line.stroke.width == (2 if theme == "neon" else 1.5)
    assert trend.area.opacity == (0.20 if theme == "neon" else 0.15)
    position = trend.text.position
    assert (position.edge, position.align, position.overlay) == (
        "right",
        "center",
        False,
    )
