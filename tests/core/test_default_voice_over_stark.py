"""Tests for the `clarity`/`paper` themes' editorial-voice overrides on `stark`.

The `clarity` theme extends `stark` and adds an editorial voice: a serif KPI
value font, legend disabled, axis-domain lines hidden. `paper` extends
`clarity` and inherits the same overrides on a warm canvas.
These tests assert the overrides exist (and propagate), without pinning the
specific aesthetic values they resolve to — those values are tunable and
shouldn't break the test when re-tuned (per dbt-charts/AGENTS.md: *"Don't pin
theme/default values in tests. Test structure, presence, and behavior under
override."*).
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import AreaChart, BarChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)

from .conftest import chart_pane

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())


def _themed(name: str):
    return resolve_style_and_context(get_theme_style(name))[1].pre_style


EDITORIAL_THEMES = ["clarity", "paper"]


@pytest.fixture(autouse=True)
def _isolate_config_style():
    """Some tests patch config.style to exercise theme-specific rendering."""
    from dbt_charts.core.compile.config import reset_config

    reset_config()
    yield
    reset_config()


# --------------------------------------------------------------------------
# KPI value serif font — `default` overrides the `stark` tabular sans with a
# serif. `cream` inherits via `extends: default`.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("theme_name", EDITORIAL_THEMES)
def test_kpi_value_font_family_overrides_stark(theme_name: str) -> None:
    """The editorial-voiced themes set a serif `kpi.value.font.family` distinct from stark."""
    stark = _themed("stark").charts.kpi.value.font.family
    editorial = _themed(theme_name).charts.kpi.value.font.family
    assert editorial is not None, (
        f"{theme_name}.charts.kpi.value.font.family is None — "
        f"default.yaml did not set the override."
    )
    assert editorial != stark, (
        f"{theme_name}.charts.kpi.value.font.family resolved to the same "
        f"stack as stark ({stark!r}); the editorial-voice override "
        f"didn't apply."
    )
    # `default` KPI value uses a serif (paired with the proportional
    # narrative voice). Specific stack is tunable; "serif" in the stack
    # is the structural intent.
    assert "serif" in editorial.lower(), (
        f"{theme_name}.charts.kpi.value.font.family is {editorial!r}; "
        f"the editorial-voiced themes use a serif KPI value font."
    )


# --------------------------------------------------------------------------
# Editorial-voice overrides over `stark`. Boolean defaults (legend.visible,
# axis_*.domain.visible) are an explicit core AGENTS.md carve-out from the
# don't-pin rule — they assert UX-default identity, not aesthetic value, so
# a direct `is False` assertion is fine.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("theme_name", EDITORIAL_THEMES)
def test_legend_disabled_by_default(theme_name: str) -> None:
    theme = get_theme_style(theme_name)
    assert theme.charts.legend.visible is False, (
        f"{theme_name}.charts.legend.visible is "
        f"{theme.charts.legend.visible!r}; the editorial-voiced themes suppress "
        f"the legend by default (direct labels and editorial titles carry "
        f"the framing). Add `legend.visible: false` to clarity.yaml."
    )


@pytest.mark.parametrize("theme_name", EDITORIAL_THEMES)
@pytest.mark.parametrize("axis", ["axis_x", "axis_y"])
def test_axis_domain_not_visible_by_default(theme_name, axis):
    """Axis domain lines are hidden on the editorial-voiced themes — verified
    through the resolved cascade rather than at the pre-cascade theme layer.

    The hiding now happens at `stark` (axis.line.visible: false cascades to
    axis_x and axis_y), so default.yaml no longer needs to re-assert it. The
    user-facing contract — "domain lines off by default on the editorial
    themes" — is preserved.
    """
    ctx = resolve_chart_style_context(get_theme_style(theme_name))
    channel_type = "ordinal" if axis == "axis_x" else "quantitative"
    axis_style = resolved_axis_style(
        ctx, axis, channel_type, chart_type="", label_authored=False
    )
    assert axis_style.line.visible is False, (
        f"resolved {theme_name}.charts.{axis}.line.visible is "
        f"{axis_style.line.visible!r}; the editorial-voiced themes must "
        f"hide axis domain lines after cascade resolution."
    )


# --------------------------------------------------------------------------
# Inheritance — `paper` picks up `clarity`'s overrides via `extends:`.
# Spot-check via the KPI override since it's the most distinctive.
# --------------------------------------------------------------------------


def test_paper_inherits_clarity_kpi_value_font():
    """`paper` inherits its parent `clarity`'s KPI value font."""
    parent = _themed("clarity").charts.kpi.value.font.family
    child = _themed("paper").charts.kpi.value.font.family
    assert child == parent, (
        f"paper.charts.kpi.value.font.family is {child!r}, "
        f"clarity parent is {parent!r}; the `extends: clarity` "
        f"inheritance chain didn't propagate the override."
    )


# --------------------------------------------------------------------------
# Legend visible propagates through resolve_style — the editorial-voiced
# themes must have visible=False survive the compiled→resolved conversion.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("theme_name", EDITORIAL_THEMES)
def test_legend_disable_propagates_through_resolve_style(theme_name: str) -> None:
    """legend.visible=False on the compiled theme must survive resolve_style.

    The compiled-theme test (test_legend_disabled_by_default) passes, but
    _build_resolved_legend previously dropped the field by not copying it.
    This test exercises the full compiled→resolved path to guard against
    that regression.
    """
    compiled = get_theme_style(theme_name)
    ctx = resolve_chart_style_context(compiled)
    assert ctx.legend.visible is False, (
        f"{theme_name}.charts.legend.visible lost during resolve_style: "
        f"{ctx.legend.visible!r}"
    )


# --------------------------------------------------------------------------
# Sparse y-axis tick count — the editorial themes set ticks.count: 6 on
# axis_quantitative so out-of-box bar/line/area charts render 5–8 ticks, not
# the ~20 VL picks by default on a 0–4000 range. Enforced via tickValues emit
# (VL's tickCount is advisory and frequently ignored).
# --------------------------------------------------------------------------


@pytest.mark.parametrize("theme_name", EDITORIAL_THEMES)
def test_default_theme_quantitative_y_emits_sparse_tick_values(
    monkeypatch: pytest.MonkeyPatch, theme_name
) -> None:
    """Under the editorial themes, a bar chart with y in 0-4000 emits ≤ 6 tick
    values on the y-axis — not the ~20 VL defaults to on that range.

    Regression test for the sparse-tick-density fix: default.yaml sets
    axis_quantitative.ticks.count: 6, the renderer computes nice tickValues,
    and VL renders exactly that many gridlines.
    """
    from dbt_charts.core.compile.config import reset_config
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    reset_config()
    monkeypatch.setenv("DCT_DEFAULT_THEME", theme_name)
    data = [{"month": f"2024-{i:02d}", "revenue": i * 400} for i in range(1, 11)]
    chart = BarChart(
        id="t",
        type="bar",
        x="month",
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
    )
    _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, data, width=400)
    y_axis = spec.get("encoding", {}).get("y", {}).get("axis", {})
    tick_values = y_axis.get("values")
    assert tick_values is not None, (
        f"{theme_name}: encoding.y.axis.values is absent — "
        f"the renderer must emit explicit tickValues when ticks.count is set "
        f"to enforce sparse y-axis tick density."
    )
    assert len(tick_values) <= 6, (
        f"{theme_name}: {len(tick_values)} tick values emitted on 0-3600 range; "
        f"expected ≤ 6. Got: {tick_values}"
    )


@pytest.mark.parametrize("theme_name", EDITORIAL_THEMES)
def test_bar_tick_values_include_zero(
    monkeypatch: pytest.MonkeyPatch, theme_name
) -> None:
    """Bar chart tickValues must include 0 when the minimum y value is > 0.

    VL bar marks default to scale.zero=true so the axis spans [0, max]. The
    emitted tickValues must agree — otherwise the axis has no labels on the
    zero-to-min segment.
    """
    from dbt_charts.core.compile.config import reset_config
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    reset_config()
    monkeypatch.setenv("DCT_DEFAULT_THEME", theme_name)
    # Data starts high — all y values well above the nice step boundary.
    data = [{"month": f"2024-{i:02d}", "revenue": 3000 + i * 500} for i in range(1, 5)]
    chart = BarChart(
        id="t",
        type="bar",
        x="month",
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
    )
    _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, data, width=400)
    y_axis = spec.get("encoding", {}).get("y", {}).get("axis", {})
    tick_values = y_axis.get("values")
    assert tick_values is not None, f"{theme_name}: no tickValues emitted"
    assert tick_values[0] == 0.0, (
        f"{theme_name}: tickValues[0] is {tick_values[0]} — bar scale defaults "
        f"to zero=true so ticks must start at 0. Got: {tick_values}"
    )


@pytest.mark.parametrize("theme_name", EDITORIAL_THEMES)
def test_stacked_bar_tick_values_use_stacked_totals(
    monkeypatch: pytest.MonkeyPatch, theme_name
) -> None:
    """Stacked bar tickValues must be derived from stacked column totals, not raw row values.

    For a category-stacked bar (stack='zero') with two categories each
    contributing ~2000, the stacked total per product is ~4000. Using raw
    min/max of individual rows would cap tickValues at 2200, leaving the top
    half of the axis unlabeled.
    """
    from dbt_charts.core.compile.config import reset_config
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    reset_config()
    monkeypatch.setenv("DCT_DEFAULT_THEME", theme_name)
    # Use ISO date x so bar renders vertical (discrete nominal x → horizontal).
    data = [
        {"month": "2024-01", "category": "X", "revenue": 2000},
        {"month": "2024-01", "category": "Y", "revenue": 1800},
        {"month": "2024-02", "category": "X", "revenue": 2200},
        {"month": "2024-02", "category": "Y", "revenue": 2100},
    ]
    chart = BarChart(
        id="t",
        type="bar",
        x="month",
        y="revenue",
        color="category",
        stack="zero",
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
    )
    _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, data, width=400)
    # Endpoint labels wrap the chart in hconcat/vconcat — unwrap to the real
    # chart pane first (chart_pane() is a no-op otherwise).
    y_axis = chart_pane(spec).get("encoding", {}).get("y", {}).get("axis", {})
    tick_values = y_axis.get("values")
    assert tick_values is not None, f"{theme_name}: no tickValues emitted"
    stacked_max = 2000 + 1800  # month 2024-01 stacked total (category X + Y)
    assert max(tick_values) >= stacked_max, (
        f"{theme_name}: tickValues max is {max(tick_values)} — must reach the "
        f"stacked total {stacked_max}. Got: {tick_values}"
    )


@pytest.mark.parametrize("chart_type", ["scatter", "line"])
@pytest.mark.parametrize("theme_name", EDITORIAL_THEMES)
def test_tick_values_pin_scale_domain(
    monkeypatch: pytest.MonkeyPatch, theme_name: str, chart_type: str
) -> None:
    """Domain-pinning rules for charts with clustered data well above zero.

    line/scatter with ratio > 0.25 — smart-auto sets scale.zero=false
    (zoomed axis). Headroom is span-relative and symmetric: both domain_max
    and domain_min are pinned so the marks sit ~headroom% from each edge.
    The exact values are read from the theme rather than hardcoded, per the
    "don't pin tunable theme defaults" test convention.

    Area is excluded: unlike line/scatter, area always zero-anchors positive
    data regardless of ratio (see test_area_domain_zero_anchors_even_above_ratio_threshold
    below) — its fill is the magnitude encoding, so it never takes the
    zoomed/span-relative headroom path this test exercises.
    """
    from pydantic import TypeAdapter as _TA

    from dbt_charts.core.compile.config import reset_config
    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    reset_config()
    monkeypatch.setenv("DCT_DEFAULT_THEME", theme_name)
    # Data well above zero with ratio ≈ 0.58 > 0.25 — smart-auto sets zero:False
    # for area/line/scatter (zoomed axis; span-relative headroom applies).
    revenues = [97000 + i * 2500 for i in range(1, 29)]
    data = [
        {"week": f"2024-01-{i:02d}", "revenue": r}
        for i, r in enumerate(revenues, start=1)
    ]
    chart = _TA(Chart).validate_python(
        {
            "id": "t",
            "type": chart_type,
            "x": "week",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
        }
    )
    _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, data, width=400)
    y_enc = spec.get("encoding", {}).get("y", {})
    tick_values = y_enc.get("axis", {}).get("values")
    assert tick_values is not None, f"{theme_name}/{chart_type}: no tickValues emitted"

    y_scale = y_enc.get("scale") or {}
    domain_min = y_scale.get("domainMin")
    domain_max = y_scale.get("domainMax")

    theme_headroom = _BOARD_STYLE.axis_y.scale.headroom or 0
    data_min = min(revenues)
    data_max = max(revenues)
    span = data_max - data_min
    expected_domain_max = data_max + theme_headroom * span
    expected_domain_min = data_min - theme_headroom * span
    assert domain_max == pytest.approx(expected_domain_max), (
        f"{theme_name}/{chart_type}: scale.domainMax={domain_max!r} should equal "
        f"data_max + headroom*span = {expected_domain_max!r}."
    )
    assert domain_min == pytest.approx(expected_domain_min), (
        f"{theme_name}/{chart_type}: scale.domainMin={domain_min!r} should equal "
        f"data_min - headroom*span = {expected_domain_min!r}."
    )


@pytest.mark.parametrize("theme_name", EDITORIAL_THEMES)
def test_area_domain_zero_anchors_even_above_ratio_threshold(
    monkeypatch: pytest.MonkeyPatch, theme_name: str
) -> None:
    """Area with the same clustered, ratio > 0.25 data as
    test_tick_values_pin_scale_domain still zero-anchors: unlike line/scatter,
    area's fill is the magnitude encoding, so it always extends to zero for
    all-positive data (top-only multiplicative headroom, not span-relative)."""
    from pydantic import TypeAdapter as _TA

    from dbt_charts.core.compile.config import reset_config
    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    reset_config()
    monkeypatch.setenv("DCT_DEFAULT_THEME", theme_name)
    revenues = [97000 + i * 2500 for i in range(1, 29)]
    data = [
        {"week": f"2024-01-{i:02d}", "revenue": r}
        for i, r in enumerate(revenues, start=1)
    ]
    chart = _TA(Chart).validate_python(
        {
            "id": "t",
            "type": "area",
            "x": "week",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
        }
    )
    spec = generate_vega_lite_spec(chart, data, width=400)
    y_scale = spec.get("encoding", {}).get("y", {}).get("scale") or {}
    assert y_scale.get("zero") is True
    assert y_scale.get("domainMin") == 0.0

    theme_headroom = _BOARD_STYLE.axis_y.scale.headroom or 0
    expected_domain_max = max(revenues) * (1 + theme_headroom)
    assert y_scale.get("domainMax") == pytest.approx(expected_domain_max), (
        f"{theme_name}/area: scale.domainMax={y_scale.get('domainMax')!r} should "
        f"equal data_max * (1 + headroom) = {expected_domain_max!r}."
    )


@pytest.mark.parametrize("chart_type", ["area", "bar"])
@pytest.mark.parametrize("theme_name", EDITORIAL_THEMES)
def test_area_near_zero_pins_domain_min(
    monkeypatch: pytest.MonkeyPatch, theme_name: str, chart_type: str
) -> None:
    """When scale.zero=true forces zero into the domain, domainMin must be pinned.

    With data near zero (ratio ≤ 0.25 for area; always for bar), smart-auto
    keeps scale.zero=true so VL extends to zero. The domainMin pin prevents a
    blank gap below the first gridline (e.g. $0–$80K gap on a $100K–$400K bar).
    """
    from pydantic import TypeAdapter as _TA

    from dbt_charts.core.compile.config import reset_config
    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    reset_config()
    monkeypatch.setenv("DCT_DEFAULT_THEME", theme_name)
    # ratio = 50k/450k ≈ 0.11 ≤ 0.25 threshold → smart-auto keeps zero:True for area;
    # bar always uses zero:True.  Data well above 0 so ticks start > 0.
    data = [
        {"week": f"2024-01-{i:02d}", "revenue": 50000 + i * 15000} for i in range(1, 29)
    ]
    chart = _TA(Chart).validate_python(
        {
            "id": "t",
            "type": chart_type,
            "x": "week",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
        }
    )
    _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, data, width=400)
    y_enc = spec.get("encoding", {}).get("y", {})
    tick_values = y_enc.get("axis", {}).get("values")
    assert tick_values is not None, f"{theme_name}/{chart_type}: no tickValues emitted"

    y_scale = y_enc.get("scale") or {}
    domain_min = y_scale.get("domainMin")

    # scale.zero=true is forced → VL would extend to 0, creating a blank gap
    # below the first tick. domainMin must be pinned to the first tick value.
    assert domain_min == tick_values[0], (
        f"{theme_name}/{chart_type}: scale.domainMin={domain_min!r} should equal "
        f"first tick {tick_values[0]} to prevent blank space below the lowest gridline."
    )


@pytest.mark.parametrize("stack_mode", ["zero", "center"])
@pytest.mark.parametrize("theme_name", EDITORIAL_THEMES)
def test_stacked_area_no_domain_pin(
    monkeypatch: pytest.MonkeyPatch,
    model_copy_at: Any,
    theme_name: str,
    stack_mode: str,
) -> None:
    """Stacked area charts must NOT have domainMax pinned.

    nice_tick_values computes domain_max from individual row values, which is
    far below the stacked total. Pinning domainMax to that value clips the
    stacked marks above it, making the chart disappear entirely.
    VL must own the domain-MAX computation for stacked and streamgraph area
    charts. domainMin is stack-mode dependent: stack="zero" zero-anchors
    all-positive data and pins domainMin=0.0 (the stack's own true baseline
    genuinely is 0, so pinning that bottom edge doesn't clip anything);
    stack="center" (streamgraph) is carved out of the zero-anchor bake
    entirely, so domainMin stays unset.

    Endpoint labels are forced off here: stack='center' has no cumulative-midpoint
    anchor (same restriction bar already has — ChartDataError, see
    EndpointLabelFeature.apply), and clarity/paper default area endpoint_labels
    to visible. This test targets domain-pin behavior, not endpoint labels.
    """
    from dbt_charts.core.compile.config import reset_config
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    reset_config()
    monkeypatch.setenv("DCT_DEFAULT_THEME", theme_name)
    # Two series stacked — stacked max (~350K) far exceeds individual max (~175K).
    data = [
        {"month": f"2024-{i:02d}", "series": s, "revenue": 97000 + i * 2500 + offset}
        for i in range(1, 13)
        for s, offset in [("A", 0), ("B", 50000)]
    ]
    chart = AreaChart(
        id="t",
        type="area",
        x="month",
        y="revenue",
        color="series",
        stack=stack_mode,
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
    )
    seed = model_copy_at(
        get_theme_style(theme_name),
        "charts.area.endpoint_labels",
        EndpointLabelsConfig(visible=False, label_offset=5.0, height=20.0),
    )
    board_rs, board_ctx = resolve_style_and_context(seed)
    _rc = resolve(chart, data, chart_style_context=board_ctx)
    spec = generate_vega_lite_spec(
        chart, data, width=400, board_style=board_rs, chart_style_context=board_ctx
    )
    y_enc = spec.get("encoding", {}).get("y", {})
    y_scale = y_enc.get("scale") or {}
    # stack="zero" always zero-anchors positive data (bar-like magnitude), so
    # domainMin is genuinely pinned at 0.0. stack="center" (streamgraph) is
    # carved out of the zero-anchor bake entirely -- its y=0 is the
    # silhouette's centerline, not a baseline -- so domainMin stays unset.
    expected_domain_min = 0.0 if stack_mode == "zero" else None
    assert y_scale.get("domainMin") == expected_domain_min, (
        f"{theme_name}/area stack={stack_mode!r}: domainMin={y_scale.get('domainMin')} "
        f"must equal {expected_domain_min!r}."
    )
    assert "domainMax" not in y_scale, (
        f"{theme_name}/area stack={stack_mode!r}: domainMax={y_scale.get('domainMax')} "
        f"must not be set — individual-row max clips the stacked marks."
    )
