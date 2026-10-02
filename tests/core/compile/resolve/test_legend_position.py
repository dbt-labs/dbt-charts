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


def _bar(position: dict[str, Any] | None = None) -> BarChart:
    legend: dict[str, Any] = {} if position is None else {"position": position}
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
