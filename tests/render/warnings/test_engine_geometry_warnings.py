"""The geometry detectors turn the engine's warning params into diagnostics.

The engine measures the band, panel and label-slot widths (Rust tests pin those
numbers); a detector only words what the engine reported and anchors it on the
authored field. The real pipeline, from `render()` to the diagnostic, is covered
by the `*_warning_e2e` tests in `tests/core/render`.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.authored._layer import AreaLayer, LineLayer
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.diagnostics import (
    WARN_ANNOTATION_NOTE_MOVED,
    WARN_BAR_BAND_WIDTH_TOO_NARROW,
    WARN_BAR_GROUPED_SERIES_COINCIDE,
    WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM,
    WARN_LAYOUT_MIN_EXCEEDS_HEIGHT,
    WARN_VALUE_LABELS_CROWD_WIDTH,
    Diagnostic,
)
from dbt_charts.core.diagnostics.registry import WarningCode
from dbt_charts.core.render.warnings import (
    WarningContext,
    annotation_note_moved,
    bar_band_width_too_narrow,
    bar_grouped_series_coincide,
    facet_panel_width_below_minimum,
    layout_min_exceeds_height,
    value_labels_crowd_width,
)
from dbt_charts.core.render.warnings.base import engine_warnings

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart

_ROWS = [{"cat": "a", "val": 1}, {"cat": "b", "val": 2}]


def _ctx(code: WarningCode, params: dict[str, Any]) -> WarningContext:
    chart = make_test_resolved_chart(
        BarChart(id="c1", type="bar", query_name="q", x="cat", y="val"), _ROWS
    )
    return WarningContext(
        board_spec=make_test_resolved_board(charts={"c1": chart}),
        chart_results={"c1": _ROWS},
        geometry_warnings={"c1": {code.code: [{"chart_id": "c1", **params}]}},
    )


def _only(diagnostics: list[Diagnostic]) -> Diagnostic:
    assert len(diagnostics) == 1
    return diagnostics[0]


def test_engine_warnings_yield_every_record_for_a_code() -> None:
    ctx = _ctx(
        WARN_VALUE_LABELS_CROWD_WIDTH,
        {"label_width": 31.0, "slot_width": 18.0, "layer": None},
    )
    ctx.geometry_warnings["c1"][WARN_VALUE_LABELS_CROWD_WIDTH.code].append(
        {"chart_id": "c1", "label_width": 40.0, "slot_width": 18.0, "layer": None}
    )
    found = list(engine_warnings(ctx, WARN_VALUE_LABELS_CROWD_WIDTH))
    assert [(cid, p["label_width"]) for cid, p in found] == [("c1", 31.0), ("c1", 40.0)]
    assert list(engine_warnings(ctx, WARN_BAR_BAND_WIDTH_TOO_NARROW)) == []


def test_a_chart_the_engine_raised_nothing_for_has_no_diagnostic() -> None:
    ctx = WarningContext(
        board_spec=make_test_resolved_board(), chart_results={}, geometry_warnings={}
    )
    for detector in (
        annotation_note_moved,
        bar_band_width_too_narrow,
        bar_grouped_series_coincide,
        facet_panel_width_below_minimum,
        layout_min_exceeds_height,
        value_labels_crowd_width,
    ):
        assert detector.detect(ctx) == []


def test_band_too_narrow_words_the_categorical_message_and_anchors_on_x() -> None:
    params = {
        "bar_width": 2.5,
        "distinct": 240,
        "min_band_width": 4.0,
        "render_width": 600.0,
        "series": 1,
    }
    diagnostic = _only(
        bar_band_width_too_narrow.detect(_ctx(WARN_BAR_BAND_WIDTH_TOO_NARROW, params))
    )
    assert diagnostic.code == WARN_BAR_BAND_WIDTH_TOO_NARROW.code
    assert diagnostic.path == "charts.c1.x"
    assert diagnostic.field == "cat"
    assert "240 bands x 1 series" in diagnostic.message


def test_band_too_narrow_over_a_numeric_x_words_the_overlap_message() -> None:
    params = {
        "bar_width": 9.0,
        "distinct": 12,
        "min_band_width": 6.5,
        "render_width": 600.0,
        "series": 1,
        "continuous": True,
    }
    diagnostic = _only(
        bar_band_width_too_narrow.detect(_ctx(WARN_BAR_BAND_WIDTH_TOO_NARROW, params))
    )
    assert "bars will overlap their neighbors" in diagnostic.message
    assert "~6.50px apart" in diagnostic.message


def test_grouped_series_that_coincide_name_the_x_field() -> None:
    diagnostic = _only(
        bar_grouped_series_coincide.detect(
            _ctx(WARN_BAR_GROUPED_SERIES_COINCIDE, {"field": "cat"})
        )
    )
    assert diagnostic.path == "charts.c1.x"
    assert diagnostic.field == "cat"


def test_facet_panels_under_the_floor_anchor_on_multiples() -> None:
    params = {"min_panel_px": 120.0, "panel_cols": 12, "panel_width": 40.0}
    diagnostic = _only(
        facet_panel_width_below_minimum.detect(
            _ctx(WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM, params)
        )
    )
    assert diagnostic.path == "charts.c1.multiples"
    assert "12 column panel(s) at 40px" in diagnostic.message


def test_a_category_floor_above_the_authored_height_prints_both() -> None:
    params = {"authored_height": 120.0, "min_height": 900.0, "n_categories": 40}
    diagnostic = _only(
        layout_min_exceeds_height.detect(_ctx(WARN_LAYOUT_MIN_EXCEEDS_HEIGHT, params))
    )
    assert "40 category bands" in diagnostic.message
    assert "900px" in diagnostic.message
    assert "120px" in diagnostic.message


def _crowd(**params: Any) -> dict[str, Any]:
    return {"label_width": 31.0, "slot_width": 18.0, "layer": None, **params}


def test_crowded_value_labels_name_the_bar_mark_labels() -> None:
    diagnostic = _only(
        value_labels_crowd_width.detect(_ctx(WARN_VALUE_LABELS_CROWD_WIDTH, _crowd()))
    )
    assert diagnostic.path == "charts.c1.style.marks.bar.labels"
    assert diagnostic.field == "val"
    assert "31px wide but has only 18px of room" in diagnostic.message


def _layered_ctx(*records: dict[str, Any]) -> WarningContext:
    labeled = {"labels": {"visible": True}}
    layers = [
        LineLayer.model_validate(
            {"type": "line", "y": "line_y", "style": {"marks": {"line": labeled}}}
        ),
        AreaLayer.model_validate(
            {"type": "area", "y": "area_y", "style": {"marks": {"line": labeled}}}
        ),
        LineLayer.model_validate(
            {"type": "line", "y": "dot_y", "style": {"marks": {"point": labeled}}}
        ),
    ]
    chart = make_test_resolved_chart(
        BarChart(id="c1", type="bar", query_name="q", x="cat", y="val", layers=layers),
        _ROWS,
    )
    return WarningContext(
        board_spec=make_test_resolved_board(charts={"c1": chart}),
        chart_results={"c1": _ROWS},
        geometry_warnings={
            "c1": {
                WARN_VALUE_LABELS_CROWD_WIDTH.code: [
                    {"chart_id": "c1", **record} for record in records
                ]
            }
        },
    )


def test_a_layers_crowded_labels_name_that_layers_mark_labels() -> None:
    diagnostics = value_labels_crowd_width.detect(
        _layered_ctx(_crowd(layer=0), _crowd(layer=1), _crowd(layer=2))
    )
    assert [(d.path, d.field) for d in diagnostics] == [
        ("charts.c1.layers.0.style.marks.line.labels", "line_y"),
        ("charts.c1.layers.1.style.marks.line.labels", "area_y"),
        ("charts.c1.layers.2.style.marks.point.labels", "dot_y"),
    ]


def test_one_label_set_raised_many_times_is_one_diagnostic_keeping_the_worst() -> None:
    diagnostics = value_labels_crowd_width.detect(
        _layered_ctx(
            _crowd(layer=0, label_width=30.0, slot_width=25.0),
            _crowd(layer=None, label_width=30.0, slot_width=25.0),
            _crowd(layer=0, label_width=30.0, slot_width=10.0),
            _crowd(layer=0, label_width=30.0, slot_width=20.0),
        )
    )
    assert [d.path for d in diagnostics] == [
        "charts.c1.layers.0.style.marks.line.labels",
        "charts.c1.style.marks.bar.labels",
    ]
    assert "30px wide but has only 10px of room" in diagnostics[0].message


def test_a_wide_charts_crowded_labels_name_no_synthetic_measure() -> None:
    rows = [{"cat": "a", "x": 1, "y": 2}, {"cat": "b", "x": 3, "y": 4}]
    chart = make_test_resolved_chart(
        BarChart(id="c1", type="bar", query_name="q", x="cat", y=["x", "y"]), rows
    )
    ctx = WarningContext(
        board_spec=make_test_resolved_board(charts={"c1": chart}),
        chart_results={"c1": rows},
        geometry_warnings={
            "c1": {WARN_VALUE_LABELS_CROWD_WIDTH.code: [{"chart_id": "c1", **_crowd()}]}
        },
    )
    assert _only(value_labels_crowd_width.detect(ctx)).field is None


def test_a_moved_note_anchors_on_its_annotation() -> None:
    diagnostic = _only(
        annotation_note_moved.detect(_ctx(WARN_ANNOTATION_NOTE_MOVED, {"index": 2}))
    )
    assert diagnostic.path == "charts.c1.annotations[2]"
    assert "annotation 2" in diagnostic.message
