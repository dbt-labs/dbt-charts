"""Tests for the DUAL_AXIS_COMPETING_SCALES render-warning detector."""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.authored._layer import (
    LayerAxisYStyle,
    LineLayer,
)
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.diagnostics import WARN_DUAL_AXIS_COMPETING_SCALES
from dbt_charts.core.render.warnings import (
    WarningContext,
    dual_axis_competing_scales as detector,
)

from ...core._board_utils import (
    make_test_resolved_board,
    make_test_resolved_chart,
)

_ROWS: list[dict[str, Any]] = [
    {"month": "Jan", "revenue": 5.0, "headcount": 40},
    {"month": "Feb", "revenue": 6.0, "headcount": 44},
]


def _detect(*layers: LineLayer, **chart_kwargs: Any) -> list[Any]:
    chart_kwargs.setdefault("y", "revenue")
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        title="",
        x="month",
        layers=list(layers),
        **chart_kwargs,
    )
    resolved = make_test_resolved_chart(chart, _ROWS)
    ctx = WarningContext(
        board_spec=make_test_resolved_board(charts={resolved.id: resolved}),
        chart_results={resolved.id: _ROWS},
        vega_specs={resolved.id: {"mark": "layered"}},
        layer_results={},
    )
    return detector.detect(ctx)


def _pinned(y: str | None = "headcount") -> LineLayer:
    return LineLayer(type="line", y=y, axis_y=LayerAxisYStyle(position="right"))


def test_fires_on_pinned_layer() -> None:
    (w,) = _detect(_pinned())
    assert w.code == WARN_DUAL_AXIS_COMPETING_SCALES.code
    assert w.chart == "c1"
    assert w.field is None
    assert "headcount" in w.message and "revenue" in w.message
    assert w.path == "charts.c1.layers.0.axis_y.position"
    assert [r.path for r in w.related] == ["charts.c1.y"]
    assert w.fix is not None


def test_two_pinned_layers_give_one_diagnostic() -> None:
    assert len(_detect(_pinned("headcount"), _pinned("revenue"))) == 1


def test_names_the_pinned_layer_not_the_first() -> None:
    (w,) = _detect(LineLayer(type="line", y="revenue"), _pinned())
    assert w.path == "charts.c1.layers.1.axis_y.position"


def test_pinned_layer_without_y_still_fires_naming_a_layer_with_y() -> None:
    """The engine makes y independent on any pin; the y-less layer is skipped."""
    (w,) = _detect(_pinned(None), LineLayer(type="line", y="headcount"))
    assert "headcount" in w.message
    assert w.path == "charts.c1.layers.0.axis_y.position"
    assert [r.path for r in w.related] == ["charts.c1.y", "charts.c1.layers.1.y"]


def test_silent_when_no_layer_has_y() -> None:
    assert _detect(_pinned(None)) == []


def test_silent_without_pinned_layer() -> None:
    assert _detect(LineLayer(type="line", y="headcount")) == []


def test_unset_base_y_names_no_column_and_no_related_path() -> None:
    (w,) = _detect(_pinned(), y=None)
    assert "separate from the base chart's measure." in w.message
    assert not w.related
