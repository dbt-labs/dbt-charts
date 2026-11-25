"""Registered codes for the cartesian path's former bare ChartDataError raises."""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.diagnostics import codes_render
from dbt_charts.core.diagnostics.chart_data import ChartDataError

from ...._results import rows_result

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
    (
        "ERR_TICKS_TIME_UNIT_NO_CADENCE",
        {"time_unit": "hour", "grains": "day, month"},
        "'hour' has no step-anchored cadence",
    ),
    ("ERR_LAYERED_NO_PALETTE", {"chart_id": "c"}, "no color palette"),
    ("ERR_LAYER_X_NOT_QUANTITATIVE", _LAYER_X, "must itself resolve"),
    ("ERR_LAYER_X_DOMAIN_MISMATCH", _LAYER_X, "same discrete-label domain"),
    ("ERR_LAYER_LABEL_COLLISION", {"label": "L", "chart_id": "c"}, "label 'L'"),
    ("ERR_HEATMAP_COLOR_NO_VALUES", {"field": "x"}, "color field 'x' holds no numeric"),
    (
        "ERR_SUPPORT_TABLE_FORMAT_NOT_NUMERIC",
        {"source": "grp", "format_spec": ",d", "value": "store"},
        "non-numeric value 'store'",
    ),
]


@pytest.mark.parametrize(("name", "params", "expected"), CASES)
def test_code_registered_and_renders(
    name: str, params: dict[str, Any], expected: str
) -> None:
    error_code = getattr(codes_render, name)
    error = ChartDataError.from_code(error_code, **params)
    assert error.code.code == name.replace("_", "-")
    assert expected in str(error)


def _code(exc_info: pytest.ExceptionInfo[ChartDataError]) -> str:
    assert exc_info.value.code is not None
    return exc_info.value.code.code


def test_a_heatmap_color_without_a_number_draws_no_results_instead_of_painting_nan() -> (
    None
):
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.chart.normalized import HeatmapChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
    from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

    board_style, context = resolve_style_and_context(get_theme_style())
    chart = HeatmapChart(
        id="h",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="heatmap",
        x="grp",
        y="grp",
        color="x",
    )
    data = [{"x": None, "grp": "a"}, {"x": None, "grp": "b"}]
    resolved = resolve(chart, data, chart_style_context=context)
    artifact = render_resolved_chart(resolved, rows_result(data), board_style)
    assert ">No results</text>" in str(artifact.payload)
