"""Tests for grouped column chart default behavior.

When a bar chart has a color field and stack is not authored (None), the
renderer defaults to side-by-side grouped columns via xOffset (vertical) or
yOffset (horizontal) instead of VL's stacked default.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import (
    get_chart_rendering,
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
from dbt_charts.core.compile.models.chart.normalized import BarChart, Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    BarChartStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

from .conftest import chart_pane

_RESOLVED_STYLE, _BOARD_STYLE = resolve_style_and_context(get_theme_style())


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _chart(
    *, stack=None, orientation: str = "vertical", color="region", overlap=None, **kwargs
) -> Chart:
    return BarChart(
        id="test_bar",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="bar",
        x="category",
        y="revenue",
        color=color,
        stack=stack,
        style=BarChartStylePatch(orientation=orientation, overlap=overlap),
        **kwargs,
    )


MULTI_SERIES_DATA = [
    {"category": "Alpha", "revenue": 100, "region": "North"},
    {"category": "Alpha", "revenue": 80, "region": "South"},
    {"category": "Beta", "revenue": 120, "region": "North"},
    {"category": "Beta", "revenue": 90, "region": "South"},
]

SINGLE_SERIES_DATA = [
    {"category": "Alpha", "revenue": 100},
    {"category": "Beta", "revenue": 200},
]


THREE_SERIES_DATA = [
    {"category": "Alpha", "revenue": 100, "region": "North"},
    {"category": "Alpha", "revenue": 80, "region": "South"},
    {"category": "Alpha", "revenue": 60, "region": "West"},
    {"category": "Beta", "revenue": 120, "region": "North"},
    {"category": "Beta", "revenue": 90, "region": "South"},
    {"category": "Beta", "revenue": 70, "region": "West"},
]


NUMERIC_X_DATA = [
    {"x_num": 1, "revenue": 100, "region": "North"},
    {"x_num": 1, "revenue": 80, "region": "South"},
    {"x_num": 2, "revenue": 120, "region": "North"},
    {"x_num": 2, "revenue": 90, "region": "South"},
]


def _get_encoding(spec: dict) -> dict:
    """Return encoding from a single-layer or layered spec.

    Endpoint labels may have wrapped the chart in hconcat/vconcat — unwrap to
    the real chart pane first (chart_pane() is a no-op otherwise).
    """
    spec = chart_pane(spec)
    if "encoding" in spec:
        return spec["encoding"]
    layers = spec.get("layer", [])
    return layers[0].get("encoding", {}) if layers else {}


def _get_mark(spec: dict) -> dict:
    """Return the bar mark dict from a single-layer or layered spec."""
    spec = chart_pane(spec)
    if "mark" in spec:
        return spec["mark"]
    for layer in spec.get("layer", []):
        mark = layer.get("mark", {})
        if isinstance(mark, dict) and mark.get("type") == "bar":
            return mark
    return {}


class TestGroupedBarOverlap:
    """style.bar.overlap controls within-group bar spacing.

    Gap/touch (fraction ≤ 0) and vertical overlap emit a band-relative bar width;
    horizontal overlap emits a ``bandwidth('yOffset')`` size expression (Vega
    clamps height band > 1, so the band shorthand can't overlap horizontal
    bars — the expression form isn't clamped and needs no panel dimensions).
    Positive overlap is restricted to exactly 2 series.
    """

    def _spec(self, data, *, width=None, height=None, **kw):
        return generate_vega_lite_spec(
            _chart(**kw),
            data,
            width=width,
            height=height,
        )

    def test_wide_by_dimension_counts_composites(self):
        """``y: [a, b]`` + ``color:`` with two values is four series, not
        two: positive overlap is refused, as it is for any four-series group."""
        chart = BarChart(
            id="test_bar",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="bar",
            x="category",
            y=["revenue", "cost"],
            color="region",
            stack="none",
            style=BarChartStylePatch(orientation="vertical", overlap=0.5),
        )
        data = [dict(row, cost=row["revenue"] // 2) for row in MULTI_SERIES_DATA]
        with pytest.raises(ChartDataError, match="exactly 2 series \\(got 4\\)"):
            generate_vega_lite_spec(chart, data)

    def test_auto_two_series_uses_partial_band(self):
        # auto + 2 series, vertical → partial (0.25) → width band 1.25.
        spec = self._spec(MULTI_SERIES_DATA)
        assert "xOffset" in _get_encoding(spec)
        assert _get_mark(spec)["width"]["band"] == pytest.approx(1.25)

    def test_auto_three_series_uses_no_overlap_gap(self):
        # auto + 3 series → none (-0.1) → width band 0.9 (a gap); no error.
        spec = self._spec(THREE_SERIES_DATA)
        assert _get_mark(spec)["width"]["band"] == pytest.approx(0.9)

    def test_flush_touches_via_band(self):
        spec = self._spec(MULTI_SERIES_DATA, overlap="flush")
        assert _get_mark(spec)["width"]["band"] == pytest.approx(1.0)

    def test_full_drops_offset_to_coincide(self):
        spec = self._spec(MULTI_SERIES_DATA, overlap="full")
        assert "xOffset" not in _get_encoding(spec)

    def test_above_one_clamps_to_full(self):
        # 1 is the maximum: a fraction >1 coincides exactly like 'full' (bars
        # never cross past each other), so the offset is dropped, not amplified.
        spec = self._spec(MULTI_SERIES_DATA, overlap=1.5)
        assert "xOffset" not in _get_encoding(spec)

    def test_vertical_numeric_overlap_uses_band(self):
        spec = self._spec(MULTI_SERIES_DATA, overlap=0.5)
        assert _get_mark(spec)["width"]["band"] == pytest.approx(1.5)

    def test_horizontal_two_series_overlap_uses_bandwidth_expression(self):
        # Horizontal overlap needs an explicit size (height band clamps at 1).
        # bandwidth('yOffset') is Vega's own resolved per-series offset-scale
        # width — no panel dimension needed, no chrome guess.
        spec = self._spec(
            MULTI_SERIES_DATA, orientation="horizontal", overlap="partial"
        )
        assert "yOffset" in _get_encoding(spec)
        mark = _get_mark(spec)
        assert mark.get("size") == {"expr": "1.25 * bandwidth('yOffset')"}
        assert "height" not in mark

    def test_horizontal_overlap_ignores_panel_dimensions(self):
        # The bandwidth expression is resolved by Vega at its own layout time,
        # so passing (or omitting) width/height must not change the spec.
        with_dims = self._spec(
            MULTI_SERIES_DATA,
            orientation="horizontal",
            overlap="partial",
            width=400.0,
            height=300.0,
        )
        without_dims = self._spec(
            MULTI_SERIES_DATA, orientation="horizontal", overlap="partial"
        )
        assert _get_mark(with_dims)["size"] == _get_mark(without_dims)["size"]

    def test_horizontal_overlap_renders_nonzero_bar_thickness(self):
        """Render the bandwidth() expression through vl-convert end to end.

        The spec-level tests above pin the expression string, but its
        correctness hinges entirely on Vega naming the offset scale
        'yOffset' at expression-eval time — an external dependency a renamed
        or re-scoped scale would silently break (every bar would collapse to
        zero height) while the unit tests above stayed green. Render for
        real and assert the bars have measurable, non-degenerate thickness.
        """
        try:
            import vl_convert as vlc  # noqa: F401 — skip marker
        except ImportError:
            pytest.skip("vl_convert not installed")

        import re

        from dbt_charts.core.render.converters.chart import render_vega_spec

        chart = _chart(orientation="horizontal", overlap="partial")
        resolve(chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, MULTI_SERIES_DATA)
        svg = render_vega_spec(
            spec,
            "svg",
            _RESOLVED_STYLE,
            width=400.0,
            height=300.0,
            is_placeholder=False,
            chart_id="chart",
        )
        mark_group = re.search(
            r'<g class="mark-rect[^"]*"[^>]*>(.*?)</g>', svg, re.DOTALL
        )
        assert mark_group, "no bar mark group found in rendered SVG"
        thicknesses = [
            abs(float(v))
            for v in re.findall(
                r'd="M[0-9.\-]+,[0-9.\-]+h[0-9.\-]+v(-?[0-9.]+)h', mark_group.group(1)
            )
        ]
        assert thicknesses, "no bar path 'd' attributes matched — regex is stale"
        assert all(t > 1.0 for t in thicknesses), (
            f"bar thicknesses {thicknesses} include a near-zero bar — "
            "bandwidth('yOffset') resolved to ~0, the offset scale name assumption broke"
        )

    def test_three_series_positive_overlap_raises(self):
        with pytest.raises(ChartDataError, match="2 series"):
            generate_vega_lite_spec(
                _chart(overlap="partial"),
                THREE_SERIES_DATA,
            )

    def test_single_series_default_does_not_raise(self):
        # Regression: a bar with color encoding where every x maps to the same
        # color value (1:1, single series) must not raise. xOffset is suppressed
        # because color is 1:1 with x — the grouped-bar overlap feature never
        # fires, and the 2-series guard is never reached.
        single_color = [
            {"category": "Alpha", "revenue": 100, "region": "North"},
            {"category": "Beta", "revenue": 120, "region": "North"},
        ]
        spec = self._spec(single_color)  # default overlap=auto
        # xOffset is suppressed: both categories map to the same color "North"
        # (1:1 with x), so full-width bars are emitted, no grouped layout.
        assert "xOffset" not in _get_encoding(spec)


class TestGroupedColumnsDefault:
    """stack="none" (theme default) + color → grouped (xOffset for vertical, yOffset for horizontal)."""

    def test_vertical_bar_with_color_emits_xoffset(self):
        chart = _chart()
        resolve(chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, MULTI_SERIES_DATA)
        enc = _get_encoding(spec)
        assert "xOffset" in enc, (
            "vertical bar with color and no stack should emit xOffset"
        )
        assert enc["xOffset"]["field"] == "region"

    def test_horizontal_bar_with_color_emits_yoffset(self):
        chart = _chart(orientation="horizontal")
        resolve(chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, MULTI_SERIES_DATA)
        enc = _get_encoding(spec)
        assert "yOffset" in enc, (
            "horizontal bar with color and no stack should emit yOffset"
        )
        assert enc["yOffset"]["field"] == "region"

    def test_no_offset_without_color(self):
        chart = BarChart(
            id="test_bar_no_color",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="bar",
            x="category",
            y="revenue",
            style=BarChartStylePatch(orientation="vertical"),
        )
        resolve(chart, SINGLE_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, SINGLE_SERIES_DATA)
        enc = _get_encoding(spec)
        assert "xOffset" not in enc
        assert "yOffset" not in enc


class TestExplicitStackOverridesGroupedDefault:
    """Stacking modes zero/normalize/center override the grouped (side-by-side) default."""

    def test_stack_zero_produces_stacked_not_grouped(self):
        chart = _chart(stack="zero")
        resolve(chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, MULTI_SERIES_DATA)
        enc = _get_encoding(spec)
        assert "xOffset" not in enc
        assert "yOffset" not in enc

    def test_stack_normalize_produces_normalized_not_grouped(self):
        chart = _chart(stack="normalize")
        resolve(chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, MULTI_SERIES_DATA)
        enc = _get_encoding(spec)
        assert "xOffset" not in enc
        assert "yOffset" not in enc


MIXED_SIGN_DATA = [
    {"category": "Alpha", "revenue": -50, "region": "North"},
    {"category": "Alpha", "revenue": 80, "region": "South"},
    {"category": "Beta", "revenue": 120, "region": "North"},
    {"category": "Beta", "revenue": -30, "region": "South"},
]


class TestGroupedBarZeroRuleOffset:
    """Zero-baseline rule must not inherit the grouped-bar xOffset/yOffset encoding."""

    def _get_zero_rule_layer(self, spec: dict) -> dict | None:
        for layer in spec.get("layer", []):
            mark = layer.get("mark", {})
            if isinstance(mark, dict) and mark.get("type") == "rule":
                enc = layer.get("encoding", {})
                if "y" in enc and enc["y"].get("datum") == 0:
                    return layer
        return None

    def test_vertical_grouped_zero_rule_overrides_xoffset(self):
        """Zero rule layer must override xOffset to 0 so it spans the full grid line."""
        chart = _chart()
        resolve(chart, MIXED_SIGN_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, MIXED_SIGN_DATA)
        assert "layer" in spec, "zero rule should convert spec to layered"
        rule = self._get_zero_rule_layer(spec)
        assert rule is not None, "zero rule layer not found"
        enc = rule.get("encoding", {})
        assert "xOffset" in enc, (
            "zero rule must override xOffset to prevent inheritance"
        )
        assert enc["xOffset"] == {"value": 0}, (
            f"zero rule xOffset must be {{value: 0}}, got {enc['xOffset']}"
        )

    def test_horizontal_grouped_zero_rule_overrides_yoffset(self):
        """Horizontal grouped bar zero rule must override yOffset to 0."""
        chart = _chart(orientation="horizontal")
        resolve(chart, MIXED_SIGN_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, MIXED_SIGN_DATA)
        assert "layer" in spec
        # For horizontal bar the zero rule encodes x (the measure axis)
        rule = None
        for layer in spec.get("layer", []):
            mark = layer.get("mark", {})
            if isinstance(mark, dict) and mark.get("type") == "rule":
                enc = layer.get("encoding", {})
                if "x" in enc and enc["x"].get("datum") == 0:
                    rule = layer
                    break
        assert rule is not None, "horizontal zero rule layer not found"
        enc = rule.get("encoding", {})
        assert "yOffset" in enc, "horizontal zero rule must override yOffset"
        assert enc["yOffset"] == {"value": 0}


class TestGroupedBarScalePadding:
    """Grouped bar charts need inner+outer padding for visual separation."""

    def test_vertical_grouped_bar_x_scale_padding(self):
        """Grouped vertical bars: paddingInner creates group gaps, paddingOuter prevents edge overflow."""
        chart = _chart()
        resolve(chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        bar_cfg = get_chart_rendering().bar
        spec = generate_vega_lite_spec(chart, MULTI_SERIES_DATA)
        x_scale = spec.get("encoding", {}).get("x", {}).get("scale", {})
        assert x_scale.get("paddingInner") == bar_cfg.grouped_bar_padding_inner
        assert x_scale.get("paddingOuter") == bar_cfg.grouped_bar_padding_outer
        assert "padding" not in x_scale, (
            "grouped bar must not have padding shorthand — it overrides explicit paddingOuter in Vega"
        )

    def test_horizontal_grouped_bar_y_scale_padding(self) -> None:
        """Grouped horizontal bars: same padding contract on y scale."""
        bar_cfg = get_chart_rendering().bar
        chart = _chart(orientation="horizontal")
        resolve(chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, MULTI_SERIES_DATA)
        y_scale = spec.get("encoding", {}).get("y", {}).get("scale", {})
        assert y_scale.get("paddingInner") == bar_cfg.grouped_bar_padding_inner
        assert y_scale.get("paddingOuter") == bar_cfg.grouped_bar_padding_outer
        assert "padding" not in y_scale, "grouped bar must not have padding shorthand"

    def test_non_grouped_bar_uses_bar_padding_not_grouped_defaults(self):
        """Non-grouped single-series bar must not get forced grouped paddingInner/paddingOuter."""
        chart = BarChart(
            id="test_bar_no_color",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="bar",
            x="category",
            y="revenue",
            style=BarChartStylePatch(orientation="vertical"),
        )
        resolve(chart, SINGLE_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, SINGLE_SERIES_DATA)
        x_scale = spec.get("encoding", {}).get("x", {}).get("scale", {})
        assert x_scale.get("paddingOuter") is None, (
            "non-grouped bar must not have forced paddingOuter"
        )


class TestGroupedBarWithLayers:
    """Grouped-bar spacing and overlap must survive an authored `layers:` block.

    BarEmitter wraps the flat spec into a layered spec (render_cartesian_overlay)
    whenever chart.layers is non-empty; the grouped-bar padding + overlap must be
    applied to the flat spec before that wrap, or it silently never fires.
    """

    def _chart_with_line_layer(self, **kwargs) -> Chart:
        return _chart(layers=[LineLayer(type="line", y="target")], **kwargs)

    def _bar_scale(self, spec: dict, channel: str) -> dict:
        """Scale for `channel` as the bar mark sees it.

        A layered spec hoists the shared quantitative channel to the top level
        and leaves the categorical one on the bar layer, and which channel that
        is flips with orientation — so look at the bar layer first, then fall
        back to the top-level encoding VL would inherit from.
        """
        for layer in spec.get("layer", []):
            mark = layer.get("mark", {})
            if isinstance(mark, dict) and mark.get("type") == "bar":
                enc = layer.get("encoding", {})
                if channel in enc:
                    return enc[channel].get("scale", {})
        return spec.get("encoding", {}).get(channel, {}).get("scale", {})

    def test_layered_grouped_bar_x_scale_padding(self) -> None:
        """A grouped bar with a layers: line still gets the grouped-bar band padding."""
        chart = self._chart_with_line_layer()
        resolve(chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        bar_cfg = get_chart_rendering().bar
        spec = generate_vega_lite_spec(chart, MULTI_SERIES_DATA)
        x_scale = self._bar_scale(spec, "x")
        assert x_scale.get("paddingInner") == bar_cfg.grouped_bar_padding_inner
        assert x_scale.get("paddingOuter") == bar_cfg.grouped_bar_padding_outer
        assert "padding" not in x_scale, (
            "grouped bar must not have padding shorthand — it overrides explicit paddingOuter in Vega"
        )

    def test_layered_grouped_bar_overlap_differs_from_default(self) -> None:
        """style.overlap must not be a silent no-op once the chart has a layers: block.

        Both specs are layered, so mark.width lands as a bandwidth() expr
        (_fix_bar_band_width converts {"band": f} -> {"expr": "f * bandwidth(...)"});
        assert the two exprs differ rather than pinning either literal string.
        """
        default_chart = self._chart_with_line_layer()
        resolve(default_chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        default_spec = generate_vega_lite_spec(default_chart, MULTI_SERIES_DATA)

        none_chart = self._chart_with_line_layer(overlap="none")
        resolve(none_chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        none_spec = generate_vega_lite_spec(none_chart, MULTI_SERIES_DATA)

        default_width = _get_mark(default_spec)["width"]
        none_width = _get_mark(none_spec)["width"]
        assert default_width != none_width, (
            "overlap='none' must emit a different bar width than the default on a "
            f"layered grouped bar chart; both were {default_width!r}"
        )

    def test_layered_horizontal_grouped_bar_y_scale_padding(self) -> None:
        """The horizontal half takes a different route and needs its own gate.

        `_fix_bar_band_width` keys off `"xOffset" in encoding`, which is false
        for a horizontal grouped bar, so the rescue that carries the vertical
        band shorthand across the layer wrap never runs here. The band padding
        must still reach the categorical (y) scale.
        """
        chart = self._chart_with_line_layer(orientation="horizontal")
        resolve(chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        bar_cfg = get_chart_rendering().bar
        spec = generate_vega_lite_spec(chart, MULTI_SERIES_DATA)
        y_scale = self._bar_scale(spec, "y")
        assert y_scale.get("paddingInner") == bar_cfg.grouped_bar_padding_inner
        assert y_scale.get("paddingOuter") == bar_cfg.grouped_bar_padding_outer

    def test_layered_horizontal_grouped_bar_honors_overlap(self) -> None:
        """style.overlap must reach the horizontal layered path too.

        The 2-series default resolves to positive overlap, which on horizontal
        emits `mark.size` and drops `mark.height`; `overlap: none` keeps the
        band-relative height instead. Compare the whole mark so either channel
        moving counts.
        """
        default_chart = self._chart_with_line_layer(orientation="horizontal")
        resolve(default_chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        default_mark = _get_mark(
            generate_vega_lite_spec(default_chart, MULTI_SERIES_DATA)
        )

        none_chart = self._chart_with_line_layer(
            orientation="horizontal", overlap="none"
        )
        resolve(none_chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        none_mark = _get_mark(generate_vega_lite_spec(none_chart, MULTI_SERIES_DATA))

        default_size = (default_mark.get("size"), default_mark.get("height"))
        none_size = (none_mark.get("size"), none_mark.get("height"))
        assert default_size != none_size, (
            "overlap='none' must change the horizontal bar's size/height on a "
            f"layered grouped bar chart; both were {default_size!r}"
        )

    def test_layered_bar_authored_size_survives_step_curve_band_rewrite(self) -> None:
        """An authored `marks.bar.size` must survive `_fix_bar_band_width`'s
        rewrite even with NO color/xOffset grouping on the bar itself.

        A sibling layer's own step-curve adds an xOffset scale of its own
        (`step_band_present`, `_overlay.py`), which is enough on its own to
        make `_fix_bar_band_width` run against the bar mark despite the bar
        never carrying an xOffset channel — and `apply_grouped_bar_spacing`
        never fires here (no color channel), isolating `_fix_bar_band_width`
        itself as the only place the authored width could be lost.

        `bar_mark_to_vl` emits an authored `size` as a literal pixel float on
        ANY scale, band or continuous — "authored beats computed, always".
        `_fix_bar_band_width` used to treat any non-dict `width` as nothing to
        preserve and replace it wholesale with a bare `bandwidth('x')`
        expression, discarding both the authored width and the band gutter.
        """
        data = [
            {"category": "Alpha", "revenue": 100, "target": 90},
            {"category": "Beta", "revenue": 120, "target": 110},
        ]
        chart = BarChart(
            id="test_bar",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="bar",
            x="category",
            y="revenue",
            style=BarChartStylePatch.model_validate(
                {"orientation": "vertical", "marks": {"bar": {"size": 6.0}}}
            ),
            layers=[
                LineLayer.model_validate(
                    {
                        "type": "line",
                        "y": "target",
                        "style": {
                            "marks": {"line": {"curve": "step", "connect": False}}
                        },
                    }
                )
            ],
        )
        resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        width = _get_mark(spec)["width"]
        assert width == 6.0, (
            f"authored bar.size=6.0 must survive the step-curve-triggered "
            f"band-width rewrite verbatim, got {width!r}"
        )

    def test_layered_bar_null_band_width_still_gets_explicit_bandwidth(self) -> None:
        """A cleared `marks.bar.band_width` must still get the explicit
        `bandwidth('x')` expression when a step-curve layer degrades VL's
        width shorthand.

        `bar_mark_to_vl` emits NO width at all when both `size` and
        `band_width` are None, and a chart-local `marks.bar.band_width: null`
        genuinely clears the theme value through the style InheritSlot (the
        same escape hatch `..._survives_null_bar_size` exercises for `size`).
        VL's degraded-shorthand quirk applies to an absent width just as it
        does to a `{band: f}` one, so `_fix_bar_band_width` must rewrite this
        case rather than skip it — skipping collapses the bar to VL's ~18px
        internal default instead of the full band.

        Pins the absent-width arm of the guard that keeps a literal authored
        width untouched: narrowing that guard back to "any non-dict returns"
        silently shrinks these bars, and no other test can tell.
        """
        data = [
            {"category": "Alpha", "revenue": 100, "target": 90},
            {"category": "Beta", "revenue": 120, "target": 110},
        ]

        def build(with_layer: bool) -> BarChart:
            return BarChart(
                id="test_bar",
                query=SqlQuery(sql="SELECT 1", source="test"),
                query_name="q",
                type="bar",
                x="category",
                y="revenue",
                style=BarChartStylePatch.model_validate(
                    {
                        "orientation": "vertical",
                        "marks": {"bar": {"band_width": None}},
                    }
                ),
                layers=[
                    LineLayer.model_validate(
                        {
                            "type": "line",
                            "y": "target",
                            "style": {
                                "marks": {"line": {"curve": "step", "connect": False}}
                            },
                        }
                    )
                ]
                if with_layer
                else [],
            )

        layered = build(True)
        resolve(layered, data, chart_style_context=_BOARD_STYLE)
        layered_width = _get_mark(generate_vega_lite_spec(layered, data)).get("width")

        plain = build(False)
        resolve(plain, data, chart_style_context=_BOARD_STYLE)
        plain_width = _get_mark(generate_vega_lite_spec(plain, data)).get("width")

        assert layered_width == {"expr": "bandwidth('x')"}, (
            f"a cleared band_width under a step-curve layer must be rewritten to "
            f"an explicit bandwidth('x'), not left absent for VL to default, "
            f"got {layered_width!r}"
        )
        assert plain_width is None, (
            f"without the degrading layer the same chart emits no width at all "
            f"and VL resolves the band itself, got {plain_width!r}"
        )


class TestGroupedBarQuantitativeX:
    """A grouped bar (color + stack: none) on a quantitative x has no band for
    xOffset to sub-divide — xOffset is still emitted (bar.py keeps it for the
    bucketed-temporal case), but it must not be trusted as a real band scale
    when sizing the mark.
    """

    def _chart_with_line_layer(self, **kwargs) -> Chart:
        return BarChart(
            id="test_bar",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="bar",
            x="x_num",
            y="revenue",
            color="region",
            stack=None,
            style=BarChartStylePatch(orientation="vertical"),
            layers=[LineLayer(type="line", y="revenue")],
            **kwargs,
        )

    def test_non_layered_grouped_bar_quantitative_x_scale_has_no_group_padding(self):
        """The grouped-bar band gutter must not leak onto a quantitative x scale.

        ``apply_grouped_bar_spacing`` unconditionally overwrote paddingOuter
        with the grouped-bar band gutter (``bar_cfg.grouped_bar_padding_outer``)
        whenever xOffset was present — meaningless on a quantitative scale,
        which has no bands to pad between.
        """
        bar_cfg = get_chart_rendering().bar
        chart = BarChart(
            id="test_bar",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="bar",
            x="x_num",
            y="revenue",
            color="region",
            stack=None,
            style=BarChartStylePatch(orientation="vertical"),
        )
        resolve(chart, NUMERIC_X_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, NUMERIC_X_DATA)
        x_scale = self._bar_scale_from_flat_or_layered(spec)
        assert x_scale.get("paddingOuter") != bar_cfg.grouped_bar_padding_outer, (
            f"quantitative x scale must not carry the grouped-bar band gutter: {x_scale}"
        )

    @pytest.mark.parametrize("overlap", ["none", "flush", 0.5, "full"])
    def test_authored_overlap_on_a_continuous_x_raises(self, overlap):
        """An authored overlap must not evaporate on a continuous x.

        `overlap` is a fraction of the categorical BAND width, and a
        quantitative x has no band — so the setting is inapplicable rather
        than merely unused. Silently emitting a spec that ignores it is the
        failure mode this pins; the author gets a clear error instead.
        """
        chart = BarChart(
            id="test_bar",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="bar",
            x="x_num",
            y="revenue",
            color="region",
            stack=None,
            style=BarChartStylePatch(orientation="vertical", overlap=overlap),
        )
        resolve(chart, NUMERIC_X_DATA, chart_style_context=_BOARD_STYLE)
        with pytest.raises(ChartDataError, match="continuous x-axis"):
            generate_vega_lite_spec(chart, NUMERIC_X_DATA)

    @pytest.mark.parametrize("overlap", [None, "auto"])
    def test_renderer_default_overlap_on_a_continuous_x_does_not_raise(self, overlap):
        """The renderer default must render, under BOTH its spellings.

        `BarChartStyle.overlap` documents None as "uses the renderer default
        ('auto')", and `_resolve_overlap_fraction` treats the two identically —
        so an explicit `overlap: auto` is a documented no-op, not an authored
        request the guard should reject.
        """
        chart = BarChart(
            id="test_bar",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="bar",
            x="x_num",
            y="revenue",
            color="region",
            stack=None,
            style=BarChartStylePatch(orientation="vertical", overlap=overlap),
        )
        resolve(chart, NUMERIC_X_DATA, chart_style_context=_BOARD_STYLE)
        assert generate_vega_lite_spec(chart, NUMERIC_X_DATA)

    @staticmethod
    def _bar_scale_from_flat_or_layered(spec: dict) -> dict:
        for layer in spec.get("layer", []):
            mark = layer.get("mark", {})
            if isinstance(mark, dict) and mark.get("type") == "bar":
                enc = layer.get("encoding", {})
                if "x" in enc:
                    return enc["x"].get("scale", {})
        return spec.get("encoding", {}).get("x", {}).get("scale", {})

    @staticmethod
    def _rendered_bar_widths(spec: dict) -> list[float]:
        """Real bar widths from a vl_convert render of `spec`.

        Corner-radius bars emit curved (M/L/C) paths, square-cornered bars emit
        straight (M/h/v) ones — every coordinate pair's x is captured either
        way, so the path's own bounding-box width is format-agnostic evidence
        of the mark's rendered width.
        """
        import re

        from dbt_charts.core.render.converters.chart import render_vega_spec

        svg = render_vega_spec(
            spec,
            "svg",
            _RESOLVED_STYLE,
            width=400.0,
            height=300.0,
            is_placeholder=False,
            chart_id="chart",
        )
        mark_group = re.search(
            r'<g class="mark-rect[^"]*"[^>]*>(.*?)</g>', svg, re.DOTALL
        )
        assert mark_group, "no bar mark group found in rendered SVG"
        paths = re.findall(r'<path[^>]*d="([^"]+)"', mark_group.group(1))
        assert paths, "no bar path 'd' attributes matched — regex is stale"
        widths = []
        for p in paths:
            xs = [float(x) for x in re.findall(r"(-?[0-9.]+),-?[0-9.]+", p)]
            assert xs, f"no coordinate pairs matched in path {p!r} — regex is stale"
            widths.append(max(xs) - min(xs))
        return widths

    def test_layered_grouped_bar_quantitative_x_renders_nonzero_width(self):
        """Render the layered spec through vl_convert and check real bar geometry.

        Before the fix, `_fix_bar_band_width` rewrote `mark.width` to
        `bandwidth('xOffset')` unconditionally whenever any sibling layer
        carried an xOffset channel. On a quantitative x, xOffset has no band
        scale to size against, so `bandwidth('xOffset')` evaluates to 0 and
        every bar path degenerates to a zero-width sliver (an `h0` segment).
        """
        try:
            import vl_convert as vlc  # noqa: F401 — skip marker
        except ImportError:
            pytest.skip("vl_convert not installed")

        chart = self._chart_with_line_layer()
        resolve(chart, NUMERIC_X_DATA, chart_style_context=_BOARD_STYLE)
        widths = self._rendered_bar_widths(
            generate_vega_lite_spec(chart, NUMERIC_X_DATA)
        )
        assert all(w > 1.0 for w in widths), (
            f"bar widths {widths} include a near-zero bar — "
            "bandwidth('xOffset') resolved to ~0 against a quantitative x"
        )

        # The real invariant: adding `layers:` must not change how wide the
        # bars are. Compared against the same chart's own unlayered render
        # rather than any literal or theme value, so it holds whatever
        # `bar.size`/`band_width` resolve to.
        flat = BarChart(
            id="test_bar",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="bar",
            x="x_num",
            y="revenue",
            color="region",
            stack=None,
            style=BarChartStylePatch(orientation="vertical"),
        )
        resolve(flat, NUMERIC_X_DATA, chart_style_context=_BOARD_STYLE)
        flat_widths = self._rendered_bar_widths(
            generate_vega_lite_spec(flat, NUMERIC_X_DATA)
        )
        assert widths == pytest.approx(flat_widths), (
            f"layered grouped bar renders at {widths}, but the same chart "
            f"without layers renders at {flat_widths} — the layer wrap "
            "changed the bar geometry"
        )

    def test_layered_grouped_bar_quantitative_x_survives_null_bar_size(self):
        """chart-local `style.marks.bar.size: null` must still render.

        `BarChartStyle.marks` carries an InheritSlot and only the board-tier
        merge runs before `apply_inherit` refills it, so the chart-local tier
        can genuinely clear `bar.size` — leaving the emitted mark with no
        `continuousBandSize` key at all. Reading that key unguarded crashed
        the whole render with a KeyError.
        """
        chart = _chart(
            layers=[LineLayer(type="line", y="revenue")],
        )
        chart.x = "x_num"
        chart.style = BarChartStylePatch.model_validate(
            {"orientation": "vertical", "marks": {"bar": {"size": None}}}
        )
        resolve(chart, NUMERIC_X_DATA, chart_style_context=_BOARD_STYLE)
        assert chart.style.marks.bar.size is None, (
            "premise broken: bar.size was refilled, so this no longer covers "
            "the missing-continuousBandSize path"
        )
        widths = self._rendered_bar_widths(
            generate_vega_lite_spec(chart, NUMERIC_X_DATA)
        )
        assert all(w > 1.0 for w in widths), f"degenerate bars: {widths}"


class TestGroupedBarTopRuleOffset:
    """100%-baseline rule (_top_rule_layer) must not inherit grouped xOffset/yOffset."""

    def _get_top_rule_layer(self, spec: dict, datum_channel: str) -> dict | None:
        for layer in chart_pane(spec).get("layer", []):
            mark = layer.get("mark", {})
            if isinstance(mark, dict) and mark.get("type") == "rule":
                enc = layer.get("encoding", {})
                if datum_channel in enc and enc[datum_channel].get("datum") == 1:
                    return layer
        return None

    def test_vertical_grouped_normalize_top_rule_overrides_xoffset(self):
        chart = _chart(stack="normalize")
        resolve(chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, MULTI_SERIES_DATA)
        assert "layer" in chart_pane(spec)
        rule = self._get_top_rule_layer(spec, "y")
        assert rule is not None, (
            "top rule layer not found for normalize-stacked vertical bar"
        )
        enc = rule.get("encoding", {})
        assert "xOffset" in enc, "top rule must override xOffset to prevent inheritance"
        assert enc["xOffset"] == {"value": 0}

    def test_horizontal_grouped_normalize_top_rule_overrides_yoffset(self):
        chart = _chart(stack="normalize", orientation="horizontal")
        resolve(chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, MULTI_SERIES_DATA)
        assert "layer" in chart_pane(spec)
        rule = self._get_top_rule_layer(spec, "x")
        assert rule is not None, (
            "top rule layer not found for normalize-stacked horizontal bar"
        )
        enc = rule.get("encoding", {})
        assert "yOffset" in enc, "top rule must override yOffset to prevent inheritance"
        assert enc["yOffset"] == {"value": 0}


class TestGroupedColumnsSkipsStackingHelpers:
    """Grouped default must not apply stacking-specific VL properties."""

    def test_grouped_bar_has_no_order_encoding(self):
        """Z-order pinning (encoding.order) is for stacked bars only."""
        chart = _chart()
        resolve(chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, MULTI_SERIES_DATA)
        enc = _get_encoding(spec)
        assert "order" not in enc, (
            "grouped bar should not have encoding.order (that's for stacked z-order)"
        )

    def test_stacked_bar_still_has_order_encoding(self):
        """Explicit stack=zero should still emit encoding.order for z-ordering."""
        chart = _chart(stack="zero")
        resolve(chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, MULTI_SERIES_DATA)
        enc = _get_encoding(spec)
        assert "order" in enc, "stacked bar should still emit encoding.order"

    def test_grouped_bar_y_scale_domain_max_not_stacked_total(self):
        """Grouped bars must not use the stacked-total as domainMax.

        The stacked total for MULTI_SERIES_DATA is 210 (120+90 for Beta).
        The grouped max per bar is 120; the tick-value algorithm may extend
        domainMax to a nice value above 120, but it must never reach 210.
        """
        chart = _chart()
        resolve(chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, MULTI_SERIES_DATA)
        enc = _get_encoding(spec)
        y_scale = enc.get("y", {}).get("scale", {})
        stacked_total = 210  # Beta: 120+90
        domain_max = y_scale.get("domainMax")
        assert domain_max != stacked_total, (
            f"grouped bar must not use stacked total {stacked_total} as domainMax "
            f"(stacking helper fired for grouped bar); got scale={y_scale}"
        )


NUMERIC_COLOR_DATA = [
    {"category": "Alpha", "revenue": 100, "flag": 1},
    {"category": "Alpha", "revenue": 80, "flag": 0},
    {"category": "Beta", "revenue": 120, "flag": 1},
    {"category": "Beta", "revenue": 90, "flag": 0},
]

BOOLEAN_COLOR_DATA = [
    {"category": "Alpha", "revenue": 100, "flag": True},
    {"category": "Alpha", "revenue": 80, "flag": False},
    {"category": "Beta", "revenue": 120, "flag": True},
    {"category": "Beta", "revenue": 90, "flag": False},
]

TEMPORAL_COLOR_DATA = [
    {"category": "Alpha", "revenue": 100, "flag": "2024-01-01"},
    {"category": "Alpha", "revenue": 80, "flag": "2024-02-01"},
    {"category": "Beta", "revenue": 120, "flag": "2024-01-01"},
    {"category": "Beta", "revenue": 90, "flag": "2024-02-01"},
]

_NON_DISCRETE_COLOR_CASES = [
    (NUMERIC_COLOR_DATA, "flag", "quantitative"),
    (BOOLEAN_COLOR_DATA, "flag", "quantitative"),
    (TEMPORAL_COLOR_DATA, "flag", "temporal"),
]
_NON_DISCRETE_COLOR_IDS = ["numeric-color", "boolean-color", "temporal-color"]


class TestGroupedBarNonStringColor:
    """A numeric/boolean/temporal color field resolves to a non-discrete VL
    type, but xOffset/yOffset always need a discrete (nominal/ordinal) type
    regardless of the color paint scale's own type — a continuous scale has
    no band for ``bandwidth(...)`` to size against, which degenerates every
    bar to zero width/height (ERR-CHART-PAINTED-NO-MARKS). The asymmetry is
    the point: the color channel itself keeps its own (non-discrete) type.
    """

    @pytest.mark.parametrize(
        ("data", "color_field", "expected_color_type"),
        _NON_DISCRETE_COLOR_CASES,
        ids=_NON_DISCRETE_COLOR_IDS,
    )
    def test_vertical_bar_emits_discrete_xoffset_type(
        self, data, color_field, expected_color_type
    ):
        chart = _chart(color=color_field)
        resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        enc = _get_encoding(spec)
        assert enc["color"]["type"] == expected_color_type, (
            "premise broken: the color channel itself is no longer "
            f"{expected_color_type!r}, got {enc['color']['type']!r}"
        )
        assert "xOffset" in enc
        assert enc["xOffset"]["type"] in ("nominal", "ordinal"), (
            "xOffset must use a discrete VL type to get a band scale, got "
            f"{enc['xOffset']['type']!r}"
        )

    @pytest.mark.parametrize(
        ("data", "color_field", "expected_color_type"),
        _NON_DISCRETE_COLOR_CASES,
        ids=_NON_DISCRETE_COLOR_IDS,
    )
    def test_horizontal_bar_emits_discrete_yoffset_type(
        self, data, color_field, expected_color_type
    ):
        chart = _chart(color=color_field, orientation="horizontal")
        resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        enc = _get_encoding(spec)
        assert enc["color"]["type"] == expected_color_type, (
            "premise broken: the color channel itself is no longer "
            f"{expected_color_type!r}, got {enc['color']['type']!r}"
        )
        assert "yOffset" in enc
        assert enc["yOffset"]["type"] in ("nominal", "ordinal"), (
            "yOffset must use a discrete VL type to get a band scale, got "
            f"{enc['yOffset']['type']!r}"
        )
