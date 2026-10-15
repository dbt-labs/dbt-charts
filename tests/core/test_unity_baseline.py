"""Unity-baseline rule mark for ratio-percent line and area charts."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AreaChartStylePatch,
    AxisLabelStylePatch,
    AxisYStylePatch,
    BarChartStylePatch,
    BaseAxisGridStylePatch,
    BaseScaleStylePatch,
    LineChartStylePatch,
    ScaleContinuousStylePatch,
    ScatterChartStylePatch,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_STYLE = resolve_style(get_theme_style())
_CHART_CTX = resolve_chart_style_context(get_theme_style())
_CHART_ADAPTER: TypeAdapter[Chart] = TypeAdapter(Chart)


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_config()
    yield
    reset_config()


def _spec(
    chart_type: str,
    data: list[dict],
    style: BarChartStylePatch
    | LineChartStylePatch
    | AreaChartStylePatch
    | ScatterChartStylePatch
    | None = None,
    color: str | None = None,
    stack: str | bool | None = None,
) -> dict:
    fields: dict = {
        "id": "t",
        "type": chart_type,
        "x": "x",
        "y": "y",
        "query": SqlQuery(sql="SELECT 1", source="src"),
        "query_name": "q",
    }
    if color is not None:
        fields["color"] = color
    if stack is not None:
        fields["stack"] = stack
    if style is not None:
        fields["style"] = style
    chart = _CHART_ADAPTER.validate_python(fields)
    return generate_vega_lite_spec(
        chart,
        data,
        width=400,
        board_style=_BOARD_STYLE,
        chart_style_context=_CHART_CTX,
    )


def _main_pane(spec: dict) -> dict:
    return spec["hconcat"][0] if "hconcat" in spec else spec


def _rule_layers(spec: dict, datum: float | int) -> list[dict]:
    rules = []
    for layer in _main_pane(spec).get("layer", []):
        mark = layer.get("mark", {})
        if not (isinstance(mark, dict) and mark.get("type") == "rule"):
            continue
        enc = layer.get("encoding", {})
        if enc.get("y", {}).get("datum") == datum:
            rules.append(layer)
    return rules


def _zero_rule_layers(spec: dict) -> list[dict]:
    return _rule_layers(spec, 0)


def _unity_rule_layers(spec: dict) -> list[dict]:
    return _rule_layers(spec, 1)


_RATIO_DATA = [
    {"x": "Jan", "y": 1.05},
    {"x": "Feb", "y": 1.10},
    {"x": "Mar", "y": 1.15},
]


def test_ratio_percent_line_domain_including_one_emits_unity_rule_without_zero() -> (
    None
):
    style = LineChartStylePatch(
        axis_y=AxisYStylePatch(
            labels=AxisLabelStylePatch(format="percent_whole"),
            scale=BaseScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[0.95, 1.2])
            ),
        ),
    )

    spec = _spec("line", _RATIO_DATA, style=style)

    assert len(_unity_rule_layers(spec)) == 1
    assert _zero_rule_layers(spec) == []


def test_ratio_percent_line_data_below_one_skips_unity_rule() -> None:
    """A percent-format line whose data stays well below 1.0 (win rates ~0-30%)
    must not get a spurious y=1 unity rule — that rule's synthetic datum:1 would
    drag the y-domain up to 100% and squish the real series into the floor.

    No explicit domain is set, so the gate must fall back to the data range
    (mirrors V1 ``_domain_includes_value``), which does not straddle 1.0.
    """
    data = [
        {"x": "Jan", "y": 0.05},
        {"x": "Feb", "y": 0.18},
        {"x": "Mar", "y": 0.30},
    ]
    style = LineChartStylePatch(axis_y={"labels": {"format": "percent_whole"}})

    spec = _spec("line", data, style=style, color="x")

    assert _unity_rule_layers(spec) == []
    # The synthetic datum:1 row must not have been injected into any layer's data.
    for layer in _main_pane(spec).get("layer", []):
        for row in layer.get("data", {}).get("values", []):
            assert row.get("y") != 1


def test_ratio_percent_line_domain_excluding_one_skips_unity_rule() -> None:
    style = LineChartStylePatch(
        axis_y=AxisYStylePatch(
            labels=AxisLabelStylePatch(format="percent_whole"),
            scale=BaseScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[1.02, 1.2])
            ),
        ),
    )

    spec = _spec("line", _RATIO_DATA, style=style)

    assert _unity_rule_layers(spec) == []


def test_percent_number_line_does_not_emit_ratio_unity_rule() -> None:
    data = [
        {"x": "Jan", "y": 105},
        {"x": "Feb", "y": 110},
        {"x": "Mar", "y": 115},
    ]
    style = LineChartStylePatch(
        axis_y=AxisYStylePatch(
            labels=AxisLabelStylePatch(format="percent_number"),
            scale=BaseScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[95, 120])
            ),
        ),
    )

    spec = _spec("line", data, style=style)

    assert _unity_rule_layers(spec) == []


def test_normalize_ratio_percent_bar_dedupes_top_and_unity_rule() -> None:
    style = BarChartStylePatch(
        orientation="vertical",
        axis_y=AxisYStylePatch(
            labels=AxisLabelStylePatch(format="percent_whole"),
            scale=BaseScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[0, 1])
            ),
        ),
    )

    spec = _spec("bar", _RATIO_DATA, style=style, color="x", stack="normalize")

    assert len(_unity_rule_layers(spec)) == 1


def test_normalize_area_grid_threshold_visible_false_skips_top_rules() -> None:
    """axis_y.grid.threshold.visible=false is the granular off-switch for
    ``_insert_top_rules`` (the normalize-stack 0/1 pair), distinct from the
    blanket grid.visible gate."""
    style = AreaChartStylePatch(
        axis_y=AxisYStylePatch(
            grid=BaseAxisGridStylePatch(threshold={"visible": False}),
        ),
    )

    spec = _spec("area", _RATIO_DATA, style=style, color="x", stack="normalize")

    assert _zero_rule_layers(spec) == []
    assert _unity_rule_layers(spec) == []


def test_normalize_area_no_percent_format_emits_zero_and_top_rules() -> None:
    """Correct end state pin: a normalize-stacked area with no percent format
    keeps its long-standing 0 and 1 top-rule pair — this branch must not
    disturb that path, only the percent-format carve-out below."""
    style = AreaChartStylePatch()

    spec = _spec("area", _RATIO_DATA, style=style, color="x", stack="normalize")

    assert len(_zero_rule_layers(spec)) == 1
    assert len(_unity_rule_layers(spec)) == 1


def test_normalize_area_percent_format_keeps_zero_baseline_dedupes_unity() -> None:
    """Regression pin: a percent-formatted normalize area must keep its 0%
    baseline — the percent carve-out's early ``return`` dropped it along with
    the duplicate 1 it was meant to dedupe — while still emitting exactly one
    100% rule (from ``_apply_unity``), never the old ``[0, 1, 1]`` triple."""
    style = AreaChartStylePatch(axis_y={"labels": {"format": "percent_whole"}})

    spec = _spec("area", _RATIO_DATA, style=style, color="x", stack="normalize")

    assert len(_zero_rule_layers(spec)) == 1
    assert len(_unity_rule_layers(spec)) == 1


def test_non_normalize_ratio_percent_bar_domain_including_one_emits_unity_rule() -> (
    None
):
    """Bar is no longer excluded from the unity gate: a non-normalize percent
    bar whose domain reaches 1.0 gets the same unity rule line/area do."""
    style = BarChartStylePatch(
        orientation="vertical",
        axis_y=AxisYStylePatch(
            labels=AxisLabelStylePatch(format="percent_whole"),
            scale=BaseScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[0, 1.2])
            ),
        ),
    )

    spec = _spec("bar", _RATIO_DATA, style=style)

    assert len(_unity_rule_layers(spec)) == 1


def test_non_normalize_ratio_percent_bar_domain_excluding_one_skips_unity_rule() -> (
    None
):
    style = BarChartStylePatch(
        orientation="vertical",
        axis_y=AxisYStylePatch(
            labels=AxisLabelStylePatch(format="percent_whole"),
            scale=BaseScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[1.02, 1.2])
            ),
        ),
    )

    spec = _spec("bar", _RATIO_DATA, style=style)

    assert _unity_rule_layers(spec) == []


def test_unity_rule_is_first_layer_for_line() -> None:
    style = LineChartStylePatch(
        axis_y=AxisYStylePatch(
            labels=AxisLabelStylePatch(format="percent_whole"),
            scale=BaseScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[0.95, 1.2])
            ),
        ),
    )

    spec = _spec("line", _RATIO_DATA, style=style)

    layers = _main_pane(spec).get("layer", [])
    rule = _unity_rule_layers(spec)[0]
    assert layers[0] is rule


def test_grid_not_visible_skips_unity_rule() -> None:
    style = LineChartStylePatch(
        axis_y=AxisYStylePatch(
            labels=AxisLabelStylePatch(format="percent_whole"),
            grid=BaseAxisGridStylePatch(visible=False),
            scale=BaseScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[0.95, 1.2])
            ),
        ),
    )

    spec = _spec("line", _RATIO_DATA, style=style)

    assert _unity_rule_layers(spec) == []


def test_grid_threshold_visible_false_skips_unity_rule() -> None:
    """axis_y.grid.threshold.visible=false is the granular off-switch for
    ``_apply_unity``, distinct from the blanket grid.visible gate above."""
    style = LineChartStylePatch(
        axis_y=AxisYStylePatch(
            labels=AxisLabelStylePatch(format="percent_whole"),
            grid=BaseAxisGridStylePatch(threshold={"visible": False}),
            scale=BaseScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[0.95, 1.2])
            ),
        ),
    )

    spec = _spec("line", _RATIO_DATA, style=style)

    assert _unity_rule_layers(spec) == []


def test_straddling_zero_and_one_emits_both_rules() -> None:
    data = [
        {"x": "Jan", "y": -0.1},
        {"x": "Feb", "y": 0.5},
        {"x": "Mar", "y": 1.1},
    ]
    style = LineChartStylePatch(
        axis_y=AxisYStylePatch(
            labels=AxisLabelStylePatch(format="percent_whole"),
            scale=BaseScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[-0.2, 1.2])
            ),
        ),
    )

    spec = _spec("line", data, style=style)

    assert len(_zero_rule_layers(spec)) == 1
    assert len(_unity_rule_layers(spec)) == 1


def test_ratio_percent_area_domain_including_one_emits_unity_rule() -> None:
    style = AreaChartStylePatch(
        axis_y=AxisYStylePatch(
            labels=AxisLabelStylePatch(format="percent_whole"),
            scale=BaseScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[0.95, 1.2])
            ),
        ),
    )

    spec = _spec("area", _RATIO_DATA, style=style)

    assert len(_unity_rule_layers(spec)) == 1


def test_unity_rule_renders_as_one_visible_horizontal_line() -> None:
    import re

    import vl_convert as vlc

    style = LineChartStylePatch(
        axis_y=AxisYStylePatch(
            labels=AxisLabelStylePatch(format="percent_whole"),
            scale=BaseScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[0.95, 1.2])
            ),
        ),
    )

    spec = _spec("line", _RATIO_DATA, style=style)
    svg = vlc.vegalite_to_svg(spec)
    rule_lines = [
        line
        for group in re.findall(
            r'<g [^>]*class="mark-rule role-mark[^>]*>.*?</g>', svg, re.S
        )
        for line in re.findall(r"<line[^>]*>", group)
    ]

    assert len(rule_lines) == 1


# NRR-shaped: every point above 1.0, span 0.089. Headroom decides whether the
# rendered domain reaches down across 1.0 — the raw data range never does.
_NRR_DATA = [
    {"x": "Jan", "y": 1.052},
    {"x": "Feb", "y": 1.09},
    {"x": "Mar", "y": 1.141},
]


def test_headroom_pulling_domain_below_one_emits_unity_rule() -> None:
    """Headroom that expands the rendered domain across 1.0 earns the rule.

    domain_min = 1.052 − 0.59 × 0.089 ≈ 0.9995, so the axis paints a 100%
    tick. The gate must agree with the picture: the reader's parity anchor is
    on screen, so it gets the heavy rule rather than a hairline gridline.
    """
    style = LineChartStylePatch(
        axis_y=AxisYStylePatch(
            labels=AxisLabelStylePatch(format="percent_whole"),
            scale=BaseScaleStylePatch(headroom=0.59),
        ),
    )

    spec = _spec("line", _NRR_DATA, style=style)

    assert len(_unity_rule_layers(spec)) == 1


def test_headroom_leaving_one_out_of_view_skips_unity_rule() -> None:
    """Modest headroom keeps 1.0 off screen, so no rule.

    domain_min = 1.052 − 0.1 × 0.089 ≈ 1.043. Same data as the case above —
    only the rendered domain differs, which is exactly what the gate reads.
    """
    style = LineChartStylePatch(
        axis_y=AxisYStylePatch(
            labels=AxisLabelStylePatch(format="percent_whole"),
            scale=BaseScaleStylePatch(headroom=0.1),
        ),
    )

    spec = _spec("line", _NRR_DATA, style=style)

    assert _unity_rule_layers(spec) == []


# Uptime-shaped: approaches 100% and never reaches it, one real dip. The class
# of metric where a 100% reference line earns its place most — availability,
# delivery rate, SLA compliance — and the one the raw data range always misses.
_UPTIME_DATA = [
    {"x": "Sep", "y": 0.9991},
    {"x": "Oct", "y": 0.9987},
    {"x": "Nov", "y": 0.9934},
    {"x": "Dec", "y": 0.9996},
]


def test_zero_headroom_ladder_reaching_one_skips_unity_rule() -> None:
    """A tick ladder rounding out past 1.0 is not the domain reaching 1.0.

    `headroom: 0` pins neither edge, so nothing in the render guarantees 1.0 is
    on screen — the ladder's top rung is a tick position, and Vega-Lite clips a
    rung outside the domain. The gate must not read it. Pinning 1.0 into view
    is what the one-ended/bounded-domain task's `include` field is for.
    """
    style = LineChartStylePatch(
        axis_y=AxisYStylePatch(
            labels=AxisLabelStylePatch(format="percent_whole"),
            scale=BaseScaleStylePatch(headroom=0),
        ),
    )

    spec = _spec("line", _UPTIME_DATA, style=style)

    assert _unity_rule_layers(spec) == []


def _unity_rules_across_headroom(
    data: list[dict], headrooms: tuple[float, ...]
) -> list[int]:
    counts = []
    for headroom in headrooms:
        style = LineChartStylePatch(
            axis_y=AxisYStylePatch(
                labels=AxisLabelStylePatch(format="percent_whole"),
                scale=BaseScaleStylePatch(headroom=headroom),
            ),
        )
        counts.append(len(_unity_rule_layers(_spec("line", data, style=style))))
    return counts


def test_unity_gate_is_monotonic_in_headroom() -> None:
    """More headroom can add the rule; it can never take it away.

    Headroom only ever expands the rendered domain, so the rule count must be
    non-decreasing across a sweep. Reading the tick ladder as a domain broke
    this in the opposite direction — `headroom: 0` reported 1.0 in range while
    `0.05`, strictly more rendered range on the same data, reported it out.

    Both ends have to be live for the sweep to mean anything: on this data the
    rule is genuinely absent at the bottom and genuinely present at the top.
    """
    counts = _unity_rules_across_headroom(_NRR_DATA, (0, 0.1, 0.3, 0.59))

    assert counts[0] == 0
    assert counts[-1] == 1
    assert counts == sorted(counts)


def test_ladder_rounding_past_one_is_not_domain_reach() -> None:
    """A rung above the data is a tick position, not an edge of the domain.

    `headroom: 0` pins neither edge here and the engine clips the ladder's top
    rung, so no amount of ladder-reading makes 1.0 reachable. Firing here also
    stretched the domain via the rule's own `datum`, manufacturing the 100%
    tick that appeared to justify it.
    """
    data = [{"x": "a", "y": 0.80}, {"x": "b", "y": 0.88}, {"x": "c", "y": 0.97}]

    assert _unity_rules_across_headroom(data, (0, 0.05)) == [0, 0]


def test_zero_anchored_percent_area_reaching_one_emits_unity_rule() -> None:
    """A zero-anchored axis starts at 0, so 1.0 is on screen well below the top.

    `_pick_scale` gives this area chart `zero: True` on its own (min/max ratio
    is small enough), and resolve deliberately leaves `domain_min` None on a
    zero-anchored axis — the floor comes from the ladder via `y_zero_scale`.
    Reading the data extent (1.05–5.0) puts 1.0 out of range while the chart
    paints 100% mid-plot.
    """
    data = [
        {"x": "Jan", "y": 1.05},
        {"x": "Feb", "y": 3.20},
        {"x": "Mar", "y": 5.00},
    ]
    style = AreaChartStylePatch(axis_y={"labels": {"format": "percent_whole"}})

    spec = _spec("area", data, style=style)

    assert len(_unity_rule_layers(spec)) == 1


def test_zero_anchored_axis_with_authored_ladder_still_floors_at_zero() -> None:
    """An authored `scale.values` ladder may not source a domain bound — but
    the axis is still anchored at zero, so 1.0 is still on screen.

    This is the one shape where the zero-anchor floor changes the answer. The
    authored ladder is filtered out (it pins tick positions, not the domain),
    so the floor falls back to the same literal `0.0` that `y_zero_scale` pins
    as `domainMin`. Without that fallback the gate would read the data floor of
    1.05 and withhold the rule from a chart drawing 100% mid-plot.
    """
    data = [
        {"x": "Jan", "y": 1.05},
        {"x": "Feb", "y": 3.20},
        {"x": "Mar", "y": 5.00},
    ]
    style = LineChartStylePatch(
        axis_y=AxisYStylePatch(
            labels=AxisLabelStylePatch(format="percent_whole"),
            scale=BaseScaleStylePatch(
                values=[0, 2, 4, 6],
                continuous=ScaleContinuousStylePatch(zero=True),
            ),
        ),
    )

    spec = _spec("line", data, style=style)

    assert len(_unity_rule_layers(spec)) == 1


def test_ratio_percent_scatter_domain_including_one_emits_unity_rule() -> None:
    """Percent-format scatter is a live path: ``_apply_unity``'s isinstance
    tuple widened to ``ResolvedScatterChart`` alongside line and area, and
    must fire under the same domain-reach rule as its line twin above."""
    style = ScatterChartStylePatch(
        axis_y=AxisYStylePatch(
            labels=AxisLabelStylePatch(format="percent_whole"),
            scale=BaseScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[0.95, 1.2])
            ),
        ),
    )

    spec = _spec("scatter", _RATIO_DATA, style=style)

    assert len(_unity_rule_layers(spec)) == 1


def test_ratio_percent_scatter_domain_excluding_one_skips_unity_rule() -> None:
    style = ScatterChartStylePatch(
        axis_y=AxisYStylePatch(
            labels=AxisLabelStylePatch(format="percent_whole"),
            scale=BaseScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[1.02, 1.2])
            ),
        ),
    )

    spec = _spec("scatter", _RATIO_DATA, style=style)

    assert _unity_rule_layers(spec) == []


def test_rotated_dot_plot_scatter_skips_unity_rule_despite_percent_format() -> None:
    """A rotated (dot-plot) scatter puts the category on y and the measure on
    x -- a categorical y carries no measure for the unity rule to reference,
    even with an explicit percent ``axis_y.labels.format`` and an authored
    domain straddling 1.0 (both of which, on their own, would otherwise pass
    the earlier gates and let the rule through).
    ``_y_carries_the_measure`` must gate the unity rule the same way it
    gates the zero rule.

    ``region`` is a numeric-string category (not ``"a"``/``"b"``):
    ``gate_label_format`` now raises a percent format on a categorical y
    whose ticks aren't themselves readable as numbers, and numeric-string
    categories are the one shape both this test's percent format AND that
    gate can agree is legal — d3 formats them cleanly."""
    style = ScatterChartStylePatch(
        axis_y=AxisYStylePatch(
            labels=AxisLabelStylePatch(format=".0%"),
            scale=BaseScaleStylePatch(
                continuous=ScaleContinuousStylePatch(domain=[0.9, 1.2])
            ),
        ),
    )
    fields: dict = {
        "id": "t",
        "type": "scatter",
        "x": "value",
        "y": "region",
        "query": SqlQuery(sql="SELECT 1", source="src"),
        "query_name": "q",
        "style": style,
    }
    chart = _CHART_ADAPTER.validate_python(fields)
    data = [
        {"region": "1", "value": 1.05},
        {"region": "2", "value": 1.20},
    ]

    spec = generate_vega_lite_spec(
        chart,
        data,
        width=400,
        board_style=_BOARD_STYLE,
        chart_style_context=_CHART_CTX,
    )

    assert _unity_rule_layers(spec) == []
