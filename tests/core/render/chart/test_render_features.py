"""Tests for render-v2 ChartFeature implementations — both unit and pipeline integration.

Feature unit tests (applies_to / apply) pass against the already-implemented
features modules.  The integration tests below call the full pipeline:

    emitter.emit() → FeaturePipeline([feature]).apply() → ChartSpec
"""

from __future__ import annotations

from typing import Any, Literal

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedBarChart,
    ResolvedBarStyle,
    ResolvedStyleChannel,
)
from dbt_charts.core.compile.models.style.theme import PaddingStyle
from dbt_charts.core.compile.resolve.style.typography import concrete_title_style
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox

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
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


_DEFAULT_CHARTS = _default_charts()
_ZERO_PADDING = PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0)
_B: dict[str, Any] = {
    "variable_dependencies": frozenset(),
    "palette": (),
    "resolved_channels": {},
    "legend": _default_legend(),
    "background": _DEFAULT_CHARTS.background,
    "canvas": _DEFAULT_CHARTS.ink_canvas,
    "title_style": concrete_title_style(_DEFAULT_CHARTS),
    "layout_padding": _ZERO_PADDING,
}
_C: dict[str, Any] = dict(_B)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
        title_style=concrete_title_style(_DEFAULT_CHARTS),
        layout_padding=_ZERO_PADDING,
        field_labels={},
    )


# ---------------------------------------------------------------------------
# BaselineFeature — applies_to (unit)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# BaselineFeature — apply (unit)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# BaselineFeature — top/unity behavior (unit)
#
# applies_to() is now a single broad predicate (isinstance over the four
# families) shared by zero/top/unity — these tests exercise the local
# top-vs-unity decision inside apply() instead of a per-rule applies_to gate.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# BaselineFeature — apply emits real rule mark
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# EndpointLabelFeature — applies_to (unit)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# EndpointLabelFeature — apply (unit)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# HIGH regression: BaselineFeature misses FormatConfig percent format
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# HIGH regression: HeatmapEmitter falls back to chart.color
# ---------------------------------------------------------------------------


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
    measure name (field_title), not the raw column name. Every
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


# ---------------------------------------------------------------------------
# Z-order regression: rule layer must be inserted at V1-matching positions
# ---------------------------------------------------------------------------
