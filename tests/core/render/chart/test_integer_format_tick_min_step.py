"""Regression: an integer (or other fixed-decimal) style.number_format on a
small-magnitude measure axis must not let a computed tick ladder paint
duplicate labels.

Repro: values 0..2 on style.number_format: integer nice-round to ticks
0, 0.5, 1, 1.5, 2 -- each rounds to a whole number under the integer format,
so the axis reads 0, 1, 1, 2, 2 with a gridline sitting on no real label.

nice_tick_values (core/numeric.py) floors its own step at the format's fixed
decimal count (compile.format.tick_min_step_for_format) wherever resolve bakes
a ladder. Where it bakes none (unauthored ticks.count such as stark, or a
stacked bar with a negative row), the format reaches Vega-Lite as a resolved
tickMinStep instead -- never beside baked values.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AxisTicksStylePatch,
    AxisYStylePatch,
    BarChartStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

from ..._svg_render import leaf_kind_subtrees, render_board_to_svg

_NUMERIC = re.compile(r"^[−-]?[\d,]+(\.\d+)?%?$")
_BOARD_STYLE = resolve_chart_style_context(get_theme_style())
_INTEGER_DATA = [
    {"status": "new", "ticket_count": 1},
    {"status": "open", "ticket_count": 2},
    {"status": "pending", "ticket_count": 1},
    {"status": "hold", "ticket_count": 0},
    {"status": "solved", "ticket_count": 2},
    {"status": "closed", "ticket_count": 1},
]


def _measure_axis_labels(chart_svg: str) -> list[str]:
    """Every tick-label group's text values, for the one numeric axis-label group."""
    groups: list[list[str]] = []
    for m in re.finditer(r'<g class="mark-text role-axis-label"[^>]*>', chart_svg):
        depth = 0
        for tag in re.finditer(r"<g\b[^>]*>|</g>", chart_svg[m.start() :]):
            depth += 1 if tag.group(0).startswith("<g") else -1
            if depth == 0:
                group = chart_svg[m.start() : m.start() + tag.end()]
                groups.append(re.findall(r"<text[^>]*>([^<]*)</text>", group))
                break
    numeric_groups = [g for g in groups if g and all(_NUMERIC.match(t) for t in g)]
    assert len(numeric_groups) == 1, (
        f"expected exactly one numeric axis-label group, found {len(numeric_groups)}: "
        f"{groups}"
    )
    return numeric_groups[0]


_TICKETS_QUERY = """
queries:
  tickets_by_status:
    columns: [status, ticket_count]
    values:
      - [new, 1]
      - [open, 2]
      - [pending, 1]
      - [hold, 0]
      - [solved, 2]
      - [closed, 1]
"""


def _board(orientation: str) -> str:
    return f"""
title: Integer format tick min step
{_TICKETS_QUERY}
charts:
  tickets_by_status:
    title: "Tickets by Status"
    query: tickets_by_status
    type: bar
    x: status
    y: ticket_count
    style: {{orientation: {orientation}, number_format: integer}}
rows:
  - tickets_by_status
"""


def test_vertical_bar_integer_format_no_duplicate_tick_labels() -> None:
    svg = render_board_to_svg(_board("vertical"))
    chart = leaf_kind_subtrees(svg, "chart")[0]
    labels = _measure_axis_labels(chart)
    assert len(labels) == len(set(labels)), f"duplicate tick labels: {labels}"
    assert set(labels) <= {"0", "1", "2"}


def test_horizontal_bar_integer_format_no_duplicate_tick_labels() -> None:
    svg = render_board_to_svg(_board("horizontal"))
    chart = leaf_kind_subtrees(svg, "chart")[0]
    labels = _measure_axis_labels(chart)
    assert len(labels) == len(set(labels)), f"duplicate tick labels: {labels}"
    assert set(labels) <= {"0", "1", "2"}


def test_stark_theme_integer_format_no_duplicate_tick_labels() -> None:
    # stark leaves ticks.count unset, so Vega-Lite picks the ticks itself;
    # the format floor reaches it as a resolved tickMinStep instead.
    svg = render_board_to_svg(f"extends: stark\n{_board('horizontal')}")
    chart = leaf_kind_subtrees(svg, "chart")[0]
    assert _measure_axis_labels(chart) == ["0", "1", "2"]


def test_baked_values_axis_carries_no_tick_min_step() -> None:
    axis = _resolved_y_axis(
        BarChartStylePatch(orientation="vertical", number_format="integer")
    )
    assert "values" in axis
    assert "tickMinStep" not in axis


_AUTHORED_SCALE_VALUES_BOARD = f"""
title: Authored ladder wins over derived min step
{_TICKETS_QUERY}
charts:
  tickets_by_status:
    title: "Tickets by Status"
    query: tickets_by_status
    type: bar
    x: status
    y: ticket_count
    style:
      orientation: vertical
      number_format: integer
      axis_y:
        scale:
          values: [0, 0.5, 1, 1.5, 2]
rows:
  - tickets_by_status
"""


def test_authored_scale_values_ladder_not_silently_thinned() -> None:
    # An explicit axis_y.scale.values ladder names every tick outright -- the
    # format-driven floor must not suppress rungs from it, even though two
    # pairs paint identically under the integer format (the author's choice,
    # not a silent engine decision).
    svg = render_board_to_svg(_AUTHORED_SCALE_VALUES_BOARD)
    chart = leaf_kind_subtrees(svg, "chart")[0]
    labels = _measure_axis_labels(chart)
    assert labels == ["0", "1", "1", "2", "2"], labels


def _resolved_y_axis(style: BarChartStylePatch) -> dict[str, Any]:
    reset_config()
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "bar",
            "x": "status",
            "y": "ticket_count",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "style": style,
        }
    )
    resolve(chart, _INTEGER_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _INTEGER_DATA, width=400)
    reset_config()
    # orientation: vertical (forced below) puts the measure on VL's y channel.
    return spec["encoding"]["y"]["axis"]


def test_authored_ticks_count_ladder_respects_derived_min_step() -> None:
    # ticks.count is the remedy ERR-TICKS-INTERVAL-MEASURE-AXIS tells authors
    # to use on a measure axis instead of ticks.step -- the baked ladder
    # itself (nice_tick_values, not just the VL tickMinStep passthrough) must
    # respect the integer format's floor: [0, 0.5, 1, 1.5, 2] (what
    # nice_tick_values(0, 2, 6) picks unfloored) would repaint as
    # [0, 1, 1, 2, 2] under the integer format.
    axis = _resolved_y_axis(
        BarChartStylePatch(
            orientation="vertical",
            number_format="integer",
            axis_y=AxisYStylePatch(ticks=AxisTicksStylePatch(count=8)),
        )
    )
    assert axis["values"] == [0.0, 1.0, 2.0, 3.0], axis["values"]


_NARROW_DOMAIN_BOARD = """
title: Domain narrower than the derived step must not go blank
queries:
  q:
    columns: [x, y]
    values:
      - [a, 10.2]
      - [b, 10.7]
      - [c, 10.4]
charts:
  c:
    query: q
    type: line
    x: x
    y: y
    style: {number_format: integer}
rows:
  - c
"""


def test_domain_narrower_than_derived_step_does_not_paint_a_blank_axis() -> None:
    # y in [10.2, 10.7]: no rung of the integer format's floored ladder
    # (10.0, 11.0) falls inside this domain, so nice_tick_values falls back
    # to its unfloored pick rather than clip every rung to a blank axis.
    svg = render_board_to_svg(_NARROW_DOMAIN_BOARD)
    chart = leaf_kind_subtrees(svg, "chart")[0]
    labels = _measure_axis_labels(chart)
    assert labels == ["10", "10", "11"], labels


_ZERO_ANCHORED_SUB_UNIT_BOARD = """
title: Zero-anchored sub-unit domain must keep its floored ladder and headroom
queries:
  q:
    columns: [x, y]
    values:
      - [a, 0.12]
      - [b, 0.51]
      - [c, 0.33]
charts:
  c:
    query: q
    type: bar
    x: x
    y: y
    style: {orientation: vertical, number_format: integer}
rows:
  - c
"""


def test_zero_anchored_sub_unit_domain_keeps_the_floored_ladder() -> None:
    # y in [0.12, 0.51], zero-anchored: domain_min (0.0) is itself a real
    # in-domain rung of the floored ladder (0.0, 1.0), so the floor must
    # bake it -- falling back to no ladder here (as a domain-span check
    # alone would) hands the axis to Vega-Lite's own auto-ticks, which
    # duplicate far worse (10+ labels, most reading "0") and drops the
    # baked headroom bound.
    svg = render_board_to_svg(_ZERO_ANCHORED_SUB_UNIT_BOARD)
    chart = leaf_kind_subtrees(svg, "chart")[0]
    labels = _measure_axis_labels(chart)
    assert labels == ["0"], labels

    reset_config()
    chart_model = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "bar",
            "x": "x",
            "y": "y",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "style": BarChartStylePatch(
                orientation="vertical", number_format="integer"
            ),
        }
    )
    data = [{"x": "a", "y": 0.12}, {"x": "b", "y": 0.51}, {"x": "c", "y": 0.33}]
    resolve(chart_model, data, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart_model, data, width=400)
    reset_config()
    # domainMax stays baked (headroom-expanded past the data max), not
    # discarded by the floored bake.
    assert spec["encoding"]["y"]["scale"]["domainMax"] is not None


def test_zero_max_row_gets_the_same_mixed_sign_pin() -> None:
    # y max is exactly 0.0 (a zero-valued row, not negative): the same
    # inflation risk applies as a strictly positive max, since 0 is still the
    # data's own ceiling here, not a floor negative_floor's strict < 0 guard
    # describes. Without the raw_max >= 0 (not > 0) widening, this case fell
    # through to the all-negative branch, which reads negative_floor -- None,
    # since 0 isn't < 0 either -- leaving domain_min unset and the floored
    # ladder's -1.0 rung free to leak through as domainMin.
    reset_config()
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "bar",
            "x": "x",
            "y": "y",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "style": BarChartStylePatch(
                orientation="vertical", number_format="integer"
            ),
        }
    )
    data = [{"x": "a", "y": 0.0}, {"x": "b", "y": -0.05}, {"x": "c", "y": -0.02}]
    resolve(chart, data, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, data, width=400)
    reset_config()
    scale = spec["encoding"]["y"]["scale"]
    assert scale["domainMin"] < -0.05, scale
    assert scale["domainMin"] > -0.06, scale


def _resolved_scale_for_family(
    chart_type: str,
    orientation: str | None,
    extra_style: dict[str, Any],
) -> dict[str, Any]:
    """Resolve a mixed-sign zero-anchored chart and return the measure
    channel's VL scale dict -- x for horizontal bar, y for every other
    family/orientation."""
    reset_config()
    style: dict[str, Any] = {"number_format": "integer", **extra_style}
    if orientation is not None:
        style["orientation"] = orientation
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": chart_type,
            "x": "x",
            "y": "y",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "style": style,
        }
    )
    data = [{"x": "a", "y": -0.05}, {"x": "b", "y": 0.08}, {"x": "c", "y": 0.02}]
    resolve(chart, data, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, data, width=400)
    reset_config()
    channel = "x" if orientation == "horizontal" else "y"
    return spec["encoding"][channel]["scale"]


@pytest.mark.parametrize(
    ("chart_type", "orientation", "extra_style"),
    [
        ("bar", "vertical", {}),
        ("bar", "horizontal", {}),
        ("line", None, {"axis_y": {"scale": {"continuous": {"zero": True}}}}),
        ("area", None, {"axis_y": {"scale": {"continuous": {"zero": True}}}}),
        ("scatter", None, {"axis_y": {"scale": {"continuous": {"zero": True}}}}),
    ],
    ids=["bar_vertical", "bar_horizontal", "line", "area", "scatter"],
)
def test_mixed_sign_pin_reaches_render_on_every_measure_channel_emit_path(
    chart_type: str, orientation: str | None, extra_style: dict[str, Any]
) -> None:
    # The resolve-baked domain_min must actually reach VL on each family's
    # own emit path, not just resolve to the right value in isolation.
    # Line/area/scatter only reach the zero-anchored branch with an authored
    # zero: true; bar anchors at zero by default.
    scale = _resolved_scale_for_family(chart_type, orientation, extra_style)
    assert scale["domainMin"] < -0.05, scale
    assert scale["domainMin"] > -0.06, scale


def _stacked_bar_axis_values(rows: list[dict[str, str | float]]) -> list[Any] | None:
    reset_config()
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "bar",
            "x": "x",
            "y": "y",
            "color": "series",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "style": {
                "orientation": "vertical",
                "stack": "zero",
                "number_format": "integer",
                "axis_y": {"scale": {"continuous": {"zero": True}}},
            },
        }
    )
    resolve(chart, rows, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, rows, width=400)
    reset_config()
    top = spec["hconcat"][0] if "hconcat" in spec else spec
    return top["encoding"]["y"]["axis"].get("values")


def test_stacked_positive_totals_get_the_floored_ladder() -> None:
    # Every row is non-negative, so every per-category stacked total is too:
    # the format floor applies the same way it does to a non-stacked measure.
    values = _stacked_bar_axis_values(
        [
            {"x": "a", "series": "s1", "y": 1.2},
            {"x": "a", "series": "s2", "y": 0.5},
            {"x": "b", "series": "s1", "y": 0.4},
            {"x": "b", "series": "s2", "y": 0.3},
        ]
    )
    assert values == [0.0, 1.0, 2.0], values


def test_is_quantitative_false_skips_only_the_step_floor_not_the_bake() -> None:
    # scatter's y is a numeric-string column Vega still renders nominal (a
    # numeric-looking category, not this axis's measure). is_quantitative
    # gates ONLY the format-driven step floor -- the ladder itself still
    # bakes at the authored ticks.count either way, since
    # numeric_column_values coerces the strings into real floats regardless.
    # An unfloored ladder over 1/2/1.5/2.5-ish values is expected to still
    # show its own natural (non-duplicating, non-integer) step here.
    reset_config()
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "scatter",
            "x": "hours",
            "y": "code",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "style": {
                "axis_y": {
                    "ticks": {"count": 6},
                    "labels": {"format": "integer"},
                }
            },
        }
    )
    data = [
        {"hours": 1.0, "code": "0.5"},
        {"hours": 2.0, "code": "1.5"},
        {"hours": 3.0, "code": "2"},
    ]
    resolve(chart, data, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, data, width=400)
    reset_config()
    values = spec["encoding"]["y"]["axis"].get("values")
    # Unfloored: nice_tick_values naturally picks a 0.5 step here -- forcing
    # is_quantitative True would bake a coarser integer-floored ladder.
    assert values == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5], values


def test_stark_stacked_negative_rows_floor_via_tick_min_step() -> None:
    reset_config()
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "bar",
            "x": "x",
            "y": "y",
            "color": "series",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "style": {
                "orientation": "vertical",
                "stack": "zero",
                "number_format": "integer",
                "axis_y": {"scale": {"continuous": {"zero": True}}},
            },
        }
    )
    rows = [
        {"x": "a", "series": "s1", "y": 1.0},
        {"x": "a", "series": "s2", "y": 1.0},
        {"x": "b", "series": "s1", "y": -0.01},
    ]
    stark_style, stark_ctx = resolve_style_and_context(get_theme_style("stark"))
    resolve(chart, rows, chart_style_context=stark_ctx)
    spec = generate_vega_lite_spec(
        chart, rows, width=400, board_style=stark_style, chart_style_context=stark_ctx
    )
    reset_config()
    top = spec["hconcat"][0] if "hconcat" in spec else spec
    axis = top["encoding"]["y"]["axis"]
    assert "values" not in axis
    assert axis["tickMinStep"] == 1.0


_STACKED_NEGATIVE_BOARD = """
title: Stacked with a negative row
queries:
  q:
    columns: [x, s, v]
    values:
      - [a, s1, 1]
      - [a, s2, 1]
      - [b, s1, -0.01]
charts:
  c:
    query: q
    type: bar
    x: x
    y: v
    color: s
    style:
      orientation: vertical
      stack: zero
      number_format: integer
      axis_y:
        scale:
          continuous:
            zero: true
rows:
  - c
"""


def test_stark_stacked_negative_rows_paint_no_duplicate_tick_labels() -> None:
    svg = render_board_to_svg(f"extends: stark\n{_STACKED_NEGATIVE_BOARD}")
    chart = leaf_kind_subtrees(svg, "chart")[0]
    assert _measure_axis_labels(chart) == ["0", "1", "2"]


def test_dual_axis_layer_does_not_inherit_the_base_tick_min_step() -> None:
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.chart.normalized import LineChart
    from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
    from dbt_charts.core.render.chart.emitters.line import LineEmitter
    from dbt_charts.core.render.chart.spec import RenderBox
    from dbt_charts.core.render.chart.translate import translate_to_vl

    _, ctx = resolve_style_and_context(get_theme_style("stark"))
    data = [
        {"month": "Jan", "revenue": 1200.0, "conversion": 0.11},
        {"month": "Feb", "revenue": 1800.0, "conversion": 0.22},
    ]
    chart = LineChart(
        id="l1",
        type="line",
        x="month",
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        variable_dependencies=set(),
        format="currency_whole",
        layers=[
            LineLayer(
                type="line",
                y="conversion",
                axis_y={"position": "right", "labels": {"format": "percent"}},
            )
        ],
    )
    resolved = resolve(chart, data, ctx)
    spec = LineEmitter().emit(
        resolved, RenderBox(width=600.0, height=300.0), regroup((), data)
    )
    vl = translate_to_vl(spec)

    def axes(node: dict[str, Any]) -> list[dict[str, Any]]:
        found = []
        for layer in node.get("layer", []):
            axis = layer.get("encoding", {}).get("y", {}).get("axis")
            if isinstance(axis, dict):
                found.append(axis)
            found.extend(axes(layer))
        return found

    by_orient = {a.get("orient"): a for a in axes(vl)}
    assert by_orient["left"]["tickMinStep"] == 1.0
    assert "tickMinStep" not in by_orient["right"]


def _spec_for(
    style: dict[str, Any], rows: list[dict[str, Any]], **fields: Any
) -> dict[str, Any]:
    reset_config()
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "bar",
            "x": "x",
            "y": "y",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "style": {"orientation": "vertical", "number_format": "integer", **style},
            **fields,
        }
    )
    resolve(chart, rows, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, rows, width=400)
    reset_config()
    return spec["hconcat"][0] if "hconcat" in spec else spec


_STACK_ROWS = [
    {"x": "a", "series": "s1", "y": 4000.0},
    {"x": "a", "series": "s2", "y": 2480.0},
    {"x": "b", "series": "s1", "y": -700.0},
]


def test_stacked_negative_row_at_large_magnitude_keeps_its_ladder() -> None:
    # The floor cannot bind at this magnitude, so nothing is dropped.
    axis = _spec_for(
        {"stack": "zero", "axis_y": {"scale": {"continuous": {"zero": True}}}},
        _STACK_ROWS,
        color="series",
    )["encoding"]["y"]["axis"]
    assert "values" in axis and "tickMinStep" not in axis


def test_stacked_negative_row_keeps_an_authored_tick_count() -> None:
    axis = _spec_for(
        {
            "stack": "zero",
            "axis_y": {
                "ticks": {"count": 3},
                "scale": {"continuous": {"zero": True}},
            },
        },
        _STACK_ROWS,
        color="series",
    )["encoding"]["y"]["axis"]
    assert len(axis["values"]) <= 3


def test_stacked_negative_row_keeps_an_authored_domain_ladder() -> None:
    axis = _spec_for(
        {"stack": "zero", "axis_y": {"scale": {"continuous": {"domain": [-2, 4]}}}},
        [
            {"x": "a", "series": "s1", "y": 1.0},
            {"x": "a", "series": "s2", "y": 1.0},
            {"x": "b", "series": "s1", "y": -0.5},
        ],
        color="series",
    )["encoding"]["y"]["axis"]
    assert "values" in axis and "tickMinStep" not in axis


@pytest.mark.parametrize(
    "theme_header", ["", "extends: stark\n"], ids=["clarity", "stark"]
)
def test_normalize_stack_keeps_its_quartile_ticks_under_a_zero_decimal_format(
    theme_header: str,
) -> None:
    board = f"""{theme_header}
title: Normalize with integer format
queries:
  q:
    columns: [x, s, v]
    values:
      - [a, s1, 3]
      - [a, s2, 1]
      - [b, s1, 1]
      - [b, s2, 2]
charts:
  c:
    query: q
    type: bar
    x: x
    y: v
    color: s
    style:
      orientation: vertical
      stack: normalize
      number_format: integer
rows:
  - c
"""
    chart = leaf_kind_subtrees(render_board_to_svg(board), "chart")[0]
    labels = _measure_axis_labels(chart)
    assert labels == ["0%", "25%", "50%", "75%", "100%"], labels


def test_log_scale_axis_carries_no_tick_min_step() -> None:
    reset_config()
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "line",
            "x": "x",
            "y": "y",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "style": {
                "number_format": "integer",
                "axis_y": {"scale": {"continuous": {"type": "log"}}},
            },
        }
    )
    rows = [{"x": "a", "y": 0.02}, {"x": "b", "y": 0.9}]
    resolve(chart, rows, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, rows, width=400)
    reset_config()
    assert "tickMinStep" not in spec["encoding"]["y"]["axis"]


@pytest.mark.parametrize("chart_type", ["bar", "area"])
def test_normalize_stack_resolves_no_tick_step(chart_type: str) -> None:
    # The axis paints percent regardless of the authored format, so the
    # resolved model must not publish a step derived from that format.
    reset_config()
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": chart_type,
            "x": "x",
            "y": "y",
            "color": "series",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "style": {"stack": "normalize", "number_format": "integer"},
        }
    )
    rows = [
        {"x": "a", "series": "s1", "y": 3.0},
        {"x": "a", "series": "s2", "y": 1.0},
    ]
    resolved = resolve(chart, rows, chart_style_context=_BOARD_STYLE)
    reset_config()
    assert resolved.style.axis_y.ticks.step is None


def test_stacked_small_negative_row_drops_the_ladder_the_floor_would_inflate() -> None:
    # An authored ticks.count bakes a ladder; the floored one would start at
    # -1 and pin domainMin there for data reaching only -0.01. No ladder is
    # baked instead and the floor reaches Vega-Lite as tickMinStep.
    axis = _spec_for(
        {"stack": "zero", "axis_y": {"scale": {"continuous": {"zero": True}}}},
        [
            {"x": "a", "series": "s1", "y": 0.5},
            {"x": "a", "series": "s2", "y": 0.4},
            {"x": "b", "series": "s1", "y": -0.01},
        ],
        color="series",
    )["encoding"]["y"]["axis"]
    assert "values" not in axis
    assert axis["tickMinStep"] == 1.0


def test_histogram_count_axis_emits_the_resolved_tick_min_step() -> None:
    reset_config()
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "histogram",
            "x": "x",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "style": {"number_format": "integer"},
        }
    )
    rows = [{"x": v} for v in (1, 2, 2, 3, 5)]
    resolve(chart, rows, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, rows, width=400)
    reset_config()
    top = spec["hconcat"][0] if "hconcat" in spec else spec
    assert top["encoding"]["y"]["axis"]["tickMinStep"] == 1.0
