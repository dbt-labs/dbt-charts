"""Legend placement resolution: unset leaves are decided by the engine, authored
leaves win, unsupported combinations raise, and a pie's edge reaches both the
Vega-Lite legend and the attached table.
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.models.chart.normalized import BarChart, PieChart
from dbt_charts.core.compile.models.style.authored import (
    BarChartStylePatch,
    PieChartStylePatch,
)
from dbt_charts.core.compile.models.style.theme import LegendPositionStyle
from dbt_charts.core.compile.resolve.style.legend_position import (
    decide_legend_position,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.warnings import (
    WarningContext,
    legend_position_width_fallback as detector,
)

from ....core._board_utils import make_test_resolved_board, make_test_resolved_chart

_ROWS: list[dict[str, Any]] = [
    {"cat": "a", "series": "s1", "val": 1},
    {"cat": "a", "series": "s2", "val": 2},
]
_HYBRID = [("A", 40.0), ("B", 30.0), ("C", 20.0), ("D", 8.0), ("E", 1.5), ("F", 0.5)]
_FULL = [(f"R{i}", 7.0) for i in range(14)]
_DIRECT = [("A", 40.0), ("B", 30.0), ("C", 20.0), ("D", 10.0)]


def _bar(
    position: dict[str, Any] | None = None, direction: str | None = None
) -> BarChart:
    legend: dict[str, Any] = {} if position is None else {"position": position}
    if direction is not None:
        legend["direction"] = direction
    return BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="cat",
        y="val",
        color="series",
        style=BarChartStylePatch.model_validate({"legend": legend}),
    )


def _pie(position: dict[str, Any] | None = None, visible: bool = False) -> PieChart:
    legend: dict[str, Any] = {"visible": visible}
    if position is not None:
        legend["position"] = position
    return PieChart(
        id="p1",
        type="pie",
        query_name="q",
        theta="v",
        color="n",
        style=PieChartStylePatch.model_validate({"legend": legend}),
    )


def _pie_rows(values: list[tuple[str, float]]) -> list[dict[str, Any]]:
    return [{"n": name, "v": value} for name, value in values]


def test_unauthored_bar_resolves_every_leaf() -> None:
    position = make_test_resolved_chart(_bar(), _ROWS, width=900.0).legend.position
    assert (position.edge, position.align, position.overlay) == ("top", "start", False)


def test_unauthored_scatter_family_default_is_right_start_reserve() -> None:
    from dbt_charts.core.compile.models.chart.normalized import ScatterChart

    chart = ScatterChart(
        id="s1", type="scatter", query_name="q", x="val", y="val", color="series"
    )
    position = make_test_resolved_chart(chart, _ROWS, width=900.0).legend.position
    assert (position.edge, position.align, position.overlay) == (
        "right",
        "start",
        False,
    )


@pytest.mark.parametrize(
    ("authored", "expected"),
    [
        ({"edge": "bottom"}, ("bottom", "start", False)),
        ({"edge": "left"}, ("left", "start", False)),
        ({"edge": "top", "align": "end", "overlay": True}, ("top", "end", True)),
        (
            {"edge": "bottom", "align": "start", "overlay": True},
            ("bottom", "start", True),
        ),
    ],
)
def test_authored_leaves_win(
    authored: dict[str, Any], expected: tuple[str, str, bool]
) -> None:
    position = make_test_resolved_chart(
        _bar(authored), _ROWS, width=900.0
    ).legend.position
    assert (position.edge, position.align, position.overlay) == expected


def test_overlay_without_an_edge_anchors_to_the_top() -> None:
    position = make_test_resolved_chart(
        _bar({"overlay": True}), _ROWS, width=900.0
    ).legend.position
    assert (position.edge, position.overlay) == ("top", True)


@pytest.mark.parametrize(
    "authored",
    [
        {"edge": "left", "overlay": True},
        {"edge": "right", "align": "end", "overlay": True},
        {"edge": "top", "align": "end", "overlay": False},
        {"edge": "right", "align": "start", "overlay": True},
    ],
)
def test_unsupported_triple_raises_naming_the_supported_set(
    authored: dict[str, Any],
) -> None:
    with pytest.raises(ChartDataError) as raised:
        make_test_resolved_chart(_bar(authored), _ROWS, width=900.0)
    message = str(raised.value)
    assert "ERR-LEGEND-POSITION-UNSUPPORTED" in raised.value.code.code
    assert "any edge, `align: start|center`, `overlay: false`" in message
    assert "`edge: top|bottom`, `align: start|end`, `overlay: true`" in message
    assert "any edge, `align: center`, `overlay: true`" in message


@pytest.mark.parametrize("edge", ["left", "right", "top", "bottom"])
@pytest.mark.parametrize("overlay", [True, False])
def test_centered_legend_resolves_on_every_edge(edge: str, overlay: bool) -> None:
    position = make_test_resolved_chart(
        _bar({"edge": edge, "align": "center", "overlay": overlay}),
        _ROWS,
        width=900.0,
    ).legend.position
    assert (position.edge, position.align, position.overlay) == (
        edge,
        "center",
        overlay,
    )


def test_tiny_card_forces_top_and_the_override_names_the_authored_edge() -> None:
    resolved = make_test_resolved_chart(_bar({"edge": "bottom"}), _ROWS, width=200.0)
    assert resolved.legend.position.edge == "top"
    assert resolved.legend.position_overridden_by_width == "bottom"


def test_authored_top_edge_is_not_an_override() -> None:
    resolved = make_test_resolved_chart(_bar({"edge": "top"}), _ROWS, width=200.0)
    assert resolved.legend.position_overridden_by_width is None


def test_authored_align_alone_does_not_conflict_with_the_top_strip() -> None:
    resolved = make_test_resolved_chart(
        _bar({"align": "end", "overlay": True}), _ROWS, width=900.0
    )
    assert resolved.legend.position.edge == "top"
    assert resolved.legend.position.align == "end"
    assert resolved.legend.position_overridden_by_width is None


# --- pie ------------------------------------------------------------------


@pytest.mark.parametrize("edge", ["left", "right", "top", "bottom"])
def test_pie_edge_reaches_the_resolved_legend_in_direct_mode(edge: str) -> None:
    resolved = make_test_resolved_chart(
        _pie({"edge": edge}, visible=True), _pie_rows(_DIRECT), width=1000.0
    )
    assert resolved.attached_table_placement == "none"
    assert resolved.legend.position.edge == edge


@pytest.mark.parametrize("values", [_HYBRID, _FULL], ids=["hybrid", "full_table"])
@pytest.mark.parametrize("edge", ["left", "right", "top", "bottom"])
def test_pie_edge_places_the_attached_table(
    values: list[tuple[str, float]], edge: str
) -> None:
    resolved = make_test_resolved_chart(
        _pie({"edge": edge}), _pie_rows(values), width=1100.0
    )
    assert resolved.attached_table_placement == edge
    assert resolved.legend.position_overridden_by_width is None


def test_pie_without_an_edge_keeps_the_width_heuristic() -> None:
    wide = make_test_resolved_chart(_pie(), _pie_rows(_HYBRID), width=1100.0)
    narrow = make_test_resolved_chart(_pie(), _pie_rows(_HYBRID), width=340.0)
    assert wide.attached_table_placement == "right"
    assert narrow.attached_table_placement == "bottom"


def test_pie_side_edge_on_a_tiny_card_falls_back_loudly() -> None:
    resolved = make_test_resolved_chart(
        _pie({"edge": "left"}), _pie_rows(_HYBRID), width=340.0
    )
    assert resolved.attached_table_placement == "bottom"
    assert resolved.legend.position_overridden_by_width == "left"
    board = make_test_resolved_board(charts={resolved.id: resolved})
    warnings = detector.detect(
        WarningContext(
            board_spec=board,
            chart_results={resolved.id: _pie_rows(_HYBRID)},
            vega_specs={},
            layout_charts={resolved.id: resolved},
        )
    )
    assert len(warnings) == 1
    assert "left" in warnings[0].message
    assert "bottom" in warnings[0].message


def test_a_radial_decision_honors_align_and_ignores_overlay() -> None:
    position = decide_legend_position(
        LegendPositionStyle(edge="right", align="end", overlay=True),
        top_strip=False,
        radial=True,
    )
    assert (position.edge, position.align, position.overlay) == (
        "right",
        "end",
        False,
    )


@pytest.mark.parametrize("align", ["start", "center", "end"])
def test_pie_align_reaches_the_resolved_direct_legend(align: str) -> None:
    resolved = make_test_resolved_chart(
        _pie({"edge": "top", "align": align}, visible=True),
        _pie_rows(_DIRECT),
        width=1000.0,
    )
    assert (resolved.legend.position.edge, resolved.legend.position.align) == (
        "top",
        align,
    )


@pytest.mark.parametrize("align", ["start", "center", "end"])
@pytest.mark.parametrize("edge", ["left", "right", "top", "bottom"])
def test_pie_align_places_the_attached_table_along_its_edge(
    edge: str, align: str
) -> None:
    resolved = make_test_resolved_chart(
        _pie({"edge": edge, "align": align}), _pie_rows(_HYBRID), width=1100.0
    )
    assert resolved.attached_table_placement == edge
    assert resolved.attached_table_align == align


@pytest.mark.parametrize(
    ("edge", "expected"),
    [("left", "start"), ("right", "start"), ("top", "center"), ("bottom", "center")],
)
def test_an_unset_pie_align_keeps_each_edges_long_standing_default(
    edge: str, expected: str
) -> None:
    resolved = make_test_resolved_chart(
        _pie({"edge": edge}), _pie_rows(_HYBRID), width=1100.0
    )
    assert resolved.attached_table_align == expected


@pytest.mark.parametrize("leaf", ["edge", "align", "overlay"])
def test_an_explicit_null_leaf_means_the_engine_decides(leaf: str) -> None:
    resolved = make_test_resolved_chart(
        _bar({leaf: None}), _ROWS, width=900.0
    ).legend.position
    assert (resolved.edge, resolved.align, resolved.overlay) == ("top", "start", False)


def test_an_explicit_null_pie_edge_keeps_the_width_heuristic() -> None:
    resolved = make_test_resolved_chart(
        _pie({"edge": None}), _pie_rows(_HYBRID), width=1100.0
    )
    assert resolved.attached_table_placement == "right"


@pytest.mark.parametrize("width", [200.0, 900.0])
def test_an_unsupported_authored_triple_is_rejected_at_every_width(
    width: float,
) -> None:
    with pytest.raises(ChartDataError):
        make_test_resolved_chart(
            _bar({"edge": "left", "align": "end", "overlay": True}),
            _ROWS,
            width=width,
        )


def test_a_board_wide_unsupported_placement_spares_charts_with_no_legend() -> None:
    from ....core._svg_render import render_board_to_svg

    svg = render_board_to_svg(
        """
title: t
style:
  charts:
    legend:
      position:
        edge: left
        align: end
        overlay: true
queries:
  q:
    type: values
    rows:
      - {n: A, v: 60}
      - {n: B, v: 40}
  one:
    type: values
    rows:
      - {v: 40}
charts:
  t:
    query: q
    type: table
  k:
    query: one
    type: kpi
    value: v
  s:
    query: q
    type: spark_bar
    x: v
    y: n
rows:
  - cols:
      - t
      - k
      - s
"""
    )
    assert "ERR-" not in svg


@pytest.mark.parametrize(
    ("edge", "expected"),
    [
        ("top", "horizontal"),
        ("bottom", "horizontal"),
        ("left", "vertical"),
        ("right", "vertical"),
    ],
)
def test_unauthored_direction_follows_the_edge(edge: str, expected: str) -> None:
    resolved = make_test_resolved_chart(_bar({"edge": edge}), _ROWS, width=900.0)
    assert resolved.legend.direction == expected


def test_authored_direction_wins_over_the_edge() -> None:
    resolved = make_test_resolved_chart(
        _bar({"edge": "bottom"}, direction="vertical"), _ROWS, width=900.0
    )
    assert resolved.legend.direction == "vertical"


def test_direction_follows_a_top_edge_off_the_top_strip() -> None:
    resolved = make_test_resolved_chart(
        _pie({"edge": "top"}, visible=True), _pie_rows(_DIRECT), width=1000.0
    )
    assert resolved.legend.direction == "horizontal"


@pytest.mark.parametrize(
    "scope",
    [
        {"legend": {"direction": "vertical"}},
        {"bar": {"legend": {"direction": "vertical"}}},
    ],
    ids=["board", "family"],
)
def test_authored_direction_survives_the_board_and_family_cascade(
    scope: dict[str, Any],
) -> None:
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
    )

    context = resolve_chart_style_context(
        get_theme_style(), StylePatch.model_validate({"charts": scope})
    )
    resolved = resolve(_bar({"edge": "bottom"}), _ROWS, context, width=900.0)
    assert resolved.legend.direction == "vertical"


_MANY_ROWS: list[dict[str, Any]] = [
    {"cat": cat, "series": f"Series number {i}", "val": i + 1}
    for cat in "ab"
    for i in range(12)
]


def _many(width: float, **legend: Any) -> Any:
    legend = {"position": {"edge": "bottom"}, **legend}
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="cat",
        y="val",
        color="series",
        style=BarChartStylePatch.model_validate({"legend": legend}),
    )
    return make_test_resolved_chart(chart, _MANY_ROWS, width=width).legend


def test_a_bottom_legend_that_fits_stays_one_row() -> None:
    legend = _many(3000.0)
    assert (legend.direction, legend.columns) == ("horizontal", 0)


def test_a_bottom_legend_too_wide_for_one_row_wraps() -> None:
    legend = _many(500.0)
    assert (legend.direction, legend.columns) == ("horizontal", legend.compact_columns)


def test_authored_direction_and_columns_skip_the_fit_fallback() -> None:
    forced = _many(500.0, direction="horizontal")
    assert (forced.direction, forced.columns) == ("horizontal", 0)
    assert _many(500.0, columns=3).columns == 3


@pytest.mark.parametrize("family", ["bar", "line", "area", "scatter"])
@pytest.mark.parametrize("edge", ["bottom", "top"])
def test_every_cartesian_family_wraps_a_legend_too_wide_for_one_row(
    family: str, edge: str
) -> None:
    from dbt_charts.core.compile.models.chart import normalized as n
    from dbt_charts.core.compile.models.style import authored as a

    style = getattr(a, f"{family.capitalize()}ChartStylePatch").model_validate(
        {"legend": {"visible": True, "position": {"edge": edge}}}
    )
    chart = getattr(n, f"{family.capitalize()}Chart")(
        id="c1",
        type=family,
        query_name="q",
        x="cat",
        y="val",
        color="series",
        style=style,
    )
    wide = make_test_resolved_chart(chart, _MANY_ROWS, width=3000.0).legend
    narrow = make_test_resolved_chart(chart, _MANY_ROWS, width=500.0).legend
    assert (wide.direction, wide.columns) == ("horizontal", 0)
    assert (narrow.direction, narrow.columns) == ("horizontal", narrow.compact_columns)


def test_an_overlay_legend_is_never_wrapped() -> None:
    legend = _many(500.0, position={"edge": "bottom", "align": "end", "overlay": True})
    assert legend.columns == 0


def test_a_categorical_heatmap_legend_wraps() -> None:
    from dbt_charts.core.compile.models.chart.normalized import HeatmapChart

    chart = HeatmapChart(
        id="h1",
        type="heatmap",
        query_name="q",
        x="cat",
        y="val",
        color="series",
        style={"legend": {"visible": True, "position": {"edge": "bottom"}}},
    )
    legend = make_test_resolved_chart(chart, _MANY_ROWS, width=500.0).legend
    assert (legend.direction, legend.columns) == ("horizontal", legend.compact_columns)


def test_a_direct_pie_key_too_wide_for_one_row_wraps() -> None:
    rows = _pie_rows([(f"Segment name {i}", 14.0) for i in range(7)])
    resolved = make_test_resolved_chart(
        _pie({"edge": "bottom"}, visible=True), rows, width=600.0
    )
    assert resolved.legend.visible
    assert (resolved.legend.direction, resolved.legend.columns) == (
        "horizontal",
        resolved.legend.compact_columns,
    )


def test_a_categorical_point_map_legend_wraps() -> None:
    from dbt_charts.core.compile.models.chart.normalized import PointMapChart

    rows = [{**r, "lat": 40.0, "lon": -100.0} for r in _MANY_ROWS]
    chart = PointMapChart(
        id="m1",
        type="point_map",
        query_name="q",
        latitude="lat",
        longitude="lon",
        color="series",
        style={"legend": {"visible": True, "position": {"edge": "bottom"}}},
    )
    legend = make_test_resolved_chart(chart, rows, width=500.0).legend
    assert (legend.direction, legend.columns) == ("horizontal", legend.compact_columns)


def test_a_multi_measure_heatmap_legend_wraps() -> None:
    from dbt_charts.core.compile.models.chart.normalized import HeatmapChart

    measures = [f"Measure name number {i}" for i in range(12)]
    chart = HeatmapChart(
        id="h2",
        type="heatmap",
        query_name="q",
        x="cat",
        y=measures,
        style={"legend": {"visible": True, "position": {"edge": "bottom"}}},
    )
    rows = [{"cat": "a", **dict.fromkeys(measures, 1)}]
    legend = make_test_resolved_chart(chart, rows, width=500.0).legend
    assert (legend.direction, legend.columns) == ("horizontal", legend.compact_columns)


def test_a_choropleth_legend_reads_the_value_field_not_the_color_channel() -> None:
    from dbt_charts.core.compile.models.chart.normalized import GeoshapeChart

    rows = [
        {"region": f"R{i}", "tier": f"Tier group number {i}", "n": i} for i in range(14)
    ]
    chart = GeoshapeChart(
        id="g1",
        type="geoshape",
        query_name="q",
        geo_source="us-states",
        lookup="region",
        value="tier",
        color="n",
        style={"legend": {"visible": True, "position": {"edge": "bottom"}}},
    )
    legend = make_test_resolved_chart(chart, rows, width=500.0).legend
    assert legend.columns == legend.compact_columns
