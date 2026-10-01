"""Cross-surface integration tests for the label overlap strategy resolver.

Verifies the wired-up strategy walk produces the expected concrete
labelOverlap and labelAngle in the VL spec for the canary chart shapes:
  - discrete x with short labels → labelOverlap=false (allow), labelAngle=0
  - discrete x with very long labels → labelOverlap=false, labelAngle=-90
    (tilt exhausts the ladder, but categorical labels remain complete)

Continuous temporal x axes (ISO date/datetime) short-circuit like quantitative x
— VL places "nice" ticks, not one per data value. Bucketed-calendar temporal axes
that resolve to "ordinal" step the label cadence instead of tilting — see
test_label_cadence_ladder.py::TestCadenceSteppingResolver for that path.
test_resolve_chart_continuous_temporal_x_skips_strategy_walk covers the
continuous-temporal (short-circuit) case in this file.

Strategy walk is resolved in the V2 emitter (_label_overlap.py); these tests
check the emitted VL spec rather than intermediate resolved state.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_STYLE, _BOARD_CONTEXT = resolve_style_and_context(get_theme_style())


def _x_axis(spec: dict[str, Any]) -> dict[str, Any]:
    """Return the x-encoding axis dict from a VL spec."""
    return spec.get("encoding", {}).get("x", {}).get("axis", {})


def _y_axis(spec: dict[str, Any]) -> dict[str, Any]:
    """Return the y-encoding axis dict from a VL spec."""
    return spec.get("encoding", {}).get("y", {}).get("axis", {})


def test_resolve_chart_short_discrete_x_picks_zero_allow(make_chart):
    """Three short categorical labels on a forced-vertical bar — labelOverlap=false, labelAngle=0.

    VL's adaptive ordinal default rotates labels to vertical when they would
    collide, even with labelOverlap='allow'. The picker stamps labelAngle=0
    explicitly so labels stay flat.

    D-019: nominal x flips bars to horizontal by default, which would route
    to the parity+angle=0 horizontal-bar pin instead of the smart picker.
    Pin orientation="vertical" so this test exercises the picker.
    """
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    chart = make_chart(
        "bar",
        x="region",
        y="value",
        style=BarChartStylePatch.model_validate({"orientation": "vertical"}),
    )
    data = [
        {"region": "East", "value": 1},
        {"region": "West", "value": 2},
        {"region": "South", "value": 3},
    ]
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CONTEXT
    )
    ax = _x_axis(spec)
    assert ax.get("labelOverlap") is False
    assert ax.get("labelAngle") == 0.0


def test_resolve_chart_dense_long_discrete_x_tilts_without_skipping(make_chart):
    """Many long categorical labels tilt to -90 without dropping labels.

    Forced ``orientation: vertical`` so bar-flip doesn't preempt the picker.
    """
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    chart = make_chart(
        "bar",
        x="region",
        y="value",
        style=BarChartStylePatch.model_validate({"orientation": "vertical"}),
    )
    # 200 long labels at default ~600px chart — even -90 (line-height band)
    # cannot fit them all, so picker exhausts the ladder.
    data = [
        {"region": f"Very long region name number {i}", "value": i} for i in range(200)
    ]
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CONTEXT
    )
    ax = _x_axis(spec)
    assert ax.get("labelOverlap") is False
    assert ax.get("labelAngle") == -90


def test_resolve_chart_postcondition_x_overlap_never_smart(make_chart):
    """After rendering, axis_x labelOverlap must be a concrete VL value, not 'smart'."""

    chart = make_chart("bar", x="region", y="value")
    data = [{"region": "East", "value": 1}, {"region": "West", "value": 2}]
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CONTEXT
    )
    # For horizontal bar, categorical axis is y; for vertical, it's x.
    ax = _x_axis(spec)
    ay = _y_axis(spec)
    cat_overlap = ax.get("labelOverlap") or ay.get("labelOverlap")
    assert cat_overlap != "smart", (
        f"labelOverlap must not be 'smart' after emit: {cat_overlap!r}"
    )


def test_resolve_chart_chart_local_angle_short_circuits_picker(make_chart):
    """Chart-authored style.axis_x.labels.angle short-circuits the picker.

    Regression for the cascade gap: build_chart_style_context routes chart-local
    style.axis_x to axis_overrides_x (merged at emit time), so the dispatcher
    cannot see the angle on resolved_charts.axis_x.labels.angle.
    resolve_axis_x_overlap must read the merged axis_x.labels.angle directly —
    otherwise dense labels run the automatic tilt picker, clobbering the
    author's chosen tilt.
    """
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    style = BarChartStylePatch.model_validate(
        {"orientation": "vertical", "axis_x": {"labels": {"angle": -45}}}
    )
    chart = make_chart("bar", x="region", y="value", style=style)
    # 200 long labels at default ~600px would normally exhaust the ladder and
    # land on angle=-90. The authored angle must remain unchanged.
    # Vertical pin ensures bar-flip doesn't preempt the picker path.
    data = [
        {"region": f"Very long region name number {i}", "value": i} for i in range(200)
    ]
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CONTEXT
    )
    ax = _x_axis(spec)
    assert ax.get("labelOverlap") is False


def test_resolve_chart_horizontal_bar_pins_categorical_axis_to_zero_allow(make_chart):
    """Horizontal bars: categorical cascade (axis_x) gets angle=0 + allow.

    On a horizontal bar, axis_x labels render on VL ``encoding["y"]`` where
    tilting is wrong. Never parity-drop category names — renderer grows height
    when bands are tight. End-to-end: ``labelAngle=0`` and ``labelOverlap: false``.
    """
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    chart = make_chart(
        "bar",
        x="region",
        y="value",
        style=BarChartStylePatch.model_validate({"orientation": "horizontal"}),
    )
    data = [{"region": f"Region {i}", "value": i} for i in range(8)]
    # End-to-end: the categorical labels axis on VL emits to encoding["y"].
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CONTEXT
    )
    y_axis = spec["encoding"]["y"].get("axis", {})
    assert y_axis.get("labelAngle") == 0
    assert y_axis.get("labelOverlap") is False


def test_chart_width_narrows_label_overlap_window(make_chart):
    """chart.width feeds chart_width_for_overlap; a very narrow width forces label tilt.

    At default width (~600px), 3 short categorical labels fit flat (angle=0,
    overlap=allow). At width=1, no labels can fit — picker exhausts the ladder
    at -90 without dropping categories. Confirms chart.width actually changes the
    label-tilt heuristic rather than being a no-op.
    """
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    style = BarChartStylePatch.model_validate({"orientation": "vertical"})
    data = [
        {"region": "East", "value": 1},
        {"region": "West", "value": 2},
        {"region": "South", "value": 3},
    ]
    # Default width (~600px): three short labels fit flat.
    chart_default = make_chart("bar", x="region", y="value", style=style)
    spec_default = generate_vega_lite_spec(
        chart_default,
        data,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CONTEXT,
    )
    assert _x_axis(spec_default).get("labelAngle") == 0.0

    # width=1: impossible to fit any labels — picker uses the steepest tilt.
    chart_narrow = make_chart("bar", x="region", y="value", style=style, width=1)
    spec_narrow = generate_vega_lite_spec(
        chart_narrow, data, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CONTEXT
    )
    assert _x_axis(spec_narrow).get("labelAngle") != 0.0


def test_resolve_chart_quantitative_x_skips_strategy_walk(make_chart):
    """Quantitative (numeric) x skips the band-width strategy walk entirely.

    VL places a handful of "nice" ticks on a continuous quantitative scale, not
    one label per distinct data value. Applying the band-width tilt math
    (usable_width / n) to 50 distinct floats at 400px would produce a steep
    -90° tilt for data that VL will actually display at ~7 roomy flat
    ticks. The resolver must short-circuit to allow+angle=0 for numeric x.
    """
    chart = make_chart("line", x="timestamp_sec", y="value")
    # 50 distinct numeric x values at a narrow-ish chart width: without the
    # quantitative guard the resolver would tilt steeply.
    data = [{"timestamp_sec": float(i), "value": i * 2} for i in range(50)]
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CONTEXT
    )
    ax = _x_axis(spec)
    assert ax.get("labelOverlap") is False, "quantitative x must not parity-drop labels"
    assert ax.get("labelAngle") == 0.0, (
        "quantitative x must not tilt labels via the strategy walk"
    )


def test_resolve_chart_horizontal_bar_auto_flipped_pins_to_zero_allow(make_chart):
    """Auto-flipped horizontal bar (orientation=auto + dense labels) also pins.

    The bar-flip heuristic resolves orientation to ``horizontal`` for crowded
    categoricals on a vertical bar. The smart resolver must see the post-flip
    orientation and pin angle=0 + allow on axis_x, otherwise the picker tilts
    what's now on VL y.
    """
    chart = make_chart("bar", x="product", y="revenue")
    data = [
        {"product": f"Very long product name number {i}", "revenue": i}
        for i in range(8)
    ]
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CONTEXT
    )
    # Bar-flip fired → categorical labels (axis_x) flat at angle 0 on VL y.
    y_axis = spec["encoding"]["y"].get("axis", {})
    assert y_axis.get("labelAngle") == 0


def test_resolve_chart_continuous_temporal_x_skips_strategy_walk(make_chart):
    """Continuous temporal (ISO date) x skips the band-width strategy walk entirely.

    VL places a handful of 'nice' ticks on a continuous temporal scale, not one
    label per distinct data value. Applying band-width math (usable_width / n) to
    365 distinct daily ISO dates at 400px produces a steep tilt for data that VL
    renders as ~7 roomy flat ticks. The resolver must short-circuit to
    allow+angle=0 for temporal x, exactly as it does for quantitative x.
    """
    import datetime

    chart = make_chart("line", x="date", y="value")
    # 365 distinct ISO date strings — infer_vega_type_from_data returns "temporal".
    # Without the guard: band = 400 * 0.8 / 365 ≈ 0.88px → steep tilt.
    start = datetime.date(2024, 1, 1)
    data = [
        {"date": (start + datetime.timedelta(days=i)).isoformat(), "value": i}
        for i in range(365)
    ]
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CONTEXT
    )
    ax = _x_axis(spec)
    assert ax.get("labelOverlap") is False, "temporal x must not parity-drop labels"
    assert ax.get("labelAngle") == 0.0, (
        "temporal x must not tilt labels via the strategy walk"
    )


def test_resolve_chart_unrecognized_cadence_omits_label_overlap(make_chart):
    """A temporal x with no recognizable calendar bucket grain must not force
    labelOverlap=false — that would disable VL's own adaptive parity thinning
    with nothing computed to replace it (every distinct date prints unthinned).
    28-day gaps, same weekday: detect_time_unit's weekly gate (median ≤14
    days) rejects this as "not weekly" and returns None.
    """
    import datetime

    chart = make_chart("area", x="date", y="value")
    start = datetime.date(2024, 1, 7)
    data = [
        {"date": (start + datetime.timedelta(days=28 * i)).isoformat(), "value": i}
        for i in range(20)
    ]
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CONTEXT
    )
    ax = _x_axis(spec)
    assert "labelOverlap" not in ax, (
        "unrecognized cadence must omit labelOverlap so VL's own adaptive "
        "default applies, not force it off with no replacement thinning"
    )


def _daily_line_axis(make_chart, days: int) -> dict[str, Any]:
    import datetime

    chart = make_chart("line", x="date", y="value")
    start = datetime.date(2024, 1, 1)
    data = [
        {"date": (start + datetime.timedelta(days=i)).isoformat(), "value": i}
        for i in range(days)
    ]
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CONTEXT
    )
    return _x_axis(spec)


def test_short_daily_axis_keeps_daily_labels_and_ticks(make_chart):
    axis = _daily_line_axis(make_chart, 14)

    assert axis["tickCount"] == {"interval": "day", "step": 1}
    assert "'%-d'" in axis["labelExpr"]
    assert "'%y'" in axis["labelExpr"]


def test_month_long_daily_axis_steps_to_monday_day_numbers(make_chart):
    axis = _daily_line_axis(make_chart, 30)

    assert "tickCount" not in axis
    assert len(axis["values"]) == 5
    assert "'%-d'" in axis["labelExpr"]


def test_long_weekly_axis_promotes_labels_and_ticks_to_months(make_chart):
    import datetime

    chart = make_chart("line", x="week", y="value")
    start = datetime.date(2024, 1, 1)
    data = [
        {"week": (start + datetime.timedelta(weeks=i)).isoformat(), "value": i}
        for i in range(70)
    ]

    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CONTEXT
    )
    axis = _x_axis(spec)

    # Render-local (non-authored) promotion on a continuous temporal axis:
    # explicit `values` carries the promoted cadence, built from the real
    # calendar-bucket openers (so the domain-start tick is never silently
    # dropped when it isn't itself a calendar boundary — see
    # type_inference.py's temporal branch).
    # Thinned to one opener per represented month (16), not the full
    # 70-week list — a regression back to the un-thinned week list would
    # still pass a values[0]-only check.
    assert len(axis["values"]) == 16
    assert axis["values"][0] == data[0]["week"]
    assert axis["values"][1] == "2024-02-05"
    assert "tickCount" not in axis
    assert "'%b'" in axis["labelExpr"]
    assert "W%V" not in axis["labelExpr"]
