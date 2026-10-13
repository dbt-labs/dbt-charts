"""Independent quantitative-x threshold rule (x=0), generalizing the zero-
baseline rule beyond the measure axis.

A scatter's x can be quantitative and straddle zero on its own — independent
of whatever the y axis carries. Previously, a scatter straddling zero on both
axes got a rule at y=0 and nothing at x=0. See
``BaselineFeature._apply_x_threshold`` in ``core/render/chart/features/baseline.py``.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.style.authored import (
    AxisLabelStylePatch,
    AxisXStylePatch,
    AxisYStylePatch,
    BarChartStylePatch,
    BaseAxisGridStylePatch,
    DimensionLabelStylePatch,
    ScaleContinuousStylePatch,
    ScatterChartStylePatch,
    XScaleStylePatch,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())


def _spec(
    chart_type: str,
    data: list[dict[str, Any]],
    x: str = "x",
    y: str = "y",
    style: ScatterChartStylePatch | None = None,
    chart_format: str | None = None,
) -> dict[str, Any]:
    reset_config()
    payload: dict[str, Any] = {
        "id": "t",
        "type": chart_type,
        "x": x,
        "y": y,
        "style": style,
    }
    if chart_format is not None:
        assert style is None
        payload["style"] = {"axis_y": {"labels": {"format": chart_format}}}
    chart = TypeAdapter(Chart).validate_python(payload)
    return generate_vega_lite_spec(
        chart, data, width=400, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )


def _rule_layers(spec: dict[str, Any]) -> list[dict[str, Any]]:
    layers = spec.get("layer", [])
    return [lay for lay in layers if lay.get("mark", {}).get("type") == "rule"]


def _x_rules(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        lay for lay in _rule_layers(spec) if "datum" in lay["encoding"].get("x", {})
    ]


def _y_rules(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        lay for lay in _rule_layers(spec) if "datum" in lay["encoding"].get("y", {})
    ]


def _x_rules_at(spec: dict[str, Any], datum: float) -> list[dict[str, Any]]:
    return [lay for lay in _x_rules(spec) if lay["encoding"]["x"]["datum"] == datum]


# Straddles zero on both x and y.
_BOTH_STRADDLE_DATA = [
    {"x": -40, "y": -30},
    {"x": -10, "y": 15},
    {"x": 20, "y": 40},
]


def test_scatter_straddling_zero_on_both_axes_emits_both_rules() -> None:
    """The exact before/after case this covers: a scatter straddling
    zero on x and y used to draw only y=0. Both are quantitative and both
    straddle zero, so both get their own threshold rule."""
    spec = _spec("scatter", _BOTH_STRADDLE_DATA)
    x_rules = _x_rules(spec)
    y_rules = _y_rules(spec)
    assert len(x_rules) == 1
    assert len(y_rules) == 1
    assert x_rules[0]["encoding"]["x"]["datum"] == 0
    assert y_rules[0]["encoding"]["y"]["datum"] == 0


def test_scatter_x_far_from_zero_skips_x_rule() -> None:
    """x quantitative but never near zero -> no x rule (y still gets one if
    it straddles)."""
    data = [{"x": 100, "y": -5}, {"x": 150, "y": 5}, {"x": 200, "y": 10}]
    spec = _spec("scatter", data)
    assert _x_rules(spec) == []


def test_scatter_categorical_x_never_gets_x_rule() -> None:
    """A categorical x that happens to hold numeric-looking strings
    ("-1"/"0"/"1", an ordinary warehouse shape ``coerce_numeric_cell``
    coerces) still resolves axis_x.is_quantitative=False -- channel
    classification is strict, a Python str is never quantitative regardless
    of content. This is the case that actually exercises the guard: without
    it, numeric_column_values coerces these same strings back to floats and
    _domain_reaches reports True (the values straddle zero), painting a
    datum:0 rule on a nominal/band x axis, where it has no defined position.
    Plain non-numeric strings ("a"/"b") would never reach that far --
    numeric_column_values returns [] for them regardless of the guard."""
    data = [{"x": "-1", "y": -5}, {"x": "0", "y": 5}, {"x": "1", "y": 10}]
    spec = _spec("scatter", data)
    assert _x_rules(spec) == []


def test_log_scale_x_skips_x_rule() -> None:
    """A log-typed x scale can never carry the datum:0 rule -- same
    incompatibility as the measure-axis guard.

    The x=0 row is load-bearing: it is what makes _domain_reaches report
    True once the log guard is bypassed, so this data actually exercises the
    guard. An authored log domain can't reach 0 -- that is rejected at
    compile time."""
    style = ScatterChartStylePatch(
        axis_x=AxisXStylePatch(
            scale=XScaleStylePatch(continuous=ScaleContinuousStylePatch(type="log"))
        ),
    )
    data = [{"x": 0, "y": -5}, {"x": 10, "y": 5}, {"x": 50, "y": 10}]
    spec = _spec("scatter", data, style=style)
    assert _x_rules(spec) == []


def test_authored_x_domain_excluding_zero_skips_x_rule() -> None:
    """An authored x-domain that excludes 0 suppresses the x rule -- there is
    no legal position for the datum on that domain."""
    style = ScatterChartStylePatch(
        axis_x=AxisXStylePatch(
            scale=XScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[10, 100])
            )
        ),
    )
    spec = _spec("scatter", _BOTH_STRADDLE_DATA, style=style)
    assert _x_rules(spec) == []
    # y is unaffected -- still straddles and gets its own rule.
    assert len(_y_rules(spec)) == 1


def test_descending_x_domain_excluding_zero_skips_x_rule() -> None:
    """Read both ends of the domain -- a descending authored x-domain that
    excludes 0 must suppress the rule the same way an ascending one does."""
    style = ScatterChartStylePatch(
        axis_x=AxisXStylePatch(
            scale=XScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[100, 10])
            )
        ),
    )
    spec = _spec("scatter", _BOTH_STRADDLE_DATA, style=style)
    assert _x_rules(spec) == []


def test_authored_x_domain_including_zero_still_fires() -> None:
    style = ScatterChartStylePatch(
        axis_x=AxisXStylePatch(
            scale=XScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[-100, 100])
            )
        ),
    )
    data = [{"x": 5, "y": -5}, {"x": 50, "y": 5}, {"x": 90, "y": 10}]
    spec = _spec("scatter", data, style=style)
    assert len(_x_rules(spec)) == 1


def test_grid_threshold_visible_false_on_axis_x_skips_only_x_rule() -> None:
    """axis_x.grid.threshold.visible=false is the granular off-switch -- it
    suppresses just the x rule, leaving the y rule (and axis_x's regular
    gridlines) alone."""
    style = ScatterChartStylePatch(
        axis_x=AxisXStylePatch(
            grid=BaseAxisGridStylePatch(threshold={"visible": False}),
        ),
    )
    spec = _spec("scatter", _BOTH_STRADDLE_DATA, style=style)
    assert _x_rules(spec) == []
    assert len(_y_rules(spec)) == 1


def test_grid_not_visible_on_axis_x_suppresses_x_rule() -> None:
    """axis_x.grid.visible=false suppresses the independent x rule.

    A theme that hides an axis's gridlines outright leaves that direction with
    no structure at all, and a lone heavy rule in that emptiness reads as a
    stray mark rather than a threshold. Whether a chart in that state should
    nonetheless get the rule is a real question, deliberately left open.
    Until it is answered, the blanket switch wins, matching the measure
    axis."""
    style = ScatterChartStylePatch(
        axis_x=AxisXStylePatch(grid=BaseAxisGridStylePatch(visible=False)),
    )
    spec = _spec("scatter", _BOTH_STRADDLE_DATA, style=style)
    assert _x_rules(spec) == []


def test_axis_x_threshold_color_override_reaches_x_rule() -> None:
    """A chart-local axis_x.grid.threshold color patch must style the x rule
    -- pins that the x rule reads axis_x's own baked style, not axis_y's."""
    style = ScatterChartStylePatch(
        axis_x=AxisXStylePatch(
            grid=BaseAxisGridStylePatch(threshold={"color": "#ff00ff"}),
        ),
    )
    spec = _spec("scatter", _BOTH_STRADDLE_DATA, style=style)
    x_rules = _x_rules(spec)
    assert len(x_rules) == 1
    assert x_rules[0]["mark"]["color"] == "#ff00ff"


def test_vertical_bar_with_quantitative_x_has_no_x_rule_under_default_theme() -> None:
    """Every shipped theme sets ``charts.bar.axis_x.grid.visible: false``, so a
    bar gets no independent x rule by default even when its x is quantitative
    and straddles zero.

    Pinned as the deliberate consequence of gating on the blanket switch, not
    as a desired end state: making the rule fire here is a family-wide visible
    change under every theme and belongs in its own change, not smuggled in as
    a side effect of this one. An author who wants it can still show axis_x's
    grid."""
    data = [{"x": -10, "y": 5}, {"x": 0, "y": 8}, {"x": 20, "y": 3}]
    spec = _spec("bar", data)
    assert _x_rules(spec) == []


def test_line_with_quantitative_x_gets_x_rule() -> None:
    """A line chart's x is usually a category/temporal field, but the
    independent x rule fires just the same when x is quantitative and
    straddles zero -- coverage for a family besides scatter/bar."""
    data = [{"x": -10, "y": 5}, {"x": 0, "y": 8}, {"x": 20, "y": 3}]
    spec = _spec("line", data)
    assert len(_x_rules(spec)) == 1


def test_area_with_quantitative_x_gets_x_rule() -> None:
    """Same independent x rule on an area chart with a quantitative x."""
    data = [{"x": -10, "y": 5}, {"x": 0, "y": 8}, {"x": 20, "y": 3}]
    spec = _spec("area", data)
    assert len(_x_rules(spec)) == 1


def test_horizontal_bar_x_measure_axis_gets_no_duplicate_x_rule() -> None:
    """Horizontal bar's x IS the measure axis, already covered by the
    measure-axis zero rule -- the independent quantitative-x pass must not
    also fire, which would duplicate the rule."""
    style = BarChartStylePatch(
        orientation="horizontal",
        # This shown grid and the numeric `bucket` values below are
        # load-bearing, not decoration: they keep this test from passing
        # trivially. Delete the orientation exclusion and a string dimension or
        # a theme-hidden axis_x grid would still return before this pass emits
        # its own duplicate rule, so the assertion would hold either way. Both
        # cleared, the exclusion is the only thing between this chart and a
        # second rule layer identical to the measure-axis one.
        axis_x=AxisXStylePatch(grid=BaseAxisGridStylePatch(visible=True)),
    )
    payload: dict[str, Any] = {
        "id": "t",
        "type": "bar",
        "x": "bucket",
        "y": "value",
        "style": style,
    }
    reset_config()
    chart = TypeAdapter(Chart).validate_python(payload)
    data = [
        {"bucket": -1.0, "value": 5},
        {"bucket": 0.5, "value": -5},
        {"bucket": 1.5, "value": 8},
    ]
    spec = generate_vega_lite_spec(
        chart, data, width=400, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )
    assert len(_rule_layers(spec)) == 1


# ── Independent quantitative-x UNITY (x=1) ──────────────────────────────────
# Mirrors the zero-rule coverage above: a percent-formatted quantitative x
# axis whose domain reaches 1.0 earns its own unity rule, independent of
# whatever the y axis carries. See BaselineFeature._apply_x_threshold.

_PERCENT_X_REACHING_ONE_DATA = [
    {"x": 0.5, "y": 10},
    {"x": 0.8, "y": 20},
    {"x": 1.05, "y": 30},
]

_PERCENT_X_BELOW_ONE_DATA = [
    {"x": 0.1, "y": 10},
    {"x": 0.3, "y": 20},
    {"x": 0.5, "y": 30},
]


def _percent_axis_x(**overrides: Any) -> AxisXStylePatch:
    return AxisXStylePatch(labels=DimensionLabelStylePatch(format=".0%"), **overrides)


def test_percent_format_x_reaching_one_emits_x_unity_rule() -> None:
    style = ScatterChartStylePatch(axis_x=_percent_axis_x())
    spec = _spec("scatter", _PERCENT_X_REACHING_ONE_DATA, style=style)
    x_unity = _x_rules_at(spec, 1)
    assert len(x_unity) == 1
    assert x_unity[0]["encoding"]["x"]["datum"] == 1


def test_percent_format_x_not_reaching_one_skips_x_unity_rule() -> None:
    style = ScatterChartStylePatch(axis_x=_percent_axis_x())
    spec = _spec("scatter", _PERCENT_X_BELOW_ONE_DATA, style=style)
    assert _x_rules_at(spec, 1) == []


def test_non_percent_x_reaching_one_skips_x_unity_rule() -> None:
    """x is quantitative and reaches 1.0, but nothing marks it percent --
    the unity rule is a percent-axis contract, not a bare-value-1 contract."""
    spec = _spec("scatter", _PERCENT_X_REACHING_ONE_DATA)
    assert _x_rules_at(spec, 1) == []


def test_categorical_x_with_percent_axis_format_never_gets_x_unity_rule() -> None:
    """axis_x.is_quantitative=False suppresses the unity rule even when
    axis_x's OWN label format is percent-shaped -- the gate reads the
    resolved channel type first, matching the zero-rule categorical guard.

    x holds numeric-looking strings ("0.5"/"0.8"/"1.05") so this actually
    exercises the guard: channel classification is strict (a Python str is
    never quantitative regardless of content), so axis_x.is_quantitative
    resolves False, but numeric_column_values coerces the same strings back
    to floats reaching 1.0 and _resolved_percent_format reads True off
    axis_x's own label format. Without the guard both later checks pass and
    a datum:1 rule paints on a nominal/band x axis, where it has no defined
    position. Plain non-numeric strings ("a"/"b") never reach that far --
    numeric_column_values returns [] for them regardless of the guard.

    (``gate_x_label_format`` -- the validator that rejects a percent label
    format on a categorical x -- only runs for bar/heatmap emitters, never
    scatter, so authoring axis_x's percent label format directly here is
    legal; that is what lets this test reach the gate the chart-level
    ``number_format`` spelling (see
    test_chart_level_percent_format_does_not_fire_x_unity_rule) cannot --
    _apply_x_threshold never reads chart-level format for the x=1 gate.)"""
    style = ScatterChartStylePatch(axis_x=_percent_axis_x())
    data = [{"x": "0.5", "y": 0.5}, {"x": "0.8", "y": 0.8}, {"x": "1.05", "y": 1.05}]
    spec = _spec("scatter", data, style=style)
    assert _x_rules_at(spec, 1) == []


def test_log_scale_percent_x_skips_x_unity_rule() -> None:
    """A log-typed x scale can never carry the datum:1 rule -- same
    incompatibility as the x-zero guard."""
    style = ScatterChartStylePatch(
        axis_x=AxisXStylePatch(
            labels=DimensionLabelStylePatch(format=".0%"),
            scale=XScaleStylePatch(continuous=ScaleContinuousStylePatch(type="log")),
        ),
    )
    spec = _spec("scatter", _PERCENT_X_REACHING_ONE_DATA, style=style)
    assert _x_rules_at(spec, 1) == []


def test_authored_x_domain_excluding_one_skips_x_unity_rule() -> None:
    style = ScatterChartStylePatch(
        axis_x=AxisXStylePatch(
            labels=DimensionLabelStylePatch(format=".0%"),
            scale=XScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[0, 0.9])
            ),
        ),
    )
    spec = _spec("scatter", _PERCENT_X_REACHING_ONE_DATA, style=style)
    assert _x_rules_at(spec, 1) == []


def test_descending_x_domain_excluding_one_skips_x_unity_rule() -> None:
    """Read both ends of the domain -- a descending authored x-domain that
    excludes 1.0 must suppress the rule the same way an ascending one does."""
    style = ScatterChartStylePatch(
        axis_x=AxisXStylePatch(
            labels=DimensionLabelStylePatch(format=".0%"),
            scale=XScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[0.9, 0.5])
            ),
        ),
    )
    spec = _spec("scatter", _PERCENT_X_REACHING_ONE_DATA, style=style)
    assert _x_rules_at(spec, 1) == []


def test_grid_threshold_visible_false_on_axis_x_skips_x_unity_rule() -> None:
    style = ScatterChartStylePatch(
        axis_x=AxisXStylePatch(
            labels=DimensionLabelStylePatch(format=".0%"),
            grid=BaseAxisGridStylePatch(threshold={"visible": False}),
        ),
    )
    spec = _spec("scatter", _PERCENT_X_REACHING_ONE_DATA, style=style)
    assert _x_rules_at(spec, 1) == []


def test_layered_scatter_with_percent_x_still_emits_x_unity_rule() -> None:
    """The spec-reading trap: a scatter with authored ``layers:`` has its
    encoding hoisted into sub-layers by render_cartesian_overlay, so a gate
    reading spec.encoding for channel type would misread the outer spec.
    The gate must read chart.style.axis_x.is_quantitative (resolved), not the
    spec."""
    payload: dict[str, Any] = {
        "id": "t",
        "type": "scatter",
        "x": "x",
        "y": "y",
        "layers": [{"type": "line", "y": "y", "label": "target"}],
        "style": ScatterChartStylePatch(axis_x=_percent_axis_x()),
    }
    reset_config()
    chart = TypeAdapter(Chart).validate_python(payload)
    spec = generate_vega_lite_spec(
        chart,
        _PERCENT_X_REACHING_ONE_DATA,
        width=400,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    assert len(_x_rules_at(spec, 1)) == 1


def test_horizontal_bar_percent_x_gets_no_duplicate_x_unity_rule() -> None:
    """Horizontal bar's x IS the measure axis, already covered by
    _apply_unity -- the independent quantitative-x unity pass must not also
    fire even when the bar's own dimension column happens to classify
    quantitative, or it would duplicate the rule at x=1.

    The measure's percent format is authored on axis_y (the cascade slot
    that always carries the measure, regardless of which VL channel it
    renders on) -- axis_x is the categorical slot on a horizontal bar and
    a percent format there says nothing about the measure.
    """
    style = BarChartStylePatch(
        orientation="horizontal",
        axis_y=AxisYStylePatch(labels=AxisLabelStylePatch(format=".0%")),
        # axis_x carries its own percent format and a shown grid for the same
        # reason as the zero test above: without them the assertion below holds
        # whether or not the orientation exclusion exists, because the ablated
        # code would return at the percent or grid gate instead.
        axis_x=AxisXStylePatch(
            grid=BaseAxisGridStylePatch(visible=True),
            labels=DimensionLabelStylePatch(format=".0%"),
        ),
    )
    payload: dict[str, Any] = {
        "id": "t",
        "type": "bar",
        "x": "bucket",
        "y": "value",
        "style": style,
    }
    reset_config()
    chart = TypeAdapter(Chart).validate_python(payload)
    data = [
        {"bucket": 0.5, "value": 0.5},
        {"bucket": 0.9, "value": 0.9},
        {"bucket": 1.05, "value": 1.05},
    ]
    spec = generate_vega_lite_spec(
        chart, data, width=400, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )
    assert len(_x_rules_at(spec, 1)) == 1


# A chart-level ``format`` is the MEASURE's format — it is the authoring fallback
# that feeds axis_y, not a statement about an unrelated quantitative x. Reading it
# to decide "is x a ratio axis" paints a 100% threshold on an axis that has no
# percent semantics: below, y is an ordinary count (40..70) the author formatted as
# a percentage, x is a plain SI-labeled quantitative axis, and the spurious x=1
# rule was the ONLY rule on the chart — one bold line meaning nothing.
_COUNT_Y_QUANT_X_REACHING_ONE = [
    {"x": 0.2, "y": 40},
    {"x": 0.6, "y": 55},
    {"x": 1.0, "y": 70},
]


def test_chart_level_percent_format_does_not_fire_x_unity_rule() -> None:
    """A measure-level percent format must not make x a ratio axis."""
    spec = _spec("line", _COUNT_Y_QUANT_X_REACHING_ONE, chart_format=".0%")
    assert _x_rules_at(spec, 1) == []


def test_axis_x_percent_format_still_fires_x_unity_rule() -> None:
    """The x axis's OWN percent format is what earns the rule (positive control)."""
    spec = _spec(
        "scatter",
        _COUNT_Y_QUANT_X_REACHING_ONE,
        style=ScatterChartStylePatch(
            axis_x=AxisXStylePatch(labels=DimensionLabelStylePatch(format=".0%"))
        ),
    )
    assert len(_x_rules_at(spec, 1)) == 1


def test_scatter_rules_render_beneath_the_points() -> None:
    """Scatter: every threshold rule sits BELOW the point mark.

    Mirrors the line convention (``test_zero_rule_is_first_layer_for_line``):
    a reference line must not interrupt the data it is a reference for. Scatter
    previously fell into ``_insert_rule``'s bar branch — appended last, drawn on
    top — which is right for bar (its fills would hide a rule underneath) but
    bisects a scatter's points, including the very point sitting on the
    threshold.
    """
    spec = _spec("scatter", _BOTH_STRADDLE_DATA)
    layers = (spec.get("hconcat") or [spec])[0].get("layer", [])
    assert layers
    point_idx = next(
        i
        for i, layer in enumerate(layers)
        if (layer.get("mark") or {}).get("type") == "point"
    )
    rule_idx = [
        i
        for i, layer in enumerate(layers)
        if (layer.get("mark") or {}).get("type") == "rule"
    ]
    assert rule_idx, "expected threshold rules on a scatter straddling zero"
    assert max(rule_idx) < point_idx, (
        f"threshold rules must render beneath the points; "
        f"rules at {rule_idx}, point mark at {point_idx}"
    )


def test_layered_scatter_rules_render_beneath_the_points() -> None:
    """Same ordering guarantee, on the layered dispatch path.

    An authored ``layers:`` entry routes a chart through
    ``translate.py``'s ``_translate_layered``, a distinct composition from
    the non-layered ``_translate_standard`` that
    ``test_scatter_rules_render_beneath_the_points`` covers above --
    ``_translate_standard`` builds ``[*under, main, *rule]`` directly, while
    ``_translate_layered`` builds ``vl["layer"]`` from
    ``(*spec.underlays, *spec.layers)``. Only presence of the rule was
    previously asserted for this path; reverting that composition back to
    ``(*spec.layers, *spec.underlays)`` would put the rules on top of the
    points here while every other test (including the non-layered one
    above) stayed green.
    """
    reset_config()
    payload: dict[str, Any] = {
        "id": "t",
        "type": "scatter",
        "x": "x",
        "y": "y",
        "layers": [{"type": "scatter", "x": "x", "y": "y", "label": "overlay"}],
    }
    chart = TypeAdapter(Chart).validate_python(payload)
    spec = generate_vega_lite_spec(
        chart,
        _BOTH_STRADDLE_DATA,
        width=400,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    layers = spec.get("layer", [])
    assert layers, "expected a layered VL spec for an authored `layers:` scatter"
    point_idx = [
        i
        for i, layer in enumerate(layers)
        if (layer.get("mark") or {}).get("type") == "point"
    ]
    rule_idx = [
        i
        for i, layer in enumerate(layers)
        if (layer.get("mark") or {}).get("type") == "rule"
    ]
    assert len(point_idx) >= 2, "expected the base series' point plus the overlay's"
    assert rule_idx, "expected threshold rules on a scatter straddling zero"
    assert max(rule_idx) < min(point_idx), (
        f"threshold rules must render beneath every point layer; "
        f"rules at {rule_idx}, points at {point_idx}"
    )


# Every other percent-x fixture sits entirely above zero, so the datum=0 pass and
# the datum=1 pass never fire on the same chart. Now that one body serves both,
# nothing else watches them coexist: a future edit coupling the two calls would
# pass the whole suite.
_PERCENT_X_SPANNING_ZERO_AND_ONE = [
    {"x": -0.2, "y": 5},
    {"x": 0.4, "y": 8},
    {"x": 1.05, "y": 3},
]


def test_percent_x_spanning_zero_and_one_emits_both_x_rules() -> None:
    """A percent x axis crossing 0 and reaching 1.0 draws a rule at each."""
    style = ScatterChartStylePatch(
        axis_x=AxisXStylePatch(
            grid=BaseAxisGridStylePatch(visible=True),
            labels=DimensionLabelStylePatch(format=".0%"),
        )
    )
    spec = _spec("scatter", _PERCENT_X_SPANNING_ZERO_AND_ONE, style=style)
    assert len(_x_rules_at(spec, 0)) == 1
    assert len(_x_rules_at(spec, 1)) == 1
