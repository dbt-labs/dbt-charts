"""Coupling test: an unresolvable `style.legend.values` entry must be BOTH
dropped from the emitted legend AND surfaced by `WARN_LEGEND_VALUES_UNRESOLVED`,
on the exact same resolved chart + rows.

`test_legend_values_resolution.py` renders real specs and asserts the drop,
deferring the warning to a comment; `test_legend_values_unresolved.py`
hand-builds `WarningContext` and never renders. Neither suite touches the
same board, so widening one gate without the other can drop an authored
entry with no diagnostic while both suites stay green -- exactly the CRITICAL
shape rounds 2, 3, and 7 each produced on this feature. This test resolves
once per checked family and asserts both halves against that single resolve.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized.geoshape import GeoshapeChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.chart.resolved.geoshape import ResolvedGeoshapeChart
from dbt_charts.core.compile.models.chart.resolved.pie import ResolvedPieChart
from dbt_charts.core.compile.models.style.authored.geoshape import (
    GeoshapeChartStylePatch,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.compile.resolve.chart.geo import _resolve_geoshape
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.chart.artifacts import ChartRenderData
from dbt_charts.core.render.chart.emitters.geo import GeoshapeEmitter
from dbt_charts.core.render.chart.spec import RenderBox
from dbt_charts.core.render.chart.translate import translate_to_vl
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart
from dbt_charts.core.render.warnings import (
    WarningContext,
    legend_values_unresolved as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart
from ...core._engine_render import render_pie_svg
from ...core._svg_render import legend_label_order
from ...core.conftest import chart_pane

_BOGUS = "TOTALLY_BOGUS_ENTRY"

_Case = tuple[ResolvedChart, ChartRenderData, list[str]]


def _color_legend_values(spec: dict[str, Any] | str) -> list[str]:
    assert isinstance(spec, dict)
    return chart_pane(spec)["encoding"]["color"]["legend"]["values"]


def _cartesian(make_chart: Any, chart_type: str) -> _Case:
    rows: ChartRenderData = [
        {"cat": "x", "series": "A", "val": 1},
        {"cat": "x", "series": "B", "val": 2},
    ]
    chart = make_chart(
        chart_type,
        x="cat",
        y="val",
        color="series",
        style={
            "legend": {"visible": True, "values": ["A", _BOGUS]},
            "endpoint_labels": {"visible": False},
        },
    )
    resolved = make_test_resolved_chart(chart, rows)
    spec = render_resolved_chart(
        resolved, rows, resolve_style(get_theme_style())
    ).payload
    return resolved, rows, _color_legend_values(spec)


def _scatter(make_chart: Any) -> _Case:
    rows: ChartRenderData = [
        {"x_field": 1, "y_field": 2, "series": "A"},
        {"x_field": 2, "y_field": 3, "series": "B"},
    ]
    chart = make_chart(
        "scatter",
        x="x_field",
        y="y_field",
        color="series",
        style={"legend": {"values": ["A", _BOGUS]}},
    )
    resolved = make_test_resolved_chart(chart, rows)
    spec = render_resolved_chart(
        resolved, rows, resolve_style(get_theme_style())
    ).payload
    return resolved, rows, _color_legend_values(spec)


def _heatmap(make_chart: Any) -> _Case:
    rows: ChartRenderData = [
        {"x_field": "Mon", "y_field": "AM", "series": "A"},
        {"x_field": "Tue", "y_field": "PM", "series": "B"},
    ]
    chart = make_chart(
        "heatmap",
        x="x_field",
        y="y_field",
        color="series",
        style={"legend": {"values": ["A", _BOGUS]}},
    )
    resolved = make_test_resolved_chart(chart, rows)
    spec = render_resolved_chart(
        resolved, rows, resolve_style(get_theme_style())
    ).payload
    return resolved, rows, _color_legend_values(spec)


def _pie(make_chart: Any) -> _Case:
    rows: ChartRenderData = [
        {"segment": "Direct", "revenue": 10},
        {"segment": "Organic", "revenue": 20},
    ]
    chart = make_chart(
        "pie",
        theta="revenue",
        color="segment",
        style={"legend": {"visible": True, "values": ["Direct", _BOGUS]}},
    )
    resolved = make_test_resolved_chart(chart, rows)
    assert isinstance(resolved, ResolvedPieChart)
    svg = render_pie_svg(resolved, rows, width=resolved.resolution_width)
    return resolved, rows, legend_label_order(svg)


def _geoshape(_make_chart: Any) -> _Case:
    rows: ChartRenderData = [
        {"state": "CA", "tier": "west"},
        {"state": "TX", "tier": "south"},
    ]
    board_style = resolve_chart_style_context(get_theme_style())
    chart = GeoshapeChart(
        id="geo1",
        type="geoshape",
        geo_source="fake-geo-source",
        lookup="state",
        value="tier",
        style=GeoshapeChartStylePatch.model_validate(
            {"legend": {"values": ["west", _BOGUS]}}
        ),
    )
    resolved = _resolve_geoshape(chart, rows, board_style, 800.0, None)
    assert isinstance(resolved, ResolvedGeoshapeChart)
    vl = translate_to_vl(
        GeoshapeEmitter().emit(
            resolved, RenderBox(width=600.0, height=300.0), regroup((), rows)
        )
    )
    return resolved, rows, vl["layer"][1]["encoding"]["color"]["legend"]["values"]


_CASES: dict[str, Callable[[Any], _Case]] = {
    "bar": lambda make_chart: _cartesian(make_chart, "bar"),
    "area": lambda make_chart: _cartesian(make_chart, "area"),
    "line": lambda make_chart: _cartesian(make_chart, "line"),
    "scatter": _scatter,
    "heatmap": _heatmap,
    "pie": _pie,
    "geoshape": _geoshape,
}


@pytest.mark.parametrize("family", sorted(_CASES))
def test_unresolvable_entry_is_dropped_and_warned(make_chart: Any, family: str) -> None:
    """One board, both halves: a `style.legend.values` entry that resolves
    against nothing must be absent from the emitted legend, and `detect()`
    must fire a WARN_LEGEND_VALUES_UNRESOLVED for that exact resolved chart
    and rows."""
    resolved, rows, legend_values = _CASES[family](make_chart)
    assert legend_values, f"{family}: expected a non-empty fallback legend"
    assert _BOGUS not in legend_values, f"{family}: bogus entry was not dropped"

    ctx = WarningContext(
        board_spec=make_test_resolved_board(charts={resolved.id: resolved}),
        chart_results={resolved.id: rows},
        vega_specs={},
    )
    warnings = detector.detect(ctx)
    assert warnings, f"{family}: detector did not fire for the dropped entry"
    assert warnings[0].chart == resolved.id
