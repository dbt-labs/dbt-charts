"""Registered codes for the cartesian path's former bare ChartDataError raises."""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.diagnostics import codes_render
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.features.endpoint_labels import (
    recascade_endpoint_labels,
)

_LAYER_X = {
    "chart_id": "c",
    "x_field": "d",
    "layer_type": "nominal",
    "base_field": "x",
    "base_type": "quantitative",
}

CASES: list[tuple[str, dict[str, Any], str]] = [
    ("ERR_STEP_BAND_MULTI_SERIES", {"chart_id": "c"}, "chart 'c'"),
    (
        "ERR_BAND_STEP_MULTI_METRIC",
        {"chart_type": "Line", "chart_id": "c"},
        "Line chart 'c': band-aware step curve",
    ),
    (
        "ERR_BAR_OVERLAP_CONTINUOUS_AXIS",
        {"chart_id": "c", "axis": "x"},
        "continuous x-axis",
    ),
    (
        "ERR_BAR_OVERLAP_SERIES_COUNT",
        {"chart_id": "c", "n_series": 3},
        "(got 3)",
    ),
    ("ERR_BAR_MULTI_METRIC_NO_X", {"chart_id": "c"}, "require an x field"),
    (
        "ERR_TICKS_TIME_UNIT_NO_CADENCE",
        {"time_unit": "hour", "grains": "day, month"},
        "'hour' has no step-anchored cadence",
    ),
    ("ERR_ENDPOINT_LABELS_RECASCADE_RATIO", {"ratio": 0.0}, "got 0.0"),
    ("ERR_LAYERED_NO_COLOR_SCALE", {"chart_id": "c"}, "no shared color scale"),
    ("ERR_LAYERED_NO_PALETTE", {"chart_id": "c"}, "no color palette"),
    ("ERR_LAYER_X_NOT_QUANTITATIVE", _LAYER_X, "must itself resolve"),
    ("ERR_LAYER_X_DOMAIN_MISMATCH", _LAYER_X, "same discrete-label domain"),
    ("ERR_LAYER_ORDER_TRANSFORM_MISSING", {"order_field": "o"}, "field 'o'"),
    ("ERR_LAYER_LABEL_COLLISION", {"label": "L", "chart_id": "c"}, "label 'L'"),
    ("ERR_SUPPORT_TABLE_NO_X_ENCODING", {}, "requires an x-encoding"),
    ("ERR_SUPPORT_TABLE_INDEPENDENT_X", {}, "'independent'"),
    ("ERR_SUPPORT_TABLE_X_FIELD_DISAGREE", {}, "disagree on field"),
    ("ERR_SUPPORT_TABLE_NO_Y_ENCODING", {}, "category y-encoding"),
]


@pytest.mark.parametrize(("name", "params", "expected"), CASES)
def test_code_registered_and_renders(
    name: str, params: dict[str, Any], expected: str
) -> None:
    error_code = getattr(codes_render, name)
    error = ChartDataError.from_code(error_code, **params)
    assert error.code.code == name.replace("_", "-")
    assert expected in str(error)


def test_recascade_nonpositive_ratio_raises_registered_code() -> None:
    with pytest.raises(ChartDataError) as exc_info:
        recascade_endpoint_labels(
            anchors={"a": 1.0, "b": 2.0},
            pixel_gap=18.0,
            y_domain_min=0.0,
            y_domain_max=10.0,
            label_mark_leaves=[],
            height_correction_ratio=0.0,
            emitted={"a": 1.0, "b": 2.0},
        )
    assert exc_info.value.code.code == "ERR-ENDPOINT-LABELS-RECASCADE-RATIO"


def _code(exc_info: pytest.ExceptionInfo[ChartDataError]) -> str:
    assert exc_info.value.code is not None
    return exc_info.value.code.code


@pytest.mark.parametrize(
    ("rows", "base_type", "expected"),
    [
        ([{"d": "a"}], "quantitative", "ERR-LAYER-X-NOT-QUANTITATIVE"),
        ([{"d": 1}], "nominal", "ERR-LAYER-X-DOMAIN-MISMATCH"),
    ],
)
def test_layer_x_mismatch_raises_its_registered_code(
    rows: list[dict[str, Any]], base_type: str, expected: str
) -> None:
    from dbt_charts.core.render.chart.emitters._overlay import (
        _resolve_layer_x_encoding,
    )

    with pytest.raises(ChartDataError) as exc:
        _resolve_layer_x_encoding(
            "d", rows, {"field": "x", "type": base_type}, False, "c"
        )
    assert _code(exc) == expected


def test_layered_chart_without_a_color_scale_raises_its_registered_code() -> None:
    from dbt_charts.core.render.chart.features.endpoint_labels import (
        _layer_color_scale,
    )
    from dbt_charts.core.render.chart.spec import ChartSpec

    spec = ChartSpec(mark="layered", layers=[ChartSpec(mark="line", encoding={})])
    with pytest.raises(ChartDataError) as exc:
        _layer_color_scale(spec, "c")
    assert _code(exc) == "ERR-LAYERED-NO-COLOR-SCALE"
