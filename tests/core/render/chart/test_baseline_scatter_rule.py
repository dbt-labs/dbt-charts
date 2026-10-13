"""Scatter zero-rule regression: BaselineFeature must draw the heavy zero rule
for a zero-anchored scatter, matching bar/line/area, instead of leaving y=0 as
an ordinary hairline gridline.

Harness copied from ``test_scatter_zero_scale.py`` (compile -> full VL spec).
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.style.authored import ScatterChartStylePatch
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())

# ratio = 6000/33000 ~= 0.18 <= 0.25 -> abstain, anchored at zero (all-positive).
_ABSTAIN_DATA = [
    {"x": "2024-01", "y": 6000.0},
    {"x": "2024-02", "y": 20000.0},
    {"x": "2024-03", "y": 33000.0},
]
# ratio = 88/96 ~= 0.917 > 0.25 -> smart-zero fires zero:false (zoomed axis).
_FAR_RATIO_DATA = [
    {"x": "a", "y": 88},
    {"x": "b", "y": 91},
    {"x": "c", "y": 96},
]
# All-negative -> _pick_scale's optional-zero heuristic abstains entirely
# (out of scope by design), leaving the axis data-fitted well above 0.
_ALL_NEGATIVE_DATA = [
    {"x": "a", "y": -95},
    {"x": "b", "y": -72},
    {"x": "c", "y": -60},
    {"x": "d", "y": -48},
    {"x": "e", "y": -31},
    {"x": "f", "y": -20},
    {"x": "g", "y": -17},
]
# Straddles zero -> the (zoomed, headroom-expanded) domain naturally spans it.
_STRADDLE_DATA = [
    {"x": "a", "y": -40},
    {"x": "b", "y": -10},
    {"x": "c", "y": 5},
    {"x": "d", "y": 30},
    {"x": "e", "y": 60},
]
# Base y stays all-negative on its own (no straddle, no anchor); y2 straddles
# zero and shares the scatter's y scale via an overlay layer, so 0 lands in
# the domain the base series alone would never reach.
_LAYERED_ALL_NEGATIVE_DATA = [
    {"x": "a", "y": -95, "y2": -5},
    {"x": "b", "y": -60, "y2": 8},
    {"x": "c", "y": -17, "y2": 20},
]
# Base y stays all-negative and y2 stays all-negative too. The bar layer's
# zero anchor would put 0 in this shared domain, but resolve pins the scale to
# -101.8/-3.2 first and the bars are clipped short of it.
_LAYERED_ALL_NEGATIVE_BAR_OVERLAY_DATA = [
    {"x": "a", "y": -95, "y2": -30},
    {"x": "b", "y": -60, "y2": -22},
    {"x": "c", "y": -17, "y2": -10},
]
# Base y is all-negative and y2 is all-positive -- neither series straddles
# zero on its own, but the union of the two brackets it.
_LAYERED_BRACKET_NEGATIVE_BASE_DATA = [
    {"x": "a", "y": -95, "y2": 5},
    {"x": "b", "y": -60, "y2": 12},
    {"x": "c", "y": -17, "y2": 20},
]
# Same bracket, signs reversed: base all-positive, overlay all-negative. The
# base's own ratio (88/96 ~= 0.917) is above the smart-zero threshold, so
# resolve bakes zero:False on the base alone -- this runs the pinned branch,
# where only the shared-domain union with the overlay can put 0 on the plot.
_LAYERED_BRACKET_POSITIVE_BASE_DATA = [
    {"x": "a", "y": 88, "y2": -20},
    {"x": "b", "y": 91, "y2": -12},
    {"x": "c", "y": 96, "y2": -5},
]


def _spec(
    chart_type: str,
    data: list[dict[str, Any]],
    style: ScatterChartStylePatch | None = None,
) -> dict[str, Any]:
    reset_config()
    payload: dict[str, Any] = {
        "id": "t",
        "type": chart_type,
        "x": "x",
        "y": "y",
        "style": style,
    }
    chart = TypeAdapter(Chart).validate_python(payload)
    return generate_vega_lite_spec(
        chart, data, width=400, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )


def _rule_layers(spec: dict[str, Any]) -> list[dict[str, Any]]:
    layers = spec.get("layer", [])
    return [lay for lay in layers if lay.get("mark", {}).get("type") == "rule"]


def test_zero_anchored_scatter_emits_zero_rule_styled_from_grid_threshold() -> None:
    """A zero-anchored scatter (all-positive, close-to-zero ratio) must draw
    the heavy zero rule at datum 0, styled identically to the same rule on a
    bar chart under the same board style (both read `axis_y.grid.threshold`)."""
    scatter_spec = _spec("scatter", _ABSTAIN_DATA)
    rule_layers = _rule_layers(scatter_spec)
    assert len(rule_layers) == 1
    rule = rule_layers[0]
    assert rule["encoding"]["y"]["datum"] == 0

    bar_spec = _spec("bar", [{"x": "a", "y": 100}, {"x": "b", "y": 200}])
    bar_rule = _rule_layers(bar_spec)[0]
    assert rule["mark"]["color"] == bar_rule["mark"]["color"]
    assert rule["mark"]["strokeWidth"] == bar_rule["mark"]["strokeWidth"]


def test_pinned_zero_false_far_from_zero_scatter_emits_no_zero_rule() -> None:
    """A chart-level `axis_y.scale.continuous.zero: false` pin on data that
    doesn't straddle zero must suppress the rule."""
    style = ScatterChartStylePatch.model_validate(
        {"axis_y": {"scale": {"continuous": {"zero": False}}}}
    )
    spec = _spec("scatter", _FAR_RATIO_DATA, style=style)
    assert _rule_layers(spec) == []


def test_grid_not_visible_scatter_emits_no_zero_rule() -> None:
    """`axis_y.grid.visible: false` suppresses the rule (existing guard)."""
    style = ScatterChartStylePatch.model_validate(
        {"axis_y": {"grid": {"visible": False}}}
    )
    spec = _spec("scatter", _ABSTAIN_DATA, style=style)
    assert _rule_layers(spec) == []


def test_log_scale_scatter_emits_no_zero_rule() -> None:
    """A log-typed measure axis can never carry the datum:0 rule (existing
    guard: a literal 0 breaks a log domain)."""
    style = ScatterChartStylePatch.model_validate(
        {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
    )
    spec = _spec("scatter", _ABSTAIN_DATA, style=style)
    assert _rule_layers(spec) == []


def test_layered_scatter_with_target_line_still_emits_zero_rule() -> None:
    """A scatter with an authored overlay layer must not lose the zero rule.

    ``render_cartesian_overlay`` hoists the base encoding into sub-layers,
    leaving only ``x`` on the outer spec — a gate reading ``spec.encoding``
    for the y channel would answer False there and silently drop the rule
    exactly when a target line is layered onto a zero-anchored scatter."""
    payload: dict[str, Any] = {
        "id": "t",
        "type": "scatter",
        "x": "x",
        "y": "y",
        "layers": [{"type": "line", "y": "y", "label": "target"}],
        "style": None,
    }
    reset_config()
    chart = TypeAdapter(Chart).validate_python(payload)
    spec = generate_vega_lite_spec(
        chart,
        _ABSTAIN_DATA,
        width=400,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    assert len(_rule_layers(spec)) == 1


def test_all_negative_scatter_emits_no_zero_rule() -> None:
    """All-negative data leaves the zero-anchor heuristic abstaining, so the
    axis is data-fitted well above 0 — firing the rule anyway paints a rule
    outside the plot band, not on its floor."""
    spec = _spec("scatter", _ALL_NEGATIVE_DATA)
    assert _rule_layers(spec) == []


def test_straddling_scatter_still_emits_zero_rule() -> None:
    """Data straddling zero keeps 0 inside the (zoomed) domain naturally —
    the all-negative guard above must not suppress this case too."""
    spec = _spec("scatter", _STRADDLE_DATA)
    assert len(_rule_layers(spec)) == 1


def test_wide_scatter_straddling_zero_emits_zero_rule() -> None:
    """A wide (``y: [a, b]``) scatter whose folded measures straddle zero
    must still draw the rule -- ``_zero_in_shared_domain`` reads the real
    query columns (``chart.wide_measures``) here, not the synthetic
    ``WIDE_VALUE_FIELD``, which is absent from every raw row and would
    silently read as an empty (never-straddling) domain."""
    payload: dict[str, Any] = {
        "id": "t",
        "type": "scatter",
        "x": "x",
        "y": ["rev", "cost"],
        "style": None,
    }
    data = [
        {"x": "a", "rev": 100, "cost": -40},
        {"x": "b", "rev": -200, "cost": 90},
        {"x": "c", "rev": 150, "cost": -70},
    ]
    reset_config()
    chart = TypeAdapter(Chart).validate_python(payload)
    spec = generate_vega_lite_spec(
        chart, data, width=400, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )
    assert len(_rule_layers(spec)) == 1


def test_layered_scatter_with_straddling_line_overlay_emits_zero_rule() -> None:
    """An all-negative base scatter with a shared-scale line overlay whose
    own values straddle zero must still draw the rule -- the overlay's data
    drags 0 into the domain the base series alone never reaches."""
    payload: dict[str, Any] = {
        "id": "t",
        "type": "scatter",
        "x": "x",
        "y": "y",
        "layers": [{"type": "line", "y": "y2", "label": "t"}],
        "style": None,
    }
    reset_config()
    chart = TypeAdapter(Chart).validate_python(payload)
    spec = generate_vega_lite_spec(
        chart,
        _LAYERED_ALL_NEGATIVE_DATA,
        width=400,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    assert len(_rule_layers(spec)) == 1


def test_layered_scatter_bar_overlay_anchors_an_unpinned_shared_scale() -> None:
    """The `layer.type == "bar"` shortcut in `_zero_in_shared_domain`, on the
    shape where it is still decisive: an unset `ticks.count` leaves both
    measure edges unbaked, so nothing pins the scale and VL's own bar-to-zero
    extension is what puts 0 in the shared domain.

    Both series are all-negative, so neither straddles zero on its own and the
    value union alone would answer False. Remove the shortcut and the rule
    disappears.
    """
    payload: dict[str, Any] = {
        "id": "t",
        "type": "scatter",
        "x": "x",
        "y": "y",
        "layers": [{"type": "bar", "y": "y2", "label": "t"}],
        "style": {"axis_y": {"ticks": {"count": None}}},
    }
    reset_config()
    chart = TypeAdapter(Chart).validate_python(payload)
    spec = generate_vega_lite_spec(
        chart,
        _LAYERED_ALL_NEGATIVE_BAR_OVERLAY_DATA,
        width=400,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    assert len(_rule_layers(spec)) == 1


def test_layered_scatter_with_all_negative_bar_overlay_draws_no_zero_rule() -> None:
    """A bar layer does not anchor a scale that resolve has already pinned.

    "VL bars always extend to/from 0" holds only while Vega-Lite is free to
    auto-fit the domain. Here the compiled Vega scale carries domainMin
    -101.8 / domainMax -3.2 alongside `zero: true`, and the pins win: the bars
    are clipped short of 0 and a datum-0 rule has no position on the plot."""
    payload: dict[str, Any] = {
        "id": "t",
        "type": "scatter",
        "x": "x",
        "y": "y",
        "layers": [{"type": "bar", "y": "y2", "label": "t"}],
        "style": None,
    }
    reset_config()
    chart = TypeAdapter(Chart).validate_python(payload)
    spec = generate_vega_lite_spec(
        chart,
        _LAYERED_ALL_NEGATIVE_BAR_OVERLAY_DATA,
        width=400,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    assert _rule_layers(spec) == []


def test_layered_scatter_brackets_zero_across_negative_base_and_positive_overlay() -> (
    None
):
    """Neither the all-negative base nor the all-positive overlay straddles
    zero by itself, but the shared y scale unions them and the union spans
    zero -- the rule must still fire on that bracketed domain."""
    payload: dict[str, Any] = {
        "id": "t",
        "type": "scatter",
        "x": "x",
        "y": "y",
        "layers": [{"type": "line", "y": "y2", "label": "t"}],
        "style": None,
    }
    reset_config()
    chart = TypeAdapter(Chart).validate_python(payload)
    spec = generate_vega_lite_spec(
        chart,
        _LAYERED_BRACKET_NEGATIVE_BASE_DATA,
        width=400,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    assert len(_rule_layers(spec)) == 1


def test_layered_scatter_brackets_zero_across_positive_base_and_negative_overlay() -> (
    None
):
    """Same bracket as above with signs reversed: an all-positive base and an
    all-negative overlay still union to a domain spanning zero."""
    payload: dict[str, Any] = {
        "id": "t",
        "type": "scatter",
        "x": "x",
        "y": "y",
        "layers": [{"type": "line", "y": "y2", "label": "t"}],
        "style": None,
    }
    reset_config()
    chart = TypeAdapter(Chart).validate_python(payload)
    spec = generate_vega_lite_spec(
        chart,
        _LAYERED_BRACKET_POSITIVE_BASE_DATA,
        width=400,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    assert len(_rule_layers(spec)) == 1


def test_rotated_dot_plot_scatter_emits_x_zero_rule_not_y_zero_rule() -> None:
    """Unlike line/area, scatter has no orientation field -- a "dot plot"
    recipe rotates by putting the quantitative value on x and the category on
    y. The measure-axis datum:0 rule must not fire against a categorical
    y-axis, even though nothing pinned `scale.continuous.zero=False` there
    (regression: test_chart_shape_recipes.py::test_dot_plot_recipe_survives_rotation).

    x is still quantitative and straddles zero, though, so the independent
    quantitative-x zero rule (``_apply_x_threshold``'s generalization of the
    measure-axis rule) fires there -- the rotated dot plot is exactly the
    shape that previously had nothing at either axis."""
    payload: dict[str, Any] = {
        "id": "t",
        "type": "scatter",
        "x": "value",
        "y": "region",
        "style": None,
    }
    reset_config()
    chart = TypeAdapter(Chart).validate_python(payload)
    data = [{"region": "a", "value": 5}, {"region": "b", "value": -3}]
    spec = generate_vega_lite_spec(
        chart, data, width=400, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )
    rules = _rule_layers(spec)
    assert len(rules) == 1
    assert rules[0]["encoding"]["x"]["datum"] == 0
    assert "datum" not in rules[0]["encoding"].get("y", {})


def _bubble_spec(data: list[dict[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "t",
        "type": "scatter",
        "x": "x",
        "y": "y",
        "size": "s",
        "style": None,
    }
    reset_config()
    chart = TypeAdapter(Chart).validate_python(payload)
    return generate_vega_lite_spec(
        chart, data, width=400, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )


def _vega_size_range(spec: dict[str, Any]) -> list[float]:
    import vl_convert as vlc

    vega = vlc.vegalite_to_vega(spec)
    (size_scale,) = [sc for sc in vega["scales"] if sc["name"] == "size"]
    return size_scale["range"]


def test_zero_rule_does_not_inherit_the_bubble_size_channel() -> None:
    """The zero rule shares the top-level encoding and accepts ``size`` as
    stroke width; inherited, it narrows the bubble scale to [1, 4]. Pinned
    against the same chart with the rule suppressed (grid hidden)."""
    data = [{"x": 1, "y": 6000.0, "s": 40}, {"x": 2, "y": 33000.0, "s": 900}]
    spec = _bubble_spec(data)
    (rule,) = _rule_layers(spec)

    reset_config()
    no_rule_chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "scatter",
            "x": "x",
            "y": "y",
            "size": "s",
            "style": {"axis_y": {"grid": {"visible": False}}},
        }
    )
    no_rule_spec = generate_vega_lite_spec(
        no_rule_chart,
        data,
        width=400,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    assert _rule_layers(no_rule_spec) == []
    assert _vega_size_range(spec) == _vega_size_range(no_rule_spec)
    assert rule["encoding"]["size"] is None
