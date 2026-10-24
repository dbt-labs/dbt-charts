"""The authored `annotations:` grammar: types, anchors and the closed function syntax."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.models.chart.authored._annotation import (
    AggregateAnchor,
    AnchorRange,
    Annotation,
    BandAnnotation,
    ClockAnchor,
    FitFunction,
    IntervalFunction,
    NoteAnnotation,
    RuleAnnotation,
    TrendAnnotation,
)

_ADAPTER: TypeAdapter[Annotation] = TypeAdapter(Annotation)


def _parse(data: dict[str, Any]) -> Annotation:
    return _ADAPTER.validate_python(data)


def test_type_selects_the_model() -> None:
    assert isinstance(_parse({"type": "rule", "y": 25_000_000}), RuleAnnotation)
    assert isinstance(_parse({"type": "band", "x": "East"}), BandAnnotation)
    assert isinstance(
        _parse({"type": "note", "text": "Dashed = forecast"}), NoteAnnotation
    )
    assert isinstance(_parse({"type": "trend", "fit": "linear()"}), TrendAnnotation)


def test_unknown_type_and_foreign_keys_are_refused() -> None:
    with pytest.raises(ValidationError):
        _parse({"type": "arrow", "y": 1})
    with pytest.raises(ValidationError, match="extra"):
        _parse({"type": "rule", "y": 1, "fit": "linear()"})
    with pytest.raises(ValidationError, match="extra"):
        _parse({"type": "band", "x": "East", "connector": "line"})


def test_literal_anchors_keep_their_yaml_type() -> None:
    rule = _parse({"type": "rule", "x": date(2024, 7, 1)})
    assert isinstance(rule, RuleAnnotation)
    assert rule.x == date(2024, 7, 1)
    rule = _parse({"type": "rule", "y": 25_000_000})
    assert isinstance(rule, RuleAnnotation)
    assert rule.y == 25_000_000
    band = _parse({"type": "band", "x": "East"})
    assert isinstance(band, BandAnnotation)
    assert band.x == "East"


def test_functions_parse_into_typed_anchors() -> None:
    rule = _parse({"type": "rule", "y": "avg()"})
    assert isinstance(rule, RuleAnnotation)
    assert rule.y == AggregateAnchor(fn="avg")
    band = _parse(
        {"type": "band", "y": {"from": "percentile(25)", "to": "percentile(75)"}}
    )
    assert isinstance(band, BandAnnotation)
    assert band.y == AnchorRange(
        **{
            "from": AggregateAnchor(fn="percentile", percent=25),
            "to": AggregateAnchor(fn="percentile", percent=75),
        }
    )
    rule = _parse({"type": "rule", "x": "now()"})
    assert isinstance(rule, RuleAnnotation)
    assert rule.x == ClockAnchor(fn="now")
    band = _parse({"type": "band", "x": "current(month)"})
    assert isinstance(band, BandAnnotation)
    assert band.x == ClockAnchor(fn="current", unit="month")


@pytest.mark.parametrize(
    "anchor",
    [
        "mean()",
        "avg(revenue)",
        "percentile(101)",
        "percentile()",
        "current(decade)",
        "now(1)",
    ],
)
def test_bad_functions_are_refused(anchor: str) -> None:
    with pytest.raises(ValidationError, match="function"):
        _parse({"type": "rule", "y": anchor})


@pytest.mark.parametrize(
    "annotation",
    [
        {"type": "band", "x": {"fn": "current"}},
        {"type": "rule", "x": {"fn": "now", "unit": "day"}},
        {"type": "rule", "y": {"fn": "percentile"}},
        {"type": "rule", "y": {"fn": "avg", "percent": 50}},
        {"type": "trend", "fit": {"kind": "polynomial"}},
        {"type": "trend", "fit": {"kind": "linear", "order": 2}},
    ],
)
def test_a_mapping_function_takes_exactly_its_arguments(
    annotation: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        _parse(annotation)


def test_trend_functions_parse() -> None:
    trend = _parse(
        {"type": "trend", "fit": "polynomial(2)", "interval": "confidence(95)"}
    )
    assert isinstance(trend, TrendAnnotation)
    assert trend.fit == FitFunction(kind="polynomial", order=2)
    assert trend.interval == IntervalFunction(kind="confidence", percent=95)
    for bad in ["spline()", "polynomial(0)", "polynomial(1.5)"]:
        with pytest.raises(ValidationError, match="fit"):
            _parse({"type": "trend", "fit": bad})
    with pytest.raises(ValidationError, match="interval"):
        _parse({"type": "trend", "fit": "linear()", "interval": "confidence(100)"})


def test_a_rule_or_band_needs_an_anchor() -> None:
    with pytest.raises(ValidationError, match="x or y"):
        _parse({"type": "rule", "text": "x"})
    with pytest.raises(ValidationError, match="x or y"):
        _parse({"type": "band", "text": "x"})


def test_a_note_has_both_anchors_or_neither() -> None:
    with pytest.raises(ValidationError, match="both x and y"):
        _parse({"type": "note", "x": 1, "text": "x"})
    with pytest.raises(ValidationError, match="both x and y"):
        _parse({"type": "note", "y": 1, "text": "x"})


def test_a_frame_note_takes_no_connector_or_enclosure() -> None:
    with pytest.raises(ValidationError, match="anchor"):
        _parse({"type": "note", "text": "x", "connector": "arrow"})
    with pytest.raises(ValidationError, match="anchor"):
        _parse({"type": "note", "text": "x", "enclose": "circle"})
    with pytest.raises(ValidationError, match="anchor"):
        _parse({"type": "note", "text": "x", "connector": "line"})
    note = _parse({"type": "note", "text": "x"})
    assert isinstance(note, NoteAnnotation) and note.connector is None


@pytest.mark.parametrize("connector", ["line", "arrow"])
def test_a_note_connector_names_its_type(connector: str) -> None:
    note = _parse({"type": "note", "x": 1, "y": 2, "text": "x", "connector": connector})
    assert isinstance(note, NoteAnnotation) and note.connector == connector


@pytest.mark.parametrize("connector", [True, False, "none"])
def test_a_note_connector_is_not_a_flag(connector: object) -> None:
    with pytest.raises(ValidationError):
        _parse({"type": "note", "x": 1, "y": 2, "text": "x", "connector": connector})


def test_a_note_anchor_is_a_value_never_a_range() -> None:
    with pytest.raises(ValidationError):
        _parse({"type": "note", "x": {"from": 1}, "y": 2, "text": "x"})


def test_a_range_needs_a_bound() -> None:
    with pytest.raises(ValidationError, match="from or to"):
        _parse({"type": "band", "x": {}})


def test_text_is_one_line() -> None:
    with pytest.raises(ValidationError):
        _parse({"type": "rule", "y": 1, "text": "two\nlines"})


def test_series_takes_each_total_or_names() -> None:
    for series in ["each", "total", ["EMEA", "APAC"]]:
        _parse({"type": "rule", "y": "avg()", "series": series})
    with pytest.raises(ValidationError):
        _parse({"type": "rule", "y": "avg()", "series": "some"})


def test_style_opens_every_annotation_mark() -> None:
    rule = _parse(
        {
            "type": "rule",
            "y": 1,
            "style": {
                "marks": {
                    "rule": {"stroke": {"dasharray": "4 3"}},
                    "text": {
                        "halo": 0,
                        "position": {"edge": "top", "align": "end", "overlay": False},
                        "box": {"visible": True},
                        "dx": -10,
                    },
                }
            },
        }
    )
    assert isinstance(rule, RuleAnnotation)
    assert rule.style is not None
    assert rule.style.marks is not None
    assert rule.style.marks.text is not None
    assert rule.style.marks.text.dx == -10
