"""Tests for render-v2 ChartFeature implementations — both unit and pipeline integration.

Feature unit tests (applies_to / apply) pass against the already-implemented
features modules.  The integration tests below call the full pipeline:

    emitter.emit() → FeaturePipeline([feature]).apply() → ChartSpec
"""

from __future__ import annotations

import dataclasses
from typing import Any, Literal

import pytest

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedAreaStyle,
    ResolvedBarChart,
    ResolvedBarStyle,
    ResolvedHeatmapChart,
    ResolvedHeatmapStyle,
    ResolvedLineChart,
    ResolvedLineStyle,
    ResolvedPieChart,
    ResolvedPieStyle,
    ResolvedScatterChart,
    ResolvedScatterStyle,
    ResolvedStyleChannel,
)
from dbt_charts.core.compile.models.primitives import ResolvedFormat
from dbt_charts.core.compile.models.style.theme import PaddingStyle
from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorScale,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.compile.resolve.chart.label_data import (
    pie_presentation_fingerprint,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import ERR_GAP_FILL_BUCKET_COLLISION
from dbt_charts.core.render.chart.feature import FeaturePipeline
from dbt_charts.core.render.chart.features.baseline import BaselineFeature
from dbt_charts.core.render.chart.features.endpoint_labels import EndpointLabelFeature
from dbt_charts.core.render.chart.features.value_labels import ValueLabelFeature
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox

from ...conftest import baked_format

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)


# Required base fields (no defaults on non-None resolved model fields).
def _default_legend():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(
        get_theme_style(get_default_theme_name())
    ).chart_defaults.legend


def _default_charts():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(get_theme_style(get_default_theme_name())).chart_defaults


_DEFAULT_CHARTS = _default_charts()
_ZERO_PADDING = PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0)
_B: dict[str, Any] = {
    "variable_dependencies": frozenset(),
    "palette": (),
    "resolved_channels": {},
    "legend": _default_legend(),
    "background": _DEFAULT_CHARTS.background,
    "canvas": _DEFAULT_CHARTS.ink_canvas,
    "title_style": _DEFAULT_CHARTS.title,
    "layout_padding": _ZERO_PADDING,
}
_C: dict[str, Any] = dict(_B)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _series_channel() -> ResolvedStyleChannel:
    return ResolvedStyleChannel(channel="color", mode="series", data_field="category")


def _spec(mark: str = "bar") -> ChartSpec:
    return ChartSpec(mark=mark, encoding={}, layers=[], config={})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _bar(
    bar_style: ResolvedBarStyle,
    stack: Literal["none", "zero", "normalize", "center"] | None = None,
    link: str | None = None,
    resolved_channels: dict[str, ResolvedStyleChannel] | None = None,
    orientation: Literal["vertical", "horizontal"] = "vertical",
    axis_x: Any = None,
    axis_y: Any = None,
) -> ResolvedBarChart:
    if axis_x is not None or axis_y is not None:
        bar_style = bar_style.model_copy(update={"axis_x": axis_x, "axis_y": axis_y})
    return ResolvedBarChart(
        panel_axes=(),
        id="bar1",
        chart_type="bar",
        x="month",
        y="revenue",
        stack=stack,
        link=link,
        orientation=orientation,
        resolved_channels=resolved_channels or {},
        variable_dependencies=frozenset(),
        palette=(),
        style=bar_style,
        legend=_default_legend(),
        background=_DEFAULT_CHARTS.background,
        canvas=_DEFAULT_CHARTS.ink_canvas,
        title_style=_DEFAULT_CHARTS.title,
        layout_padding=_ZERO_PADDING,
    )


def _line(
    line_style: ResolvedLineStyle,
    resolved_channels: dict[str, ResolvedStyleChannel] | None = None,
    link: str | None = None,
) -> ResolvedLineChart:
    return ResolvedLineChart(
        panel_axes=(),
        id="line1",
        chart_type="line",
        x="date",
        y="value",
        link=link,
        resolved_channels=resolved_channels or {},
        variable_dependencies=frozenset(),
        palette=(),
        style=line_style,
        legend=_default_legend(),
        background=_DEFAULT_CHARTS.background,
        canvas=_DEFAULT_CHARTS.ink_canvas,
        title_style=_DEFAULT_CHARTS.title,
        layout_padding=_ZERO_PADDING,
    )


def _line_with_binding(
    line_style: ResolvedLineStyle,
    palette: tuple[str, ...],
    category_colors: tuple[CategoryColorScale, ...],
) -> ResolvedLineChart:
    return ResolvedLineChart(
        panel_axes=(),
        id="line1",
        chart_type="line",
        x="date",
        y="value",
        resolved_channels={"color": _series_channel()},
        variable_dependencies=frozenset(),
        palette=palette,
        category_colors=category_colors,
        style=line_style,
        legend=_default_legend(),
        background=_DEFAULT_CHARTS.background,
        canvas=_DEFAULT_CHARTS.ink_canvas,
        title_style=_DEFAULT_CHARTS.title,
        layout_padding=_ZERO_PADDING,
    )


def _area(
    area_style: ResolvedAreaStyle,
    format: str | None = None,
    stack: Literal["none", "zero", "normalize", "center"] | None = None,
) -> ResolvedAreaChart:
    return ResolvedAreaChart(
        panel_axes=(),
        id="area1",
        chart_type="area",
        x="date",
        y="value",
        format=format,
        stack=stack,
        style=area_style,
        **_C,
    )


def _scatter(scatter_style: ResolvedScatterStyle) -> ResolvedScatterChart:
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
    from dbt_charts.core.compile.resolve.style.axis_cascade import (
        AxisOverrides,
        build_resolved_axis,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    from ...conftest import fixture_chart_for_type

    _rcs = resolve_chart_style_context(get_theme_style())
    baked = _bake_cartesian_axes(
        _rcs,
        fixture_chart_for_type("scatter"),
        "scatter",
        "quantitative",
        "quantitative",
        AxisOverrides(),
    )
    ax_merged, ay_merged, ax_band_position, ay_band_position = (
        baked.x.style,
        baked.y.style,
        baked.x.band_position,
        baked.y.band_position,
    )

    ax = build_resolved_axis(
        ax_merged,
        format=baked_format(ax_merged),
        band_position=ax_band_position,
        chart_id="test",
        formats=None,
    )
    ay = build_resolved_axis(
        ay_merged,
        format=baked_format(ay_merged),
        band_position=ay_band_position,
        chart_id="test",
        is_quantitative=True,
        zero_anchored=True,
        formats=None,
    )
    return ResolvedScatterChart(
        panel_axes=(),
        id="sc1",
        chart_type="scatter",
        x="x",
        y="y",
        style=scatter_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )


def _pie(pie_style: ResolvedPieStyle) -> ResolvedPieChart:
    return ResolvedPieChart(
        id="pie1",
        chart_type="pie",
        resolution_width=600.0,
        outer_fraction=0.9,
        attached_table_gap=12.0,
        hybrid_heading_gap=6.0,
        presentation_fingerprint=pie_presentation_fingerprint([]),
        slice_label_indices=(),
        theta="value",
        style=pie_style,
        dark_companion_stops=(),
        **_B,
    )


# ---------------------------------------------------------------------------
# BaselineFeature — applies_to (unit)
# ---------------------------------------------------------------------------


def test_zero_baseline_applies_to_bar(bar_style: ResolvedBarStyle) -> None:
    assert BaselineFeature().applies_to(_bar(bar_style)) is True


def test_zero_baseline_applies_to_line(line_style: ResolvedLineStyle) -> None:
    assert BaselineFeature().applies_to(_line(line_style)) is True


def test_zero_baseline_applies_to_area(area_style: ResolvedAreaStyle) -> None:
    assert BaselineFeature().applies_to(_area(area_style)) is True


def test_zero_baseline_applies_to_scatter(
    scatter_style: ResolvedScatterStyle,
) -> None:
    assert BaselineFeature().applies_to(_scatter(scatter_style)) is True


def test_zero_baseline_does_not_apply_to_pie(pie_style: ResolvedPieStyle) -> None:
    assert BaselineFeature().applies_to(_pie(pie_style)) is False


def test_zero_baseline_does_not_apply_to_heatmap(
    heatmap_style: ResolvedHeatmapStyle,
) -> None:
    """Heatmap is a cartesian family but has no quantitative position axis --
    both channels always render as bands -- so it must fall out of the
    structural applies_to() check even though it shares the cartesian base
    class with bar/line/area/scatter."""
    chart = ResolvedHeatmapChart(
        panel_axes=(),
        id="hm1",
        chart_type="heatmap",
        x="category",
        y="segment",
        style=heatmap_style,
        **_C,
    )
    assert BaselineFeature().applies_to(chart) is False


# ---------------------------------------------------------------------------
# BaselineFeature — apply (unit)
# ---------------------------------------------------------------------------


def _baked_axes_for(chart_type: str) -> tuple[Any, Any]:
    """Return (resolved_axis_x, resolved_axis_y) baked from the default cascade."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
    from dbt_charts.core.compile.resolve.style.axis_cascade import (
        AxisOverrides,
        build_resolved_axis,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    from ...conftest import fixture_chart_for_type

    rcs = resolve_chart_style_context(get_theme_style())
    baked = _bake_cartesian_axes(
        rcs,
        fixture_chart_for_type(chart_type),
        chart_type,
        "nominal",
        "quantitative",
        AxisOverrides(),
    )
    ax_merged, ay_merged, ax_band_position, ay_band_position = (
        baked.x.style,
        baked.y.style,
        baked.x.band_position,
        baked.y.band_position,
    )

    return (
        build_resolved_axis(
            ax_merged,
            format=baked_format(ax_merged),
            band_position=ax_band_position,
            chart_id="test",
            formats=None,
        ),
        build_resolved_axis(
            ay_merged,
            format=baked_format(ay_merged),
            band_position=ay_band_position,
            chart_id="test",
            formats=None,
        ),
    )


def test_zero_baseline_adds_rule_layer_when_zero_not_in_range(
    bar_style: ResolvedBarStyle,
) -> None:
    """All-positive y (vertical bar): full span rule is appended at y=0."""
    ax, ay = _baked_axes_for("bar")
    chart = _bar(bar_style, axis_x=ax, axis_y=ay)
    spec = _spec("bar")
    data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
    result = BaselineFeature().apply(
        spec, chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert len(result.layers) == 1
    rule = result.layers[0]
    assert rule.mark == "rule"
    # Full span rule: y=datum:0 anchors the line; x/x2 span the plot width.
    assert rule.encoding.get("y") == {"datum": 0, "type": "quantitative"}
    assert rule.encoding.get("x") == {"value": 0}
    assert rule.encoding.get("x2") == {"value": "width"}
    # Synthetic data row for the rule's own data source
    assert rule.data == [{"revenue": 0}]


def _line_with_y_zero(
    line_style: ResolvedLineStyle, zero: bool | None
) -> ResolvedLineChart:

    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedScaleContinuousStyle,
        ResolvedScaleStyle,
    )

    ax, ay = _baked_axes_for("line")
    ay = dataclasses.replace(
        ay, scale=ResolvedScaleStyle(continuous=ResolvedScaleContinuousStyle(zero=zero))
    )
    return ResolvedLineChart(
        panel_axes=(),
        id="ln",
        chart_type="line",
        x="month",
        y="revenue",
        style=line_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )


def test_zero_baseline_fires_for_line_when_zero_not_explicitly_false(
    line_style: ResolvedLineStyle,
) -> None:
    """Line with scale.zero unset (None) gets a baseline rule (V1 parity).

    V1 _domain_includes_zero fires whenever scale.zero is not explicitly False —
    the rule's datum:0 pulls 0 into the unified domain. None is the near-zero /
    straddle decision the smart-zero heuristic bakes for lines.
    """
    chart = _line_with_y_zero(line_style, None)
    data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
    result = BaselineFeature().apply(
        _spec("line"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert len(result.layers) == 1
    assert result.layers[0].mark == "rule"


def test_zero_baseline_skips_line_when_zero_false_and_all_positive(
    line_style: ResolvedLineStyle,
) -> None:
    """Line with baked scale.zero=False and all-positive data gets NO rule.

    Mirrors V1: an explicit scale.zero=False (data lives far from zero) keeps the
    domain data-fitted, and the rule only fires if data straddles 0.
    """
    chart = _line_with_y_zero(line_style, False)
    data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
    result = BaselineFeature().apply(
        _spec("line"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert len(result.layers) == 0


def test_zero_baseline_skips_line_with_log_scale(
    line_style: ResolvedLineStyle,
) -> None:
    """A log-typed measure axis must never get a datum:0 zero-baseline rule.

    The rule's ``{"datum": 0, "type": "quantitative"}`` encoding pulls a literal
    0 into the shared y-scale's domain — fundamentally incompatible with a log
    scale (whose domain cannot include 0) and, empirically, enough to break
    Vega-Lite's entire axis rendering for the chart (verified via a real
    dct render of a log-scaled line chart: every y-axis tick label vanished).
    scale.zero is left unset (None) here specifically because that's the
    "should fire" case for every other scale type — log must override it.
    """
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedScaleContinuousStyle,
        ResolvedScaleStyle,
    )

    ax, ay = _baked_axes_for("line")
    ay = dataclasses.replace(
        ay,
        scale=ResolvedScaleStyle(continuous=ResolvedScaleContinuousStyle(type="log")),
    )
    chart = ResolvedLineChart(
        panel_axes=(),
        id="ln",
        chart_type="line",
        x="month",
        y="revenue",
        style=line_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )
    data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
    result = BaselineFeature().apply(
        _spec("line"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert len(result.layers) == 0


def test_cumulative_midpoints_orders_by_global_sum() -> None:
    """Endpoint-label rail orders series largest-global-sum first (baseline order).

    Mirrors the stacked-bar z-order; otherwise the rail labels anchor to the wrong
    segments on a stacked horizontal bar.
    """
    from dbt_charts.core.utils import cumulative_stack_midpoints

    # Global sums: B=80, A=200, C=260 → order C, A, B (descending). Top row = "m1".
    data = [
        {"x": "m1", "s": "A", "y": 100},
        {"x": "m1", "s": "B", "y": 30},
        {"x": "m1", "s": "C", "y": 120},
        {"x": "m2", "s": "A", "y": 100},
        {"x": "m2", "s": "B", "y": 50},
        {"x": "m2", "s": "C", "y": 140},
    ]
    result = cumulative_stack_midpoints(
        data,
        x_field="x",
        y_field="y",
        series_field="s",
        series_names=["A", "B", "C"],
        sort_by="",
        descending=False,
    )
    assert [s for s, _ in result] == ["C", "A", "B"]


def test_cumulative_midpoints_top_row_follows_the_category_key() -> None:
    """The rail's top row is the one the axis draws first, ragged rows included.

    ``x_domain_order`` folds each category with the aggregate the bar pins
    (``bar_sort_op``), so a sort by a key column ranks by the key itself: m3
    leads descending on 1/2/3. Folding with ``sum`` instead would rank
    2/4/3 and anchor the rail on m2 — a row the axis does not draw first.
    """
    from dbt_charts.core.utils import cumulative_stack_midpoints

    data = [
        {"x": "m1", "k": 1, "s": "A", "y": 10},
        {"x": "m1", "k": 1, "s": "B", "y": 20},
        {"x": "m2", "k": 2, "s": "A", "y": 30},
        {"x": "m2", "k": 2, "s": "B", "y": 40},
        {"x": "m3", "k": 3, "s": "A", "y": 50},
    ]
    result = cumulative_stack_midpoints(
        data,
        x_field="x",
        y_field="y",
        series_field="s",
        series_names=["A", "B"],
        sort_by="k",
        descending=True,
    )
    # Top row m3 holds A alone: A spans 0..50, and B seams at its right edge.
    assert result == [("A", 25.0), ("B", 50.0)]


def test_zero_baseline_always_fires_for_bar(bar_style: ResolvedBarStyle) -> None:
    """Bar uses scale.zero=True — zero baseline fires regardless of data range."""
    ax, ay = _baked_axes_for("bar")
    chart = _bar(bar_style, axis_x=ax, axis_y=ay)
    spec = _spec("bar")
    # Data straddles zero: still fires for bar (scale.zero always shows 0)
    data = [{"month": "Jan", "revenue": -10}, {"month": "Feb", "revenue": 10}]
    result = BaselineFeature().apply(
        spec, chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert len(result.layers) == 1
    assert result.layers[0].mark == "rule"


def test_zero_baseline_skips_empty_data(bar_style: ResolvedBarStyle) -> None:
    chart = _bar(bar_style)
    result = BaselineFeature().apply(
        _spec("bar"), chart, _DEFAULT_BOX, {chart.query_name: []}
    )
    assert len(result.layers) == 0


def test_zero_baseline_skips_independent_dual_axis(
    line_style: ResolvedLineStyle,
) -> None:
    """An independent-y dual-axis layered spec (spec.resolve.scale.y ==
    "independent") skips the whole feature -- a datum:0 rule would get its
    own y scale VL can't bind to the base measure scale, so it would float
    to the wrong position rather than sit on the shared baseline."""
    chart = _line(line_style)
    data = [{"month": "Jan", "revenue": -5}, {"month": "Feb", "revenue": 5}]
    spec = _spec("line")
    spec.resolve = {"scale": {"y": "independent"}}
    result = BaselineFeature().apply(
        spec, chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert len(result.layers) == 0


def test_zero_baseline_horizontal_bar_uses_y_as_measure(
    bar_style: ResolvedBarStyle,
) -> None:
    """Horizontal bar: measure is always chart.y (revenue), not chart.x (month).

    If the feature wrongly treats chart.x as the measure it crashes on a string value.
    """
    ax, ay = _baked_axes_for("bar")
    chart = _bar(bar_style, orientation="horizontal", axis_x=ax, axis_y=ay)
    spec = _spec("bar")
    # y=revenue is all-positive → rule should be added; x=month is a string field
    data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
    result = BaselineFeature().apply(
        spec, chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert len(result.layers) == 1
    assert result.layers[0].mark == "rule"


def test_zero_baseline_horizontal_bar_rule_anchors_on_x_axis(
    bar_style: ResolvedBarStyle,
) -> None:
    """Horizontal bar: the zero rule anchors datum=0 on x; y/y2 are span delimiters."""
    ax, ay = _baked_axes_for("bar")
    chart = _bar(bar_style, orientation="horizontal", axis_x=ax, axis_y=ay)
    spec = _spec("bar")
    data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
    result = BaselineFeature().apply(
        spec, chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert len(result.layers) == 1
    rule_enc = result.layers[0].encoding
    assert rule_enc.get("x") == {
        "datum": 0,
        "type": "quantitative",
    }, f"horizontal bar rule must anchor datum=0 on x, got {rule_enc!r}"
    # y/y2 are span delimiters (value=0 / value="height"), not data anchors
    assert rule_enc.get("y") == {"value": 0}, (
        f"horizontal bar rule y span must be value:0, got {rule_enc!r}"
    )
    assert rule_enc.get("y2") == {"value": "height"}, (
        f"horizontal bar rule y2 span must be value:'height', got {rule_enc!r}"
    )


def test_zero_baseline_horizontal_bar_log_typed_axis_y_skips_rule(
    bar_style: ResolvedBarStyle,
) -> None:
    """A log-typed axis_y (the cascade's measure slot, regardless of
    orientation) must never carry the datum:0 rule on a horizontal bar --
    same incompatibility as the vertical case. Built directly against
    ``BaselineFeature`` (bypassing ``_resolve_bar``'s own log-scale rejection,
    which is unconditional on orientation) so this exercises the guard's own
    axis read rather than the compile-time validation that would otherwise
    make this combination unreachable through the full pipeline.
    """
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedScaleContinuousStyle,
        ResolvedScaleStyle,
    )

    ax, ay = _baked_axes_for("bar")
    ay = dataclasses.replace(
        ay,
        scale=ResolvedScaleStyle(continuous=ResolvedScaleContinuousStyle(type="log")),
    )
    chart = _bar(bar_style, orientation="horizontal", axis_x=ax, axis_y=ay)
    spec = _spec("bar")
    data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
    result = BaselineFeature().apply(
        spec, chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert len(result.layers) == 0


# ---------------------------------------------------------------------------
# BaselineFeature — top/unity behavior (unit)
#
# applies_to() is now a single broad predicate (isinstance over the four
# families) shared by zero/top/unity — these tests exercise the local
# top-vs-unity decision inside apply() instead of a per-rule applies_to gate.
# ---------------------------------------------------------------------------


def test_baseline_unstacked_bar_emits_only_zero_rule(
    bar_style: ResolvedBarStyle,
) -> None:
    """Unstacked bar with data: exactly one zero rule, never a top 0/1 pair."""
    ax, ay = _baked_axes_for("bar")
    chart = _bar(bar_style, stack=None, axis_x=ax, axis_y=ay)
    data = [{"month": "Jan", "revenue": 100}]
    result = BaselineFeature().apply(
        _spec("bar"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert len(result.layers) == 1
    assert result.layers[0].encoding["y"]["datum"] == 0


def test_baseline_streamgraph_emits_no_rule(area_style: ResolvedAreaStyle) -> None:
    """Streamgraph (stack: center): y=0 is the silhouette's own centerline,
    not a meaningful baseline -- neither the zero rule nor top rules fire."""
    chart = _area(area_style, stack="center")
    data = [{"date": "Jan", "value": -5}, {"date": "Feb", "value": 5}]
    result = BaselineFeature().apply(
        _spec("area"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert len(result.layers) == 0


def test_baseline_normalize_stacked_area_without_percent_format_emits_top_rules(
    area_style: ResolvedAreaStyle,
) -> None:
    """Normalize-stacked area with no % format: top 0/1 rule pair fires."""
    ax, ay = _baked_axes_for("area")
    chart = ResolvedAreaChart(
        panel_axes=(),
        id="area1",
        chart_type="area",
        x="date",
        y="value",
        stack="normalize",
        style=area_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )
    result = BaselineFeature().apply(
        _spec("area"), chart, _DEFAULT_BOX, {chart.query_name: []}
    )
    assert len(result.layers) == 2
    assert [layer.encoding["y"]["datum"] for layer in result.layers] == [0, 1]


def test_baseline_percent_format_area_fires_both_zero_and_unity_rules(
    area_style: ResolvedAreaStyle,
) -> None:
    """Non-normalize, percent-format area: zero and unity rules both fire.

    Regression for the merge: Zero and Unity are independent gates and are
    not mutually exclusive outside the normalize-stack cede branch.
    """
    ax, ay = _baked_axes_for("area")
    chart = ResolvedAreaChart(
        panel_axes=(),
        id="area1",
        chart_type="area",
        x="date",
        y="value",
        format=ResolvedFormat(spec=".0%", raw=".0%"),
        stack=None,
        style=area_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )
    # Data must straddle 1.0 for the unity rule to fire (mirrors V1's
    # _domain_includes_value gate); the zero rule fires independently.
    data = [{"date": "2024-01", "value": 0.1}, {"date": "2024-02", "value": 1.2}]
    result = BaselineFeature().apply(
        _spec("area"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert len(result.layers) == 2
    assert sorted(layer.encoding["y"]["datum"] for layer in result.layers) == [0, 1]


def test_baseline_no_unity_rule_without_percent_format(
    line_style: ResolvedLineStyle,
) -> None:
    chart = _line_with_y_zero(line_style, None)
    data = [{"month": "Jan", "revenue": -1}, {"month": "Feb", "revenue": 1}]
    result = BaselineFeature().apply(
        _spec("line"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert len(result.layers) == 1  # zero rule only; no unity rule


def test_baseline_no_unity_rule_for_bar(bar_style: ResolvedBarStyle) -> None:
    ax, ay = _baked_axes_for("bar")
    chart = _bar(bar_style, axis_x=ax, axis_y=ay)
    data = [{"month": "Jan", "revenue": 100}]
    result = BaselineFeature().apply(
        _spec("bar"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert len(result.layers) == 1  # zero rule only; unity never applies to bar


def test_unity_baseline_applies_to_format_alias_line(
    line_style: ResolvedLineStyle,
) -> None:
    """BaselineFeature must emit a unity rule when axis_y.labels.format resolves to a D3 percent spec.

    chart.format may be a dbt charts alias ('percent_whole') that contains no literal '%'.
    The baked axis_y.labels.format ('.0%') is the resolved D3 spec and is the canonical
    signal so the 100% rule fires for NRR-style percent line charts.
    """
    pct_labels = dataclasses.replace(
        line_style.axis_y.labels, format=ResolvedFormat(spec=".0%")
    )
    pct_axis_y = dataclasses.replace(line_style.axis_y, labels=pct_labels)
    pct_style = line_style.model_copy(update={"axis_y": pct_axis_y})
    chart = ResolvedLineChart(
        panel_axes=(),
        id="nrr",
        chart_type="line",
        x="month",
        y="pct",
        format="percent_whole",  # alias — no literal '%'
        variable_dependencies=frozenset(),
        resolved_channels={},
        palette=(),
        style=pct_style,
        legend=_default_legend(),
        background=_DEFAULT_CHARTS.background,
        canvas=_DEFAULT_CHARTS.ink_canvas,
        title_style=_DEFAULT_CHARTS.title,
        layout_padding=_ZERO_PADDING,
    )
    # Data reaches 1.0 so the unity rule's domain gate passes; this test isolates
    # the format-alias detection (percent_whole → axis_y.format '.0%').
    data = [{"month": "Jan", "pct": 0.5}, {"month": "Feb", "pct": 1.1}]
    result = BaselineFeature().apply(
        _spec("line"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    unity_layers = [
        layer
        for layer in result.layers
        if layer.encoding.get("y", {}).get("datum") == 1
    ]
    assert unity_layers, (
        "BaselineFeature must emit a unity rule for a line chart with "
        "axis_y.format='.0%' even when chart.format is a dbt charts alias."
    )


# ---------------------------------------------------------------------------
# BaselineFeature — apply emits real rule mark
# ---------------------------------------------------------------------------


def test_top_baseline_apply_appends_rules_at_zero_and_one(
    bar_style: ResolvedBarStyle,
) -> None:
    """BaselineFeature appends styled 0% and 100% reference rule layers."""
    ax, ay = _baked_axes_for("bar")
    chart = _bar(bar_style, stack="normalize", axis_x=ax, axis_y=ay)
    result = BaselineFeature().apply(
        _spec("bar"), chart, _DEFAULT_BOX, {chart.query_name: []}
    )
    assert len(result.layers) == 2
    assert all(layer.mark == "rule" for layer in result.layers)
    assert [layer.encoding["y"]["datum"] for layer in result.layers] == [0, 1]
    # Styled like the zero baseline (full-width span, not a bare default rule).
    assert all(layer.encoding["x2"] == {"value": "width"} for layer in result.layers)
    assert all("strokeWidth" in layer.mark_props for layer in result.layers)


def test_top_baseline_horizontal_bar_rules_anchor_on_x(
    bar_style: ResolvedBarStyle,
) -> None:
    """Horizontal normalized bar: 0%/100% rules anchor datum on x, not y.

    Regression: the measure axis is x for horizontal bars, so quantitative datum
    rules on the categorical y axis are wrong (mirror BaselineFeature).
    """
    ax, ay = _baked_axes_for("bar")
    chart = _bar(
        bar_style,
        stack="normalize",
        orientation="horizontal",
        axis_x=ax,
        axis_y=ay,
    )
    result = BaselineFeature().apply(
        _spec("bar"), chart, _DEFAULT_BOX, {chart.query_name: []}
    )
    assert len(result.layers) == 2
    assert [layer.encoding["x"]["datum"] for layer in result.layers] == [0, 1]
    assert all(
        layer.encoding.get("y2") == {"value": "height"} for layer in result.layers
    )


def test_unity_baseline_apply_appends_styled_rule_at_one(
    area_style: ResolvedAreaStyle,
) -> None:
    """BaselineFeature appends a styled rule layer at y=1."""
    # Data reaches 1.0 so the unity rule's domain gate passes (the zero rule
    # fires independently); isolate and assert the styled unity rule.
    data = [{"date": "2024-01", "value": 0.5}, {"date": "2024-02", "value": 1.1}]
    _chart = _area(area_style, format=ResolvedFormat(spec=".0%", raw=".0%"))
    result = BaselineFeature().apply(
        _spec("area"), _chart, _DEFAULT_BOX, {_chart.query_name: data}
    )
    unity = [
        layer
        for layer in result.layers
        if layer.encoding.get("y", {}).get("datum") == 1
    ]
    assert len(unity) == 1
    rule = unity[0]
    assert rule.mark == "rule"
    assert rule.encoding.get("y", {}).get("datum") == 1
    assert "x" in rule.encoding
    assert "x2" in rule.encoding
    assert "color" in rule.mark_props
    assert "strokeWidth" in rule.mark_props


# ---------------------------------------------------------------------------
# EndpointLabelFeature — applies_to (unit)
# ---------------------------------------------------------------------------


def test_endpoint_label_applies_to_multi_series_line_when_opted_in(
    line_style: ResolvedLineStyle,
) -> None:
    style = line_style.model_copy(
        update={
            "endpoint_labels": line_style.endpoint_labels.model_copy(
                update={"visible": True}
            )
        }
    )
    chart = _line(style, resolved_channels={"color": _series_channel()})
    assert EndpointLabelFeature().applies_to(chart) is True


def test_endpoint_label_does_not_apply_to_multi_series_line_without_opt_in(
    line_style: ResolvedLineStyle,
) -> None:
    """A multi-series line WITHOUT endpoint_labels.visible keeps the side legend.

    V1 endpoint_label_pane_will_fire gates every family on
    endpoint_labels.visible — the rail is opt-in, not automatic for series color.
    """
    chart = _line(line_style, resolved_channels={"color": _series_channel()})
    assert EndpointLabelFeature().applies_to(chart) is False


def test_endpoint_label_does_not_apply_to_single_series_line(
    line_style: ResolvedLineStyle,
) -> None:
    style = line_style.model_copy(
        update={
            "endpoint_labels": line_style.endpoint_labels.model_copy(
                update={"visible": True}
            )
        }
    )
    assert EndpointLabelFeature().applies_to(_line(style)) is False


def test_endpoint_label_does_not_apply_to_horizontal_ungrouped_bar(
    bar_style: ResolvedBarStyle,
) -> None:
    """Horizontal bar with no explicit stack (ungrouped default) does not fire."""
    chart = _bar(
        bar_style,
        orientation="horizontal",
        resolved_channels={"color": _series_channel()},
    )
    assert EndpointLabelFeature().applies_to(chart) is False


def test_endpoint_label_applies_to_horizontal_stacked_bar(
    bar_style: ResolvedBarStyle,
) -> None:
    """Horizontal stacked bar fires the top_rail only when opted in."""
    style = bar_style.model_copy(
        update={
            "endpoint_labels": bar_style.endpoint_labels.model_copy(
                update={"visible": True}
            )
        }
    )
    chart = _bar(
        style,
        stack="zero",
        orientation="horizontal",
        resolved_channels={"color": _series_channel()},
    )
    assert EndpointLabelFeature().applies_to(chart) is True


def test_endpoint_label_does_not_apply_to_horizontal_stacked_bar_without_opt_in(
    bar_style: ResolvedBarStyle,
) -> None:
    """Horizontal stacked bar WITHOUT endpoint_labels.visible keeps the side legend.

    Regression: the feature previously fired for any stacked horizontal,
    wrongly wrapping the 'feature OFF' chart in a rail and dropping its legend.
    """
    chart = _bar(
        bar_style,
        stack="zero",
        orientation="horizontal",
        resolved_channels={"color": _series_channel()},
    )
    assert EndpointLabelFeature().applies_to(chart) is False


# ---------------------------------------------------------------------------
# EndpointLabelFeature — apply (unit)
# ---------------------------------------------------------------------------


def test_endpoint_label_apply_sets_right_pane_layout(
    line_style: ResolvedLineStyle,
) -> None:
    """Multi-series line → endpoint_label_layout = 'right_pane'."""
    chart = _line(line_style, resolved_channels={"color": _series_channel()})
    data = [
        {"date": "2024-01", "value": 10, "category": "A"},
        {"date": "2024-02", "value": 20, "category": "A"},
        {"date": "2024-01", "value": 5, "category": "B"},
        {"date": "2024-02", "value": 15, "category": "B"},
    ]
    result = EndpointLabelFeature().apply(
        _spec("line"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert result.endpoint_label_layout == "right_pane"
    assert result.endpoint_label_data is not None
    assert result.endpoint_label_data.series_field == "category"
    assert result.endpoint_label_data.value_alias == "__y"
    # Both series have positions
    assert len(result.endpoint_label_data.positions) == 2


def test_endpoint_label_right_pane_font_style_reaches_mark(
    line_style: ResolvedLineStyle,
) -> None:
    """font_style authored on series_label reaches the right-pane label
    mark's fontStyle, mirroring font_family/font_size/font_weight — the
    first (multi-series) endpoint_labels.py call site."""
    style = line_style.model_copy(
        update={
            "series_label": line_style.series_label.model_copy(
                update={"font_style": "italic"}
            )
        }
    )
    chart = _line(style, resolved_channels={"color": _series_channel()})
    data = [
        {"date": "2024-01", "value": 10, "category": "A"},
        {"date": "2024-02", "value": 20, "category": "A"},
        {"date": "2024-01", "value": 5, "category": "B"},
        {"date": "2024-02", "value": 15, "category": "B"},
    ]
    result = EndpointLabelFeature().apply(
        _spec("line"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert result.endpoint_label_data is not None
    assert result.endpoint_label_data.label_mark_font_props.get("fontStyle") == "italic"


def test_endpoint_label_rail_colors_by_board_slot_not_local_sort_position(
    line_style: ResolvedLineStyle,
) -> None:
    """Each series' rail fill/ink must key off ITS OWN board slot.

    ``all_series = sorted(...)`` inside the feature puts "Alpha" before
    "Zeta" — but the board assigned the OPPOSITE slots (Zeta=0, Alpha=1,
    e.g. because some other chart on the board first-saw Zeta). A fix that
    colors by enumeration position over the locally-sorted list would hand
    "Alpha" the swatch that actually belongs to Zeta.
    """
    palette = ("#111111", "#222222")
    dark_palette = ("#aaaaaa", "#bbbbbb")
    style = line_style.model_copy(
        update={
            "series_label": line_style.series_label.model_copy(
                update={"dark_companion_palette": dark_palette}
            )
        }
    )
    scale = CategoryColorScale(
        field="category", slots={"Zeta": 0, "Alpha": 1}, overrides={}
    )
    chart = _line_with_binding(style, palette, (scale,))
    data = [
        {"date": "2024-01", "value": 10, "category": "Alpha"},
        {"date": "2024-02", "value": 20, "category": "Alpha"},
        {"date": "2024-01", "value": 5, "category": "Zeta"},
        {"date": "2024-02", "value": 15, "category": "Zeta"},
    ]
    result = EndpointLabelFeature().apply(
        _spec("line"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert result.endpoint_label_data is not None
    d = result.endpoint_label_data
    color_of = dict(zip(d.color_domain, d.color_range, strict=True))
    ink_of = dict(zip(d.color_domain, d.dark_companion_range, strict=True))
    assert color_of["Zeta"] == palette[0]
    assert color_of["Alpha"] == palette[1]
    assert ink_of["Zeta"] == dark_palette[0]
    assert ink_of["Alpha"] == dark_palette[1]


def test_endpoint_label_apply_sets_top_rail_layout(bar_style: ResolvedBarStyle) -> None:
    """Horizontal stacked bar → endpoint_label_layout = 'top_rail'."""
    chart = _bar(
        bar_style,
        stack="zero",
        orientation="horizontal",
        resolved_channels={"color": _series_channel()},
    )
    data = [
        {"month": "Jan", "revenue": 100, "category": "A"},
        {"month": "Jan", "revenue": 50, "category": "B"},
        {"month": "Feb", "revenue": 80, "category": "A"},
        {"month": "Feb", "revenue": 40, "category": "B"},
    ]
    result = EndpointLabelFeature().apply(
        _spec("bar"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert result.endpoint_label_layout == "top_rail"
    assert result.endpoint_label_data is not None
    assert result.endpoint_label_data.value_alias == "__x"


def test_endpoint_label_right_pane_spacing_from_label_offset(
    line_style: ResolvedLineStyle,
) -> None:
    """Right-pane spacing is sourced from ``endpoint_labels.label_offset`` —
    the same field v1 uses — not a separate series_label-owned knob."""
    distinctive = 33.0
    style = line_style.model_copy(
        update={
            "endpoint_labels": line_style.endpoint_labels.model_copy(
                update={"label_offset": distinctive}
            )
        }
    )
    chart = _line(style, resolved_channels={"color": _series_channel()})
    data = [
        {"date": "2024-01", "value": 10, "category": "A"},
        {"date": "2024-02", "value": 20, "category": "A"},
        {"date": "2024-01", "value": 5, "category": "B"},
        {"date": "2024-02", "value": 15, "category": "B"},
    ]
    result = EndpointLabelFeature().apply(
        _spec("line"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert result.endpoint_label_data is not None
    assert result.endpoint_label_data.label_offset == distinctive, (
        f"right-pane spacing is {result.endpoint_label_data.label_offset}, expected "
        f"{distinctive} — must source from endpoint_labels.label_offset"
    )


def test_endpoint_label_top_rail_spacing_from_label_offset(
    bar_style: ResolvedBarStyle,
) -> None:
    """Top-rail spacing is sourced from ``endpoint_labels.label_offset`` too —
    unifying v1/v2 on one spacing knob instead of a series_label-owned one."""
    distinctive = 27.0
    style = bar_style.model_copy(
        update={
            "endpoint_labels": bar_style.endpoint_labels.model_copy(
                update={"label_offset": distinctive}
            )
        }
    )
    chart = _bar(
        style,
        stack="zero",
        orientation="horizontal",
        resolved_channels={"color": _series_channel()},
    )
    data = [
        {"month": "Jan", "revenue": 100, "category": "A"},
        {"month": "Jan", "revenue": 50, "category": "B"},
        {"month": "Feb", "revenue": 80, "category": "A"},
        {"month": "Feb", "revenue": 40, "category": "B"},
    ]
    result = EndpointLabelFeature().apply(
        _spec("bar"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert result.endpoint_label_data is not None
    assert result.endpoint_label_data.label_offset == distinctive, (
        f"top-rail spacing is {result.endpoint_label_data.label_offset}, expected "
        f"{distinctive} — must source from endpoint_labels.label_offset"
    )


def test_endpoint_label_vertical_stacked_bar_midpoints(
    bar_style: ResolvedBarStyle,
) -> None:
    """Vertical stacked bar (stack='zero'): positions are cumulative segment midpoints.

    Dataset: two x-values, two series.
    Last x = "2024-02-01": A=40, B=60, total=100.
    Global sums: A=70, B=110 → B larger → B at baseline.
    Stack order (B first, then A):
      B: 0..60  → midpoint 30
      A: 60..100 → midpoint 80
    """
    chart = _bar(
        bar_style,
        stack="zero",
        resolved_channels={"color": _series_channel()},
    )
    data = [
        {"month": "2024-01-01", "revenue": 30, "category": "A"},
        {"month": "2024-01-01", "revenue": 50, "category": "B"},
        {"month": "2024-02-01", "revenue": 40, "category": "A"},
        {"month": "2024-02-01", "revenue": 60, "category": "B"},
    ]
    result = EndpointLabelFeature().apply(
        _spec("bar"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert result.endpoint_label_layout == "right_pane"
    assert result.endpoint_label_data is not None
    positions = dict(result.endpoint_label_data.positions)
    assert positions == {"B": 30.0, "A": 80.0}


def test_endpoint_label_vertical_normalize_bar_midpoints(
    bar_style: ResolvedBarStyle,
) -> None:
    """Vertical normalize stacked bar: positions are normalized (0-1) midpoints.

    Same dataset as above: B=0.30, A=0.80.
    """
    chart = _bar(
        bar_style,
        stack="normalize",
        resolved_channels={"color": _series_channel()},
    )
    data = [
        {"month": "2024-01-01", "revenue": 30, "category": "A"},
        {"month": "2024-01-01", "revenue": 50, "category": "B"},
        {"month": "2024-02-01", "revenue": 40, "category": "A"},
        {"month": "2024-02-01", "revenue": 60, "category": "B"},
    ]
    result = EndpointLabelFeature().apply(
        _spec("bar"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert result.endpoint_label_layout == "right_pane"
    assert result.endpoint_label_data is not None
    positions = dict(result.endpoint_label_data.positions)
    assert abs(positions["B"] - 0.30) < 1e-6
    assert abs(positions["A"] - 0.80) < 1e-6


def test_endpoint_label_normalize_bar_pins_y_domain(
    bar_style: ResolvedBarStyle,
) -> None:
    """Normalize stacked bar + endpoint labels: y scale domain is pinned to [0, 1].

    Without this, hconcat's shared y scale uses the raw value domain and
    all labels cluster near zero on 100% stacked charts.
    """
    chart = _bar(
        bar_style,
        stack="normalize",
        resolved_channels={"color": _series_channel()},
    )
    data = [
        {"month": "2024-01-01", "revenue": 30, "category": "A"},
        {"month": "2024-01-01", "revenue": 50, "category": "B"},
        {"month": "2024-02-01", "revenue": 40, "category": "A"},
        {"month": "2024-02-01", "revenue": 60, "category": "B"},
    ]
    spec = ChartSpec(
        mark="bar",
        encoding={
            "y": {
                "field": "revenue",
                "type": "quantitative",
                "scale": {"domainMax": 110.0},
            }
        },
    )
    result = EndpointLabelFeature().apply(
        spec, chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    y_scale = result.encoding.get("y", {}).get("scale", {})
    assert y_scale.get("domain") == [
        0,
        1,
    ], f"normalize stacked bar y domain must be [0,1], got {y_scale}"


def test_endpoint_label_vertical_grouped_bar_uses_bar_tops(
    bar_style: ResolvedBarStyle,
) -> None:
    """Vertical grouped bar (stack='none'): positions are bar tops, not midpoints."""
    chart = _bar(
        bar_style,
        stack="none",
        resolved_channels={"color": _series_channel()},
    )
    data = [
        {"month": "2024-01-01", "revenue": 30, "category": "A"},
        {"month": "2024-01-01", "revenue": 50, "category": "B"},
        {"month": "2024-02-01", "revenue": 40, "category": "A"},
        {"month": "2024-02-01", "revenue": 60, "category": "B"},
    ]
    result = EndpointLabelFeature().apply(
        _spec("bar"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert result.endpoint_label_data is not None
    positions = dict(result.endpoint_label_data.positions)
    # Last x = "2024-02-01": A=40, B=60 — labels at bar tops.
    assert positions == {"A": 40.0, "B": 60.0}


def test_endpoint_label_line_positions_are_raw_last_per_series(
    line_style: ResolvedLineStyle,
) -> None:
    """Line endpoint labels store raw, un-cascaded last-per-series values.

    The greedy-nudge cascade that used to run here at spec-build time is now
    deferred until the real plot geometry is known post-probe
    (``recascade_endpoint_labels`` in ``render/converters/chart.py``) — see
    ``EndpointLabelData``'s class docstring. Delivered, cascaded spacing is
    covered end-to-end (rendered SVG, not spec-build) by
    ``test_endpoint_label_gap.py``; this test only pins that ``apply()``
    stores each series' own last value, untouched.
    """
    chart = _line(line_style, resolved_channels={"color": _series_channel()})
    data = [
        {"date": "2025-01-01", "value": 50000, "category": s}
        for s in ("Core", "Self-Serve", "Enterprise")
    ] + [
        {"date": "2025-06-29", "value": v, "category": s}
        for s, v in [("Core", 155500), ("Self-Serve", 164500), ("Enterprise", 167000)]
    ]

    result = EndpointLabelFeature().apply(
        _spec("line"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert result.endpoint_label_data is not None
    positions = dict(result.endpoint_label_data.positions)
    assert positions == {
        "Core": 155500.0,
        "Self-Serve": 164500.0,
        "Enterprise": 167000.0,
    }
    assert result.endpoint_label_data.y_domain_min == 50000.0
    assert result.endpoint_label_data.y_domain_max == 167000.0
    assert result.endpoint_label_data.label_gap_px > 0


def test_line_emitter_buckets_ordinal_time_unit(line_style: ResolvedLineStyle) -> None:
    """A line with axis_x.time_unit=yearquarter keys quarter-end rows to quarters.

    Regression: V2 rendered every raw point (jagged) instead of V1's
    one-point-per-bucket. gap_fill_ordinal_time must bucket, and the emitter must
    carry the result on spec.data so the session doesn't restore the raw rows.

    The rows are quarter-ENDS, the ordinary ``LAST_DAY()`` shape: a value
    inside a bucket belongs to that bucket, so the emitter must key it to the
    bucket's start rather than looking for a row that already sits there.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.authored import AxisXStylePatch
    from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
    from dbt_charts.core.compile.resolve.style.axis_cascade import (
        AxisOverrides,
        build_resolved_axis,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.render.chart.emitters import get_emitter

    from ...conftest import fixture_chart_for_type

    rcs = resolve_chart_style_context(get_theme_style())
    overrides = AxisOverrides(
        x=AxisXStylePatch.model_validate({"time_unit": "yearquarter"})
    )
    baked = _bake_cartesian_axes(
        rcs,
        fixture_chart_for_type("line"),
        "line",
        "nominal",
        "quantitative",
        overrides,
    )
    ax_merged, ay_merged, ax_band_position, ay_band_position = (
        baked.x.style,
        baked.y.style,
        baked.x.band_position,
        baked.y.band_position,
    )

    ax = build_resolved_axis(
        ax_merged,
        format=baked_format(ax_merged),
        band_position=ax_band_position,
        chart_id="test",
        formats=None,
    )
    ay = build_resolved_axis(
        ay_merged,
        format=baked_format(ay_merged),
        band_position=ay_band_position,
        chart_id="test",
        formats=None,
    )
    chart = ResolvedLineChart(
        panel_axes=(),
        id="ln",
        chart_type="line",
        x="month",
        y="v",
        resolved_channels={"color": _series_channel()},
        variable_dependencies=frozenset(),
        palette=(),
        legend=_default_legend(),
        background=_DEFAULT_CHARTS.background,
        canvas=_DEFAULT_CHARTS.ink_canvas,
        title_style=_DEFAULT_CHARTS.title,
        layout_padding=_ZERO_PADDING,
        style=line_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
    )
    data = [
        {"month": "2025-09-30", "category": "A", "v": 9.0},
        {"month": "2025-12-31", "category": "A", "v": 12.0},
    ]  # Q3 + Q4 2025, reported at each quarter's last day
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), data))
    assert spec.data is not None
    months = sorted({row["month"] for row in spec.data})
    assert months == ["2025-07-01", "2025-10-01"]
    assert sorted(row["v"] for row in spec.data) == [9.0, 12.0]

    monthly = [
        {"month": f"2025-{m:02d}-01", "category": "A", "v": float(m)}
        for m in range(7, 13)
    ]
    with pytest.raises(ChartDataError) as excinfo:
        get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), monthly))
    # Three months per quarter is not one point per bucket; picking the
    # quarter's first month and dropping the other two would report July's
    # value as Q3's.
    assert excinfo.value.code is ERR_GAP_FILL_BUCKET_COLLISION


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------


def test_zero_baseline_pipeline_integration_bar(bar_style: ResolvedBarStyle) -> None:
    """BaselineFeature in a FeaturePipeline driven by the real BarEmitter output."""
    from dbt_charts.core.render.chart.emitters import get_emitter

    ax, ay = _baked_axes_for("bar")
    chart = _bar(bar_style, axis_x=ax, axis_y=ay)
    data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), data))
    pipeline = FeaturePipeline([BaselineFeature()])
    result = pipeline.apply(spec, chart, _DEFAULT_BOX, {chart.query_name: data})
    # All-positive data: a real rule layer at y=0 should be present
    rule_layers = [lay for lay in result.layers if lay.mark == "rule"]
    assert len(rule_layers) == 1
    assert rule_layers[0].encoding["y"]["datum"] == 0


def test_endpoint_label_pipeline_integration_line(
    line_style: ResolvedLineStyle,
) -> None:
    """EndpointLabelFeature in a pipeline: series-color line gets right-pane layout."""
    # Build the spec manually: series color raises NotImplementedError in
    # channel_to_encoding (palette not yet wired), so we can't call emit().
    # The feature only inspects resolved_channels and appends to spec.layers;
    # the spec shape is what matters, not how it was produced.
    style = line_style.model_copy(
        update={
            "endpoint_labels": line_style.endpoint_labels.model_copy(
                update={"visible": True}
            )
        }
    )
    chart = _line(style, resolved_channels={"color": _series_channel()})
    data = [
        {"date": "2024-01", "value": 10, "category": "A"},
        {"date": "2024-02", "value": 20, "category": "A"},
    ]
    spec = ChartSpec(
        mark="line", encoding={"x": {"field": "date"}, "y": {"field": "value"}}
    )
    pipeline = FeaturePipeline([EndpointLabelFeature()])
    result = pipeline.apply(spec, chart, _DEFAULT_BOX, {chart.query_name: data})
    assert result.endpoint_label_layout == "right_pane"
    assert result.endpoint_label_data is not None


def test_endpoint_label_line_always_uses_raw_positions_no_stack_concept(
    line_style: ResolvedLineStyle,
) -> None:
    """ResolvedLineChart declares no ``stack`` field — lines have no stacking
    concept in dbt charts. The area/bar stack-aware branch in
    EndpointLabelFeature.apply() must never reach line; it always anchors on
    raw per-series values at the trailing x. Regression guard for
    stacked-area-endpoint-labels-place-at-raw-y-not-band-midpoints: pins line
    behavior as byte-identical before/after widening the stack-aware isinstance
    check to include ResolvedAreaChart.
    """
    chart = _line(line_style, resolved_channels={"color": _series_channel()})
    data = [
        {"date": "2024-01-01", "value": 10, "category": "A"},
        {"date": "2024-02-01", "value": 20, "category": "A"},
        {"date": "2024-01-01", "value": 100, "category": "B"},
        {"date": "2024-02-01", "value": 5, "category": "B"},
    ]
    result = EndpointLabelFeature().apply(
        _spec("line"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert result.endpoint_label_data is not None
    positions = dict(result.endpoint_label_data.positions)
    # Raw last-x values, not cumulative/stacked math.
    assert positions == {"A": 20.0, "B": 5.0}


# ---------------------------------------------------------------------------
# HIGH regression: BaselineFeature misses FormatConfig percent format
# ---------------------------------------------------------------------------


def test_baseline_unity_rule_fires_for_format_config_percent(
    area_style: ResolvedAreaStyle,
) -> None:
    """Unity rule must fire for a percent spec authored as a FormatConfig."""
    chart = ResolvedAreaChart(
        panel_axes=(),
        id="a1",
        chart_type="area",
        x="date",
        y="pct",
        format=ResolvedFormat(spec=".0%", raw=".0%"),
        style=area_style,
        **_C,
    )
    # Data reaches 1.0 so the unity rule's domain gate passes; this test isolates
    # FormatConfig percent detection.
    data = [{"date": "2024-01", "pct": 0.5}, {"date": "2024-02", "pct": 1.1}]
    result = BaselineFeature().apply(
        _spec("area"), chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    unity = [
        layer
        for layer in result.layers
        if layer.encoding.get("y", {}).get("datum") == 1
    ]
    assert len(unity) == 1, (
        "unity rule must fire for FormatConfig(spec='.0%'); "
        "isinstance(fmt, str) gate misses FormatConfig"
    )
    rule = unity[0]
    assert rule.encoding.get("y", {}).get("datum") == 1
    assert "x" in rule.encoding and "x2" in rule.encoding
    assert "color" in rule.mark_props and "strokeWidth" in rule.mark_props


# ---------------------------------------------------------------------------
# HIGH regression: HeatmapEmitter falls back to chart.color
# ---------------------------------------------------------------------------


def test_heatmap_emitter_color_ignores_chart_color_fallback() -> None:
    """HeatmapEmitter must not emit color encoding when resolved_channels has no color,
    even when chart.color is set."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.theme import RectMarkStyle
    from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
    from dbt_charts.core.compile.resolve.style.axis_cascade import (
        AxisOverrides,
        build_resolved_axis,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.render.chart.emitters.heatmap import HeatmapEmitter

    from ...conftest import fixture_chart_for_type

    _rcs = resolve_chart_style_context(get_theme_style())
    _hm_baked = _bake_cartesian_axes(
        _rcs,
        fixture_chart_for_type("heatmap"),
        "heatmap",
        "nominal",
        "nominal",
        AxisOverrides(),
    )
    _hm_ax_merged, _hm_ay_merged, _hm_ax_band_position, _hm_ay_band_position = (
        _hm_baked.x.style,
        _hm_baked.y.style,
        _hm_baked.x.band_position,
        _hm_baked.y.band_position,
    )
    _hm_ax = build_resolved_axis(
        _hm_ax_merged,
        format=baked_format(_hm_ax_merged),
        band_position=_hm_ax_band_position,
        chart_id="test",
        formats=None,
    )
    _hm_ay = build_resolved_axis(
        _hm_ay_merged,
        format=baked_format(_hm_ay_merged),
        band_position=_hm_ay_band_position,
        chart_id="test",
        formats=None,
    )
    chart = ResolvedHeatmapChart(
        panel_axes=(),
        id="h1",
        chart_type="heatmap",
        x="col",
        y="row",
        color="metric",
        style=ResolvedHeatmapStyle(
            color_gradient=None,
            rect_mark=RectMarkStyle(),
            tooltip_format="",
            label_usable_ratio=0.8,
            axis_x=_hm_ax,
            axis_y=_hm_ay,
        ),
        **_C,
    )
    spec = HeatmapEmitter().emit(chart, _DEFAULT_BOX, regroup((), []))
    assert "color" not in spec.encoding, (
        "HeatmapEmitter fell back to chart.color when resolved_channels had no color; "
        "use resolved_channels exclusively like all other emitters"
    )


# ---------------------------------------------------------------------------
# HIGH regression: channel_to_encoding gradient uses domainMin/Max not domain
# ---------------------------------------------------------------------------


def test_channel_to_encoding_series_returns_field_encoding() -> None:
    """Series mode infers VL type from data — nominal for strings; no scale.range (palette in config)."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.render.chart.emitters._channels import channel_to_encoding

    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="category")
    enc = channel_to_encoding(ch, [{"category": "A"}])
    assert enc is not None
    assert enc == {
        "field": "category",
        "type": "nominal",
    }, f"series must return plain field encoding, got {enc!r}"
    assert "scale" not in enc, (
        "series must not embed scale.range — palette goes in config.range.category"
    )


# ---------------------------------------------------------------------------
# FIX-3: ClickInteractivityFeature
# ---------------------------------------------------------------------------


def test_click_interactivity_applies_to_chart_with_link(
    bar_style: ResolvedBarStyle,
) -> None:
    """ClickInteractivityFeature applies when chart.link is set."""
    from dbt_charts.core.render.chart.features.click_interactivity import (
        ClickInteractivityFeature,
    )

    chart = _bar(bar_style, link="https://example.com/{{ x }}")
    assert ClickInteractivityFeature().applies_to(chart)


def test_click_interactivity_does_not_apply_without_link(
    bar_style: ResolvedBarStyle,
) -> None:
    from dbt_charts.core.render.chart.features.click_interactivity import (
        ClickInteractivityFeature,
    )

    assert not ClickInteractivityFeature().applies_to(_bar(bar_style))


def test_click_interactivity_sets_href_link_on_spec(
    bar_style: ResolvedBarStyle,
) -> None:
    """apply() sets spec.href_link to a VL calc expression string."""
    from dbt_charts.core.render.chart.features.click_interactivity import (
        ClickInteractivityFeature,
    )

    chart = _bar(bar_style, link="https://example.com/{{ x }}")
    spec = _spec()
    spec.encoding["x"] = {"field": "month", "type": "ordinal"}
    result = ClickInteractivityFeature().apply(
        spec, chart, _DEFAULT_BOX, {chart.query_name: []}
    )
    assert result.href_link is not None, "href_link must be set after apply()"
    assert "datum" in result.href_link, (
        f"href_link must be a VL datum expression, got {result.href_link!r}"
    )


def test_click_interactivity_color_field_uses_raw_key_for_wide_measures(
    make_chart,
) -> None:
    """Regression: {{ color }} on a wide chart (y: [...]) resolved to
    color_ch.data_field, which is WIDE_LABEL_FIELD -- the fold's HUMANIZED
    measure name (default_axis_title), not the raw column name. Every
    wide-chart drill-down silently started writing display text
    ("revenue ($)") into a link variable whose downstream SQL compares
    against a real column name ("revenue_usd"), matching nothing. Mirrors
    the `y` branch's own wide-chart special case one line above."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.chart._wide_fields import WIDE_KEY_FIELD
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
    )
    from dbt_charts.core.render.chart.features.click_interactivity import (
        _channel_field,
    )

    chart = make_chart("bar", x="month", y=["revenue_usd", "units_sold"])
    rows = [{"month": "Jan", "revenue_usd": 10, "units_sold": 2}]
    context = resolve_chart_style_context(get_theme_style())
    resolved = resolve(chart, rows, chart_style_context=context)

    assert _channel_field(resolved, "color") == WIDE_KEY_FIELD


def test_click_interactivity_color_field_uses_the_dimension_for_wide_plus_color(
    make_chart,
) -> None:
    """Regression: a wide chart (y: [...]) that ALSO authors color: as a
    dimension (measures cross with it into composites) returned
    WIDE_KEY_FIELD unconditionally -- the fold's raw MEASURE key only, a
    plain column on every row that never carries the dimension. The
    dimension itself stays an ordinary, untouched column (the fold never
    renames or drops it), so {{ color }} there must read the dimension
    column, not the measure key, or the dimension -- the one part of the
    composite a filter could actually act on -- is gone from the URL
    entirely."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
    )
    from dbt_charts.core.render.chart.features.click_interactivity import (
        _channel_field,
    )

    chart = make_chart("bar", x="month", y=["revenue_usd", "cost"], color="region")
    rows = [{"month": "Jan", "revenue_usd": 10, "cost": 5, "region": "west"}]
    context = resolve_chart_style_context(get_theme_style())
    resolved = resolve(chart, rows, chart_style_context=context)

    assert _channel_field(resolved, "color") == "region"


def test_baseline_normalize_stacked_percent_area_dedupes_unity_rule(
    area_style: ResolvedAreaStyle,
) -> None:
    """Normalize-stacked area with % format: the 0% baseline still fires,
    and the 100% line fires exactly once (from the unity gate, not doubled
    up with the top-rule pair's own datum-1) — never the old duplicate-unity
    ``[0, 1, 1]`` triple.
    """
    chart = _area(area_style, stack="normalize", format=".0%")
    result = BaselineFeature().apply(
        _spec("area"), chart, _DEFAULT_BOX, {chart.query_name: []}
    )
    datums = sorted(layer.encoding.get("y", {}).get("datum") for layer in result.layers)
    assert datums == [0, 1]
    for rule in result.layers:
        assert "color" in rule.mark_props and "strokeWidth" in rule.mark_props


def test_baseline_normalize_stacked_percent_bar_dedupes_unity_rule(
    bar_style: ResolvedBarStyle,
) -> None:
    """Normalize-stacked bar with % format: mirrors the area case above --
    the 0% baseline fires and the 100% line fires exactly once.
    """
    ax, ay = _baked_axes_for("bar")
    chart = ResolvedBarChart(
        panel_axes=(),
        id="b",
        chart_type="bar",
        x="month",
        y="revenue",
        stack="normalize",
        format=".0%",
        orientation="vertical",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )
    result = BaselineFeature().apply(
        _spec("bar"), chart, _DEFAULT_BOX, {chart.query_name: []}
    )
    datums = sorted(layer.encoding["y"]["datum"] for layer in result.layers)
    assert datums == [0, 1]


def test_channel_to_encoding_gradient_both_bounds_uses_domain_array() -> None:
    """When gradient has both min and max, scale must use domain=[min,max] (not
    separate domainMin/domainMax) to match v1 _resolved_channel_to_vl behavior."""
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.compile.models.primitives import ScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import channel_to_encoding

    scale = ScaleTargetConfig(palette=["#000", "#fff"], min=0.0, max=100.0)
    ch = ResolvedStyleChannel(
        channel="color", mode="gradient", data_field="metric", scale=scale
    )
    enc = channel_to_encoding(ch, [])
    assert enc is not None
    assert "domain" in enc["scale"], (
        "gradient with both min and max must use scale.domain=[min,max] "
        "matching v1 _resolved_channel_to_vl; got domainMin/domainMax instead"
    )
    assert enc["scale"]["domain"] == [0.0, 100.0]
    assert "domainMin" not in enc["scale"]
    assert "domainMax" not in enc["scale"]


# ---------------------------------------------------------------------------
# Regression: board_style dropped from feature apply()
# ---------------------------------------------------------------------------


def _baked_bar(bar_style: ResolvedBarStyle) -> ResolvedBarChart:
    """Bar chart with resolved_axis_x/y baked from the default cascade."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
    from dbt_charts.core.compile.resolve.style.axis_cascade import (
        AxisOverrides,
        build_resolved_axis,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    from ...conftest import fixture_chart_for_type

    rcs = resolve_chart_style_context(get_theme_style())
    baked = _bake_cartesian_axes(
        rcs,
        fixture_chart_for_type("bar"),
        "bar",
        "nominal",
        "quantitative",
        AxisOverrides(),
    )
    ax_merged, ay_merged, ax_band_position, ay_band_position = (
        baked.x.style,
        baked.y.style,
        baked.x.band_position,
        baked.y.band_position,
    )

    ax = build_resolved_axis(
        ax_merged,
        format=baked_format(ax_merged),
        band_position=ax_band_position,
        chart_id="test",
        formats=None,
    )
    ay = build_resolved_axis(
        ay_merged,
        format=baked_format(ay_merged),
        band_position=ay_band_position,
        chart_id="test",
        formats=None,
    )
    return ResolvedBarChart(
        panel_axes=(),
        id="b1",
        chart_type="bar",
        x="month",
        y="revenue",
        orientation="vertical",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )


def test_zero_baseline_apply_no_board_style(bar_style: ResolvedBarStyle) -> None:
    """BaselineFeature.apply() takes 3 args — no board_style."""
    chart = _baked_bar(bar_style)
    spec = _spec("bar")
    data = [{"month": "Jan", "revenue": 100}]
    result = BaselineFeature().apply(
        spec, chart, _DEFAULT_BOX, {chart.query_name: data}
    )
    assert len(result.layers) == 1
    assert result.layers[0].mark == "rule"


# ---------------------------------------------------------------------------
# Z-order regression: rule layer must be inserted at V1-matching positions
# ---------------------------------------------------------------------------


def _baked_line(line_style: ResolvedLineStyle) -> ResolvedLineChart:
    ax, ay = _baked_axes_for("line")
    return ResolvedLineChart(
        panel_axes=(),
        id="ln",
        chart_type="line",
        x="date",
        y="value",
        style=line_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )


def _default_line_mark():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(
        get_theme_style(get_default_theme_name())
    ).chart_defaults.line.marks.line


def _default_point_mark():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(
        get_theme_style(get_default_theme_name())
    ).chart_defaults.line.marks.point


def _baked_area(area_style: ResolvedAreaStyle) -> ResolvedAreaChart:
    ax, ay = _baked_axes_for("area")
    return ResolvedAreaChart(
        panel_axes=(),
        id="ar",
        chart_type="area",
        x="date",
        y="value",
        style=area_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )


def _baked_bar_chart(bar_style: ResolvedBarStyle) -> ResolvedBarChart:
    ax, ay = _baked_axes_for("bar")
    return ResolvedBarChart(
        panel_axes=(),
        id="b1",
        chart_type="bar",
        x="month",
        y="revenue",
        orientation="vertical",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )


def test_zero_baseline_line_rule_is_first_layer(line_style: ResolvedLineStyle) -> None:
    """V1 inserts the zero rule BEFORE all line/point layers.

    V1: spec["layer"] = [*rules, *spec["layer"]]
    V2 must match: rule at index 0, not appended last.
    """
    from dbt_charts.core.render.chart.emitters import get_emitter

    chart = _baked_line(line_style)
    data = [{"date": "2024-01", "value": 10.0}, {"date": "2024-02", "value": 20.0}]
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), data))
    result = BaselineFeature().apply(
        spec, chart, _DEFAULT_BOX, {chart.query_name: data}
    )

    marks = [layer.mark for layer in result.layers]
    assert "rule" in marks, f"rule layer missing; layers={marks}"
    rule_idx = marks.index("rule")
    assert rule_idx == 0, (
        f"line: rule must be FIRST (index 0) to render below strokes; "
        f"got index {rule_idx} in {marks}"
    )


def test_zero_baseline_area_rule_after_last_area_layer(
    area_style: ResolvedAreaStyle,
) -> None:
    """V1 inserts the zero rule after the last 'area' sub-layer.

    V1: area order is halo_fill, halo_stroke, fg_fill, zero_rule, fg_stroke, hover.
    The rule must not be last — it sits between the fill and the foreground stroke.
    """
    from dbt_charts.core.render.chart.emitters import get_emitter

    chart = _baked_area(area_style)
    data = [{"date": "2024-01", "value": 10.0}, {"date": "2024-02", "value": 20.0}]
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), data))
    result = BaselineFeature().apply(
        spec, chart, _DEFAULT_BOX, {chart.query_name: data}
    )

    marks = [layer.mark for layer in result.layers]
    assert "rule" in marks, f"rule layer missing; layers={marks}"
    rule_idx = marks.index("rule")

    # Rule must NOT be last — there must be at least one non-rule layer after it.
    assert rule_idx < len(marks) - 1, (
        f"area: rule must not be last; got index {rule_idx} in {marks}. "
        f"Expected: after last 'area' fill, before foreground line/point layers."
    )

    # Rule must be after the last 'area' mark.
    last_area_idx = max(i for i, m in enumerate(marks) if m == "area")
    assert rule_idx > last_area_idx, (
        f"area: rule (idx={rule_idx}) must be AFTER last area fill (idx={last_area_idx}); "
        f"layers={marks}"
    )


def test_zero_baseline_bar_rule_is_last_layer(bar_style: ResolvedBarStyle) -> None:
    """V1 appends the zero rule AFTER all bar layers (on top of fills).

    Bar must stay rule-LAST; this test guards against regressions from the fix.
    """
    from dbt_charts.core.render.chart.emitters import get_emitter

    chart = _baked_bar_chart(bar_style)
    data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), data))
    result = BaselineFeature().apply(
        spec, chart, _DEFAULT_BOX, {chart.query_name: data}
    )

    marks = [layer.mark for layer in result.layers]
    assert marks[-1] == "rule", (
        f"bar: rule must be LAST (appended on top of fills); got {marks}"
    )


def test_zero_baseline_area_with_bar_layer_rule_renders_on_top(
    area_style: ResolvedAreaStyle,
) -> None:
    """An area base with a `chart.layers` bar overlay wraps the base's own
    composite sub-layers into ONE outer entry (render_cartesian_overlay), so
    `spec.layers` is `[base_composite, bar_overlay]` — not the flat area
    sub-layer list the bare-area branch assumes. The rule must still render
    on top of BOTH the area fill/stroke AND the bar overlay: append last.

    Regression: the bare-area "insert after last top-level area mark" logic
    found no top-level `area` mark in this wrapped shape and fell into a
    fallback that inserted the rule one position before the LAST layer —
    i.e. before the bar overlay, so the bar painted over the rule."""
    from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedBarLayer
    from dbt_charts.core.compile.models.style.resolved import ResolvedBarMarkStyle
    from dbt_charts.core.render.chart.emitters import get_emitter

    chart = _baked_area(area_style).model_copy(
        update={
            "layers": (
                ResolvedBarLayer(
                    type="bar", bar_mark=ResolvedBarMarkStyle(), y="actual"
                ),
            )
        }
    )
    data = [
        {"date": "2024-01", "value": 10.0, "actual": 8.0},
        {"date": "2024-02", "value": 20.0, "actual": 15.0},
    ]
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), data))
    result = BaselineFeature().apply(
        spec, chart, _DEFAULT_BOX, {chart.query_name: data}
    )

    marks = [layer.mark for layer in result.layers]
    assert marks[-1] == "rule", (
        f"area+bar-layer: rule must be LAST — on top of both the area base "
        f"and the bar overlay; got {marks}"
    )


def test_zero_baseline_fires_for_area_with_bar_layer_despite_scale_zero_false(
    area_style: ResolvedAreaStyle,
) -> None:
    """The zero rule must fire whenever a `chart.layers` bar overlay is
    present, even if the base series' OWN values never approach 0 and its
    axis has `scale.zero: False`. A bar overlay always renders zero-anchored
    on the SAME shared y scale, so 0 is visually on the axis regardless of
    the base series' range — the straddle check that gates a bare area/line
    must not suppress the rule just because it only looked at the base's own
    data.

    Regression: a target series sitting at 30-55 (never near 0) with
    scale.zero=False suppressed the rule entirely, even though the bar
    layer's own values (20-60, zero-anchored bars) put 0 squarely on the
    rendered axis — "0 is clearly on the axis" but no rule ever fired."""
    from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedBarLayer
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedBarMarkStyle,
        ResolvedScaleContinuousStyle,
        ResolvedScaleStyle,
    )
    from dbt_charts.core.render.chart.emitters import get_emitter

    base_chart = _baked_area(area_style)
    zeroless_axis_y = dataclasses.replace(
        base_chart.style.axis_y,
        scale=ResolvedScaleStyle(continuous=ResolvedScaleContinuousStyle(zero=False)),
    )
    chart = base_chart.model_copy(
        update={
            "style": base_chart.style.model_copy(update={"axis_y": zeroless_axis_y}),
            "layers": (
                ResolvedBarLayer(
                    type="bar", bar_mark=ResolvedBarMarkStyle(), y="actual"
                ),
            ),
        }
    )
    # Base series (value) never approaches 0; only the bar layer's data does.
    data = [
        {"date": "2024-01", "value": 32.0, "actual": 30.0},
        {"date": "2024-02", "value": 55.0, "actual": 20.0},
    ]
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), data))
    result = BaselineFeature().apply(
        spec, chart, _DEFAULT_BOX, {chart.query_name: data}
    )

    marks = [layer.mark for layer in result.layers]
    assert "rule" in marks, (
        f"zero rule must fire when a bar layer is present (bars always "
        f"zero-anchor on the shared scale) even though the base series' own "
        f"values never approach 0 and scale.zero=False; got {marks}"
    )


def test_zero_baseline_fires_for_non_bar_layer_whose_own_data_straddles_zero(
    area_style: ResolvedAreaStyle,
) -> None:
    """A non-bar overlay layer's OWN data domain must be checked too — not
    just "is there a bar layer". A line-type layer with its own `query:`
    (separate data straddling 0) shares the base's y scale, so 0 is
    genuinely on the rendered axis even though neither the base series nor
    the bypass-only-for-bar heuristic would have caught it."""
    from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLineLayer
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedScaleContinuousStyle,
        ResolvedScaleStyle,
    )
    from dbt_charts.core.render.chart.emitters import get_emitter

    base_chart = _baked_area(area_style)
    zeroless_axis_y = dataclasses.replace(
        base_chart.style.axis_y,
        scale=ResolvedScaleStyle(continuous=ResolvedScaleContinuousStyle(zero=False)),
    )
    chart = base_chart.model_copy(
        update={
            "style": base_chart.style.model_copy(update={"axis_y": zeroless_axis_y}),
            "layers": (
                ResolvedLineLayer(
                    type="line",
                    line_mark=_default_line_mark(),
                    point_mark=_default_point_mark(),
                    y="delta",
                    query_name="deltas",
                ),
            ),
        }
    )
    # Base series (value) never approaches 0.
    data = [
        {"date": "2024-01", "value": 32.0},
        {"date": "2024-02", "value": 55.0},
    ]
    # The line layer's own query data straddles 0.
    datasets = {
        chart.query_name: data,
        "deltas": [
            {"date": "2024-01", "delta": -5.0},
            {"date": "2024-02", "delta": 8.0},
        ],
    }
    spec = get_emitter(chart).emit(
        chart, _DEFAULT_BOX, regroup((), data), datasets=datasets
    )
    result = BaselineFeature().apply(spec, chart, _DEFAULT_BOX, datasets)

    marks = [layer.mark for layer in result.layers]
    assert "rule" in marks, (
        f"zero rule must fire when a non-bar overlay layer's own data "
        f"straddles 0, even with scale.zero=False and a base series that "
        f"never approaches 0; got {marks}"
    )


def test_zero_baseline_stays_suppressed_when_no_layer_reaches_zero(
    area_style: ResolvedAreaStyle,
) -> None:
    """scale.zero=False with a base series AND every overlay layer staying
    well clear of 0 must still suppress the rule — the fix broadens the
    straddle check to cover layers, it must not regress to "any layer at all
    forces the rule on"."""
    from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLineLayer
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedScaleContinuousStyle,
        ResolvedScaleStyle,
    )
    from dbt_charts.core.render.chart.emitters import get_emitter

    base_chart = _baked_area(area_style)
    zeroless_axis_y = dataclasses.replace(
        base_chart.style.axis_y,
        scale=ResolvedScaleStyle(continuous=ResolvedScaleContinuousStyle(zero=False)),
    )
    chart = base_chart.model_copy(
        update={
            "style": base_chart.style.model_copy(update={"axis_y": zeroless_axis_y}),
            "layers": (
                ResolvedLineLayer(
                    type="line",
                    line_mark=_default_line_mark(),
                    point_mark=_default_point_mark(),
                    y="target",
                ),
            ),
        }
    )
    data = [
        {"date": "2024-01", "value": 32.0, "target": 40.0},
        {"date": "2024-02", "value": 55.0, "target": 60.0},
    ]
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), data))
    result = BaselineFeature().apply(
        spec, chart, _DEFAULT_BOX, {chart.query_name: data}
    )

    marks = [layer.mark for layer in result.layers]
    assert "rule" not in marks, (
        f"zero rule must stay suppressed when neither the base series nor "
        f"any overlay layer's data reaches 0; got {marks}"
    )


def test_value_label_feature_dispatch_table_covers_all_supported_families() -> None:
    assert set(ValueLabelFeature._HANDLERS) == {
        "bar",
        "histogram",
        "line",
        "area",
        "scatter",
    }


def test_dashes_with_empty_chart_local_palette_and_board_category_colors_does_not_raise(
    line_style: ResolvedLineStyle,
) -> None:
    """Regression: `is_bindable_series` has no `and chart.palette` gate,
    so resolution runs on an empty palette too, which lets `scale` be set
    from a board-wide `category_colors` binding regardless of the chart's
    own palette. The `style.dashes` branch's `color_at(scale, v,
    chart.palette)` call then indexes an EMPTY palette and raises
    "palette has only 0 swatches" -- a board that rendered before now
    shows an error card. Guarded the same way the other empty-palette
    sites are."""
    from dbt_charts.core.render.chart.emitters.line import LineEmitter

    chart = ResolvedLineChart(
        panel_axes=(),
        id="line1",
        chart_type="line",
        x="month",
        y="revenue",
        resolved_channels={
            "color": ResolvedStyleChannel(
                channel="color", mode="series", data_field="region"
            )
        },
        variable_dependencies=frozenset(),
        palette=(),
        category_colors=(
            CategoryColorScale(
                field="region", slots={"north": 0, "south": 1}, overrides={}
            ),
        ),
        style=line_style.model_copy(update={"dashes": [[4, 4], [8, 8]]}),
        legend=_default_legend(),
        background=_DEFAULT_CHARTS.background,
        canvas=_DEFAULT_CHARTS.ink_canvas,
        title_style=_DEFAULT_CHARTS.title,
        layout_padding=_ZERO_PADDING,
    )
    data = [
        {"month": "Jan", "revenue": 100, "region": "north"},
        {"month": "Jan", "revenue": 80, "region": "south"},
        {"month": "Feb", "revenue": 120, "region": "north"},
        {"month": "Feb", "revenue": 90, "region": "south"},
    ]

    spec = LineEmitter().emit(chart, _DEFAULT_BOX, regroup((), data))

    color_scale = spec.encoding["color"].get("scale", {})
    assert "range" not in color_scale


def test_baseline_normalize_stacked_grid_not_visible_emits_no_rules(
    area_style: ResolvedAreaStyle,
) -> None:
    """A normalize stack with `axis_y.grid.visible: false` emits no rules.

    The 0%/100% pair frames a normalize stack rather than decorating it, so the
    block used to emit it unconditionally; deferring to the blanket grid switch
    is new behavior. Without this test the `grid.visible` term can be deleted
    and the suite stays green — every other `visible=False` case is a
    non-normalize chart, and the normalize off-switch case uses
    `threshold.visible` instead.
    """
    import dataclasses

    # grid/axis are frozen dataclasses; the family style is a pydantic model.
    hidden_grid = dataclasses.replace(area_style.axis_y.grid, visible=False)
    hidden_axis = dataclasses.replace(area_style.axis_y, grid=hidden_grid)
    style = area_style.model_copy(update={"axis_y": hidden_axis})
    chart = _area(style, stack="normalize")
    result = BaselineFeature().apply(
        _spec("area"), chart, _DEFAULT_BOX, {chart.query_name: []}
    )
    assert result.layers == []
