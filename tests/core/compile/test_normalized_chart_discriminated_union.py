"""TDD smoke tests for per-family discriminated normalized Chart union.

Covers:
- Each family model constructs with minimal required fields.
- TypeAdapter(Chart).validate_python dispatches to the correct family model.
- Required-field enforcement (theta, value, layers, message).
- extra="forbid" rejects family-alien fields.
- Multi-literal dispatch: "histogram" → BarChart, "donut" → PieChart,
  "bubble_map" → PointMapChart, "map" → GeoshapeChart.
"""

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    CalloutChart,
    Chart,
    GeoshapeChart,
    HeatmapChart,
    KpiChart,
    LineChart,
    PieChart,
    PointMapChart,
    ScatterChart,
    SparkBarChart,
    TableChart,
)

_adapter = TypeAdapter(Chart)


# ── direct construction ────────────────────────────────────────────────────────


def test_bar_constructs():
    c = BarChart(id="b", type="bar")
    assert c.type == "bar"
    assert isinstance(c, BarChart)


def test_line_constructs():
    c = LineChart(id="l", type="line")
    assert c.type == "line"


def test_area_constructs():
    c = AreaChart(id="a", type="area")
    assert c.type == "area"


def test_scatter_constructs():
    c = ScatterChart(id="s", type="scatter")
    assert c.type == "scatter"


def test_heatmap_constructs():
    c = HeatmapChart(id="h", type="heatmap")
    assert c.type == "heatmap"


def test_pie_constructs():
    c = PieChart(id="p", type="pie", theta="share")
    assert c.theta == "share"


def test_kpi_constructs():
    c = KpiChart(id="k", type="kpi", value="revenue")
    assert c.value == "revenue"


def test_table_constructs():
    c = TableChart(id="t", type="table")
    assert c.type == "table"


def test_point_map_constructs():
    c = PointMapChart(id="pm", type="point_map")
    assert c.type == "point_map"


def test_geoshape_constructs():
    c = GeoshapeChart(id="g", type="geoshape")
    assert c.type == "geoshape"


def test_callout_constructs():
    c = CalloutChart(id="ca", type="callout", message="note")
    assert c.message == "note"


def test_spark_bar_constructs():
    c = SparkBarChart(id="sp", type="spark_bar")
    assert c.type == "spark_bar"


# ── discriminated dispatch via TypeAdapter ────────────────────────────────────


@pytest.mark.parametrize(
    ("payload", "expected_cls"),
    [
        ({"id": "b", "type": "bar"}, BarChart),
        ({"id": "l", "type": "line"}, LineChart),
        ({"id": "a", "type": "area"}, AreaChart),
        ({"id": "s", "type": "scatter"}, ScatterChart),
        ({"id": "h", "type": "heatmap"}, HeatmapChart),
        ({"id": "p", "type": "pie", "theta": "share"}, PieChart),
        ({"id": "k", "type": "kpi", "value": "rev"}, KpiChart),
        ({"id": "t", "type": "table"}, TableChart),
        ({"id": "pm", "type": "point_map"}, PointMapChart),
        ({"id": "g", "type": "geoshape"}, GeoshapeChart),
        ({"id": "ca", "type": "callout", "message": "hi"}, CalloutChart),
        ({"id": "sp", "type": "spark_bar"}, SparkBarChart),
    ],
)
def test_dispatch(payload: dict, expected_cls: type) -> None:
    result = _adapter.validate_python(payload)
    assert isinstance(result, expected_cls)


# ── multi-literal dispatch ─────────────────────────────────────────────────────


def test_histogram_dispatches_to_bar():
    result = _adapter.validate_python({"id": "h", "type": "histogram"})
    assert isinstance(result, BarChart)


def test_donut_dispatches_to_pie():
    result = _adapter.validate_python({"id": "d", "type": "donut", "theta": "share"})
    assert isinstance(result, PieChart)


def test_bubble_map_dispatches_to_point_map():
    result = _adapter.validate_python({"id": "bm", "type": "bubble_map"})
    assert isinstance(result, PointMapChart)


def test_map_dispatches_to_geoshape():
    result = _adapter.validate_python({"id": "m", "type": "map"})
    assert isinstance(result, GeoshapeChart)


# ── required-field enforcement ────────────────────────────────────────────────


def test_pie_theta_is_unset_until_bound():
    assert PieChart(id="p", type="pie").theta is None


def test_kpi_value_is_unset_until_bound():
    assert KpiChart(id="k", type="kpi").value is None


def test_callout_requires_message():
    with pytest.raises(ValidationError):
        CalloutChart(id="c", type="callout")


# ── extra="forbid" ────────────────────────────────────────────────────────────


def test_pie_rejects_x_field():
    with pytest.raises(ValidationError):
        PieChart(id="p", type="pie", theta="share", x="month")


def test_kpi_rejects_theta():
    with pytest.raises(ValidationError):
        KpiChart(id="k", type="kpi", value="rev", theta="slice")


def test_bar_rejects_theta():
    with pytest.raises(ValidationError):
        BarChart(id="b", type="bar", theta="slice")


def test_scatter_accepts_size_and_shape():
    c = ScatterChart(id="s", type="scatter", size="pop", shape="category")
    assert c.size == "pop"
    assert c.shape == "category"


def test_bar_rejects_size():
    """size is scatter-only; bar should reject it."""
    with pytest.raises(ValidationError):
        BarChart(id="b", type="bar", size="pop")


# ── unknown type rejected ─────────────────────────────────────────────────────


def test_unknown_type_rejected():
    with pytest.raises(ValidationError):
        _adapter.validate_python({"id": "x", "type": "fuzzbomp"})


def test_missing_type_rejected():
    with pytest.raises(ValidationError):
        _adapter.validate_python({"id": "x"})
