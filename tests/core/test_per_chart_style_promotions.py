"""Regression tests: per-chart-type *Style fields populated from theme YAML.

Every promoted field must be reachable via the cascade and a distinctive
override must propagate to rendered output.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    KpiChart,
    SparkBarChart,
    TableChart,
)
from dbt_charts.core.compile.models.primitives import StrokeStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
    resolve_style_and_context,
)
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_STYLE = resolve_style(get_theme_style())
_BOARD_CONTEXT = resolve_chart_style_context(get_theme_style())


def _mark(spec: dict) -> dict:
    """Get the foreground mark dict from single-spec or layered spec.

    For halo line charts, halo layers come first; the foreground is the last
    line-type layer. For non-halo specs, returns the single mark or first layer.
    """
    m = spec.get("mark", {})
    if isinstance(m, dict) and m:
        return m
    layers = spec.get("layer", [])
    if not layers:
        return {}
    # Prefer the last line-type layer (foreground in halo specs).
    for layer in reversed(layers):
        lm = layer.get("mark", {})
        if isinstance(lm, dict) and lm.get("type") == "line":
            return lm
    return layers[0].get("mark", {})


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _board_with_mark(chart_type: str, mark_key: str, **mark_overrides):
    """Return a (ResolvedStyle, ChartStyleContext) pair with an override on a
    specific mark within a chart family.

    When the family tier has no mark override (None), uses the global marks tier
    as base so the override can be applied.
    """
    compiled = get_theme_style()
    chart_style = getattr(compiled.charts, chart_type)
    family_marks = chart_style.marks
    mark_style = getattr(family_marks, mark_key)
    if mark_style is None:
        # Family tier has no override — use global marks as base.
        mark_style = getattr(compiled.charts.marks, mark_key)
    updated_mark = mark_style.model_copy(update=mark_overrides)
    updated_marks = family_marks.model_copy(update={mark_key: updated_mark})
    updated = chart_style.model_copy(update={"marks": updated_marks})
    charts = compiled.charts.model_copy(update={chart_type: updated})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def _board_with_global_mark(mark_key: str, **mark_overrides):
    """Return a (ResolvedStyle, ChartStyleContext) pair with an override on a
    global mark (charts.marks.<mark>)."""
    compiled = get_theme_style()
    mark_style = getattr(compiled.charts.marks, mark_key)
    updated_mark = mark_style.model_copy(update=mark_overrides)
    updated_marks = compiled.charts.marks.model_copy(update={mark_key: updated_mark})
    charts = compiled.charts.model_copy(update={"marks": updated_marks})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def _board_with(chart_type: str, **field_overrides):
    """Return a (ResolvedStyle, ChartStyleContext) pair with a distinctive
    override on one chart-type style."""
    compiled = get_theme_style()
    chart_style = getattr(compiled.charts, chart_type)
    updated = chart_style.model_copy(update=field_overrides)
    charts = compiled.charts.model_copy(update={chart_type: updated})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def _board_with_stroke(chart_type: str, mark_key: str, **stroke_overrides):
    """Return a (ResolvedStyle, ChartStyleContext) pair with stroke sub-field
    overrides on a mark within a chart family."""
    compiled = get_theme_style()
    chart_style = getattr(compiled.charts, chart_type)
    family_marks = chart_style.marks
    mark_style = getattr(family_marks, mark_key)
    # stroke is a cascade-tier sentinel — None means "not set at this tier".
    # Use the existing stroke as base if present; otherwise start from an empty StrokeStyle.
    stroke_base = mark_style.stroke or StrokeStyle()
    new_stroke = stroke_base.model_copy(update=stroke_overrides)
    updated_mark = mark_style.model_copy(update={"stroke": new_stroke})
    updated_marks = family_marks.model_copy(update={mark_key: updated_mark})
    updated = chart_style.model_copy(update={"marks": updated_marks})
    charts = compiled.charts.model_copy(update={chart_type: updated})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def _board_with_point(chart_type: str, **point_overrides):
    """Return a (ResolvedStyle, ChartStyleContext) pair with point sub-field
    overrides at the effective tier.

    Injects at the family tier when the chart type has its own family-level point mark
    (so family-level defaults are preserved), otherwise injects at the global tier.
    Matches the _board_with_stroke pattern.
    """
    compiled = get_theme_style()
    chart_style = getattr(compiled.charts, chart_type)
    family_marks = chart_style.marks
    base_point = (
        family_marks.point
        if family_marks.point is not None
        else compiled.charts.marks.point
    )
    new_point = base_point.model_copy(update=point_overrides)
    updated_marks = family_marks.model_copy(update={"point": new_point})
    updated = chart_style.model_copy(update={"marks": updated_marks})
    charts = compiled.charts.model_copy(update={chart_type: updated})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


SAMPLE_DATA = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]

# Multi-series area data for opacity-cascade tests -- keeps those charts off
# the single-series recipe swap (area.py) that would otherwise overwrite an
# authored opacity override with the theme's stacked-recipe value.
_MULTI_SERIES_DATA = [
    {"month": "Jan", "revenue": 100, "segment": "A"},
    {"month": "Feb", "revenue": 200, "segment": "A"},
    {"month": "Jan", "revenue": 50, "segment": "B"},
    {"month": "Feb", "revenue": 80, "segment": "B"},
]


class TestLineStylePromotion:
    """LineStyle fields come from theme YAML (no Python defaults)."""

    def test_curve_propagates_to_vl_interpolate(self, make_chart):
        # monotone (not "step") — "step" on this nominal x is band-aware and
        # forces interpolate=step-after; this test checks the generic
        # curve-passthrough plumbing, not the band-step mechanism.
        board, board_context = _board_with_mark("line", "line", curve="monotone")
        chart = make_chart("line", x="month", y="revenue")
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board, chart_style_context=board_context
        )
        assert _mark(spec)["interpolate"] == "monotone"

    def test_point_size_nonzero_enables_line_point(self, make_chart):
        # line.marks.point.size = 0 suppresses points on line charts (family tier).
        # Override via the family tier (not global marks) to actually enable points.
        board, board_context = _board_with_mark("line", "point", size=77.0)
        chart = make_chart("line", x="month", y="revenue")
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board, chart_style_context=board_context
        )
        # With halo enabled (default theme halo_multiplier > 0), points are separate layers.
        # With halo disabled, point appears in the mark dict.
        layers = spec.get("layer", [])
        if layers:
            point_marks = [
                lyr
                for lyr in layers
                if isinstance(lyr.get("mark"), dict)
                and lyr["mark"].get("type") == "point"
            ]
            assert len(point_marks) > 0, (
                "point layers must be present when point.size > 0"
            )
        else:
            mark = spec.get("mark", {})
            assert mark.get("point") is not None and mark.get("point") is not False

    def test_stroke_width_propagates(self, make_chart):
        # Board-level stroke is the theme fallback; density-adaptive stroke
        # overrides it when chart data is available.  Author the stroke at the
        # chart level to pin the output and verify the cascade carries it through.
        chart_a = make_chart(
            "line",
            x="month",
            y="revenue",
            style={"marks": {"line": {"stroke": {"width": 1.0}}}},
        )
        chart_b = make_chart(
            "line",
            x="month",
            y="revenue",
            style={"marks": {"line": {"stroke": {"width": 5.0}}}},
        )
        spec_a = generate_vega_lite_spec(
            chart_a,
            SAMPLE_DATA,
            board_style=_BOARD_STYLE,
            chart_style_context=_BOARD_CONTEXT,
        )
        spec_b = generate_vega_lite_spec(
            chart_b,
            SAMPLE_DATA,
            board_style=_BOARD_STYLE,
            chart_style_context=_BOARD_CONTEXT,
        )
        assert _mark(spec_a)["strokeWidth"] != _mark(spec_b)["strokeWidth"]

    def test_stroke_cap_propagates_to_vl_config(self, make_chart):
        board, board_context = _board_with_stroke("line", "line", cap="butt")
        chart = make_chart("line", x="month", y="revenue")
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board, chart_style_context=board_context
        )
        assert _mark(spec)["strokeCap"] == "butt"

    def test_stroke_join_propagates_to_vl_config(self, make_chart):
        board, board_context = _board_with_stroke("line", "line", join="bevel")
        chart = make_chart("line", x="month", y="revenue")
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board, chart_style_context=board_context
        )
        assert _mark(spec)["strokeJoin"] == "bevel"

    def test_halo_multiplier_zero_emits_single_line(self, make_chart):
        """halo=0 emits a single line mark + invisible point overlay (no halo layer).
        No zero-baseline rule: SAMPLE_DATA is all-positive and the pipeline infers
        zero=False for line charts, so _domain_includes_zero returns False.

        Points pinned off explicitly: SAMPLE_DATA's 2 points sit well inside the
        density-auto-on trigger (bake_point_companions), which would otherwise add
        a real third visible-point layer here, unrelated to what halo=0 tests.
        """
        board, board_context = _board_with_mark("line", "line", halo_multiplier=0.0)
        chart = make_chart(
            "line", x="month", y="revenue", style={"marks": {"point": {"size": 0.0}}}
        )
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board, chart_style_context=board_context
        )
        # No halo: [line, point_overlay] only.
        layer = spec["layer"]
        assert [lyr["mark"]["type"] for lyr in layer] == ["line", "point"]
        assert layer[1]["mark"]["opacity"] == 0  # overlay is invisible

    def test_halo_multiplier_positive_emits_layered_halo(self, make_chart):
        """Points pinned off explicitly: SAMPLE_DATA's 2 points sit well inside
        the density-auto-on trigger, which would otherwise add a real
        visible-point layer here, unrelated to what the halo layering tests."""
        compiled = get_theme_style()
        line = compiled.charts.line
        line_marks = line.marks
        line_mark = line_marks.line
        stroke_base = line_mark.stroke or StrokeStyle()
        updated_line_mark = line_mark.model_copy(
            update={
                "halo_multiplier": 3.0,
                "stroke": stroke_base.model_copy(update={"width": 2.0}),
            }
        )
        updated_marks = line_marks.model_copy(update={"line": updated_line_mark})
        updated_line = line.model_copy(update={"marks": updated_marks})
        charts = compiled.charts.model_copy(update={"line": updated_line})
        board, board_context = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )
        # Pin the stroke at the chart level so density-adaptive stroke is
        # bypassed; the authored value propagates through halo × halo_multiplier.
        # Points pinned off separately (unrelated knob -- see docstring above).
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style={
                "marks": {
                    "line": {"stroke": {"width": 2.0}},
                    "point": {"size": 0.0},
                }
            },
        )
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board, chart_style_context=board_context
        )
        layer = spec["layer"]
        # halo + foreground line + hover point overlay;
        # no zero-baseline rule (all-positive data, zero=False).
        assert [lyr["mark"]["type"] for lyr in layer] == ["line", "line", "point"]
        halo, fg = layer[0]["mark"], layer[1]["mark"]
        # Halo is wider than foreground by exactly halo_multiplier
        assert halo["strokeWidth"] == pytest.approx(2.0 * 3.0)
        assert fg["strokeWidth"] == pytest.approx(2.0)
        # Halo is knockout-colored to the chart background
        assert halo["stroke"] == spec["background"]

    def test_halo_with_points_emits_four_layers(self, make_chart):
        compiled = get_theme_style()
        line = compiled.charts.line
        line_marks = line.marks
        # line.marks.line is None (family tier has no override); use global marks as base.
        line_mark = line_marks.line or compiled.charts.marks.line
        updated_line_mark = line_mark.model_copy(update={"halo_multiplier": 2.0})
        new_point = compiled.charts.marks.point.model_copy(update={"size": 40.0})
        # line.marks.point is None (family tier has no override); use global marks as base.
        line_point = line_marks.point or compiled.charts.marks.point
        updated_line_point = line_point.model_copy(update={"size": 40.0})
        updated_marks = line_marks.model_copy(
            update={"line": updated_line_mark, "point": updated_line_point}
        )
        updated_line = line.model_copy(update={"marks": updated_marks})
        updated_global_marks = compiled.charts.marks.model_copy(
            update={"point": new_point}
        )
        charts = compiled.charts.model_copy(
            update={"line": updated_line, "marks": updated_global_marks}
        )
        board, board_context = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )
        # Author point.size at the chart level to pin it at 40.0 — this bypasses
        # the companion size override that density-adaptive stroke would otherwise
        # apply when the cascaded size is not explicitly authored.
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style={"marks": {"point": {"size": 40.0}}},
        )
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board, chart_style_context=board_context
        )
        layer = spec["layer"]
        # All halo layers come before all foreground layers so the foreground
        # line is never occluded by a point halo at the data points.
        # Hover point overlay is appended last.
        # No zero-baseline rule: all-positive data with inferred zero=False.
        assert [lyr["mark"]["type"] for lyr in layer] == [
            "line",
            "point",
            "line",
            "point",
            "point",
        ]
        halo_point, fg_point = layer[1]["mark"], layer[3]["mark"]
        assert halo_point["size"] == pytest.approx(40.0 * 2.0)
        assert fg_point["size"] == pytest.approx(40.0)
        assert halo_point["fill"] == spec["background"]


class TestAreaStylePromotion:
    """AreaStyle fields come from theme YAML."""

    @staticmethod
    def _fg_area_mark(spec: dict) -> dict:
        """Return the foreground area mark (last area layer, before any rule layers)."""
        layers = spec.get("layer", [])
        for layer in reversed(layers):
            m = layer.get("mark", {})
            if (
                isinstance(m, dict)
                and m.get("type") == "area"
                and m.get("tooltip") is True
            ):
                return m
        return spec.get("mark", {})

    def test_opacity_propagates(self, make_chart):
        # Multi-series (color) so the single-series recipe swap
        # (area.py: solid opacity, nothing to overlap with at one series)
        # doesn't override this authored opacity with the theme's stacked
        # value; endpoint labels off so the spec stays a single layer list
        # instead of the endpoint-label rail's hconcat wrapper.
        board, board_context = _board_with_mark("area", "area", opacity=0.11)
        chart = make_chart(
            "area",
            x="month",
            y="revenue",
            color="segment",
            style={"endpoint_labels": {"visible": False}},
        )
        spec = generate_vega_lite_spec(
            chart,
            _MULTI_SERIES_DATA,
            board_style=board,
            chart_style_context=board_context,
        )
        # With halo, the fg area uses fillOpacity for the tint; mark-level opacity=1.
        fg = self._fg_area_mark(spec)
        assert fg.get("fillOpacity") == pytest.approx(0.11)

    def test_stroke_width_propagates(self, make_chart):
        # Area's top-edge stroke is a genuine separate line mark (Vega-Lite
        # compiles it that way) — its geometry lives on marks.line, not
        # marks.area (fill-only: opacity/curve).
        # Author stroke at the chart level (primary.marks.line.stroke.width) so
        # the authored-pin check bypasses density-adaptive stroke baking.
        # Multi-series (color) so the single-series recipe swap (area.py: the
        # full stacked recipe, including stroke) doesn't replace this authored
        # marks.line.stroke wholesale; endpoint labels off so the spec stays a
        # single layer list instead of the endpoint-label rail's hconcat wrapper.
        chart_a = make_chart(
            "area",
            x="month",
            y="revenue",
            color="segment",
            style={
                "marks": {"line": {"stroke": {"width": 1.0}}},
                "endpoint_labels": {"visible": False},
            },
        )
        chart_b = make_chart(
            "area",
            x="month",
            y="revenue",
            color="segment",
            style={
                "marks": {"line": {"stroke": {"width": 7.0}}},
                "endpoint_labels": {"visible": False},
            },
        )
        spec_a = generate_vega_lite_spec(
            chart_a,
            _MULTI_SERIES_DATA,
            board_style=_BOARD_STYLE,
            chart_style_context=_BOARD_CONTEXT,
        )
        spec_b = generate_vega_lite_spec(
            chart_b,
            _MULTI_SERIES_DATA,
            board_style=_BOARD_STYLE,
            chart_style_context=_BOARD_CONTEXT,
        )

        # stroke_width is on the fg line mark (separate layer after fill/stroke split).
        def _fg_line(spec: dict) -> dict:
            for layer in reversed(spec.get("layer", [])):
                m = layer.get("mark", {})
                if isinstance(m, dict) and m.get("type") == "line":
                    return m
            return spec.get("mark", {})

        sw_a = _fg_line(spec_a).get("strokeWidth")
        sw_b = _fg_line(spec_b).get("strokeWidth")
        assert sw_a != sw_b


class TestScatterStylePromotion:
    """ScatterStyle fields come from theme YAML."""

    def test_opacity_propagates(self, make_chart):
        board, board_context = _board_with_point("scatter", opacity=0.13)
        chart = make_chart("scatter", x="month", y="revenue")
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board, chart_style_context=board_context
        )
        assert _mark(spec)["opacity"] == pytest.approx(0.13)

    def test_filled_propagates(self, make_chart):
        board_true, ctx_true = _board_with_point("scatter", filled=True)
        board_false, ctx_false = _board_with_point("scatter", filled=False)
        chart = make_chart("scatter", x="month", y="revenue")
        spec_t = generate_vega_lite_spec(
            chart,
            SAMPLE_DATA,
            board_style=board_true,
            chart_style_context=ctx_true,
        )
        spec_f = generate_vega_lite_spec(
            chart,
            SAMPLE_DATA,
            board_style=board_false,
            chart_style_context=ctx_false,
        )
        assert _mark(spec_t)["filled"] != _mark(spec_f)["filled"]

    def test_point_size_propagates(self, make_chart):
        board_a, ctx_a = _board_with_point("scatter", size=11.0)
        board_b, ctx_b = _board_with_point("scatter", size=99.0)
        chart = make_chart("scatter", x="month", y="revenue")
        spec_a = generate_vega_lite_spec(
            chart,
            SAMPLE_DATA,
            board_style=board_a,
            chart_style_context=ctx_a,
        )
        spec_b = generate_vega_lite_spec(
            chart,
            SAMPLE_DATA,
            board_style=board_b,
            chart_style_context=ctx_b,
        )
        assert _mark(spec_a)["size"] != _mark(spec_b)["size"]


class TestKpiStylePromotion:
    """KpiStyle visual fields come from theme YAML (presence test)."""

    def test_kpi_visual_fields_present(self):
        compiled = get_theme_style()
        kpi = compiled.charts.kpi
        # Presence: field is set (not None, not missing — would raise AttributeError if absent)
        assert kpi.preferred_width > 0
        assert kpi.default_height > 0
        assert kpi.value.font.size > 0
        assert kpi.min_card_width > 0
        # tones is board-level (Style.tones), not per-family
        assert compiled.tones.positive  # tone palette resolves through theme YAML
        assert kpi.content_padding.horizontal >= 0
        assert kpi.content_padding.vertical >= 0
        assert kpi.affix.font.size > 0


class TestSparkBarStylePromotion:
    """SparkBarChartStyle sub-blocks come from theme YAML (presence test)."""

    def test_spark_bar_sub_blocks_present(self):
        compiled = get_theme_style()
        sb = compiled.charts.spark_bar
        # bar sub-block
        assert sb.bar.height > 0
        assert sb.bar.padding >= 0
        # label sub-block
        assert isinstance(sb.label.visible, bool)
        assert sb.label.width > 0
        # count sub-block
        assert isinstance(sb.count.visible, bool)
        assert sb.count.width > 0
        # flat survivors
        assert sb.max_bars > 0


class TestChartTypePatchCascade:
    """Compiled*StylePatch fields set on ChartStylePatch reach resolved_style."""

    def test_line_patch_stroke_width_cascades_to_vl_config(self, make_chart):
        """chart.style.line.marks.line.stroke.width reaches the resolved resolved_style."""
        from dbt_charts.core.compile.models.style.authored import (
            LineChartStylePatch,
        )

        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style=LineChartStylePatch(marks={"line": {"stroke": {"width": 9.0}}}),
        )
        _rc = resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT)
        spec = generate_vega_lite_spec(chart, SAMPLE_DATA)
        assert _mark(spec)["strokeWidth"] == pytest.approx(9.0)

    def test_line_patch_nested_curve_cascades(self, make_chart):
        """chart.style.line.marks.line.curve reaches config.line.interpolate via cascade."""
        from dbt_charts.core.compile.models.style.authored import (
            LineChartStylePatch,
        )

        # monotone (not "step") — "step" on this nominal x is band-aware and
        # forces interpolate=step-after; this test checks the generic
        # cascade plumbing, not the band-step mechanism.
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style=LineChartStylePatch(marks={"line": {"curve": "monotone"}}),
        )
        _rc = resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT)
        spec = generate_vega_lite_spec(chart, SAMPLE_DATA)
        assert _mark(spec)["interpolate"] == "monotone"

    def test_area_patch_opacity_cascades(self, make_chart):
        """chart.style.area.marks.area.opacity reaches the fg area's fillOpacity via cascade.

        Multi-series (color) so the single-series recipe swap (area.py) --
        solid opacity, nothing to overlap with at one series -- doesn't
        override this authored opacity with the theme's stacked value.
        """
        from dbt_charts.core.compile.models.style.authored import (
            AreaChartStylePatch,
        )

        chart = make_chart(
            "area",
            x="month",
            y="revenue",
            color="segment",
            style=AreaChartStylePatch(
                marks={"area": {"opacity": 0.22}},
                endpoint_labels={"visible": False},
            ),
        )
        _rc = resolve(chart, _MULTI_SERIES_DATA, chart_style_context=_BOARD_CONTEXT)
        spec = generate_vega_lite_spec(chart, _MULTI_SERIES_DATA)
        # With halo, the fg area mark uses fillOpacity for the tint.
        layers = spec.get("layer", [])
        fg = next(
            (
                lyr["mark"]
                for lyr in reversed(layers)
                if isinstance(lyr.get("mark"), dict)
                and lyr["mark"].get("type") == "area"
                and lyr["mark"].get("tooltip") is True
            ),
            spec.get("mark", {}),
        )
        assert fg.get("fillOpacity") == pytest.approx(0.22)

    def test_bar_patch_opacity_cascades(self, make_chart):
        """chart.style.bar.marks.bar.opacity reaches the bar mark's fillOpacity via cascade.

        Bar opacity maps to VL ``fillOpacity`` (fill-only), so an authored
        border keeps full stroke opacity — matching BarMarkStyle.opacity's own
        "Bar fill opacity" contract.
        """
        from dbt_charts.core.compile.models.style.authored import (
            BarChartStylePatch,
        )

        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style=BarChartStylePatch(marks={"bar": {"opacity": 0.85}}),
        )
        _rc = resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT)
        spec = generate_vega_lite_spec(chart, SAMPLE_DATA)
        assert _mark(spec)["fillOpacity"] == pytest.approx(0.85)

    def test_bar_no_opacity_authored_leaves_mark_unchanged(self, make_chart):
        """Default path: a bar chart with no marks.bar.opacity authored must
        emit no 'opacity' key at all — the same shape as before this field
        existed."""
        chart = make_chart("bar", x="month", y="revenue")
        _rc = resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT)
        spec = generate_vega_lite_spec(chart, SAMPLE_DATA)
        assert "opacity" not in _mark(spec)

    def test_histogram_patch_opacity_cascades(self, make_chart):
        """chart.style.bar.marks.bar.opacity also reaches histogram bars —
        histogram shares BarChart/BarChartStylePatch and BarMarkStyle."""
        from dbt_charts.core.compile.models.style.authored import (
            BarChartStylePatch,
        )

        histogram_data = [{"price": n} for n in range(1, 21)]
        chart = make_chart(
            "histogram",
            x="price",
            y=None,
            style=BarChartStylePatch(marks={"bar": {"opacity": 0.85}}),
        )
        _rc = resolve(chart, histogram_data, chart_style_context=_BOARD_CONTEXT)
        spec = generate_vega_lite_spec(chart, histogram_data)
        assert _mark(spec)["fillOpacity"] == pytest.approx(0.85)

    def test_histogram_no_opacity_authored_leaves_mark_unchanged(self, make_chart):
        """Default path: a histogram with no marks.bar.opacity authored must
        emit no 'opacity' key — the emitted spec is unchanged from before
        this field existed."""
        histogram_data = [{"price": n} for n in range(1, 21)]
        chart = make_chart("histogram", x="price", y=None)
        _rc = resolve(chart, histogram_data, chart_style_context=_BOARD_CONTEXT)
        spec = generate_vega_lite_spec(chart, histogram_data)
        assert "opacity" not in _mark(spec)


class TestSparkBarNestedPatchCascade:
    """Nested patch fields inside SparkBarChartStylePatch survive merge_onto_base."""

    def test_spark_bar_nested_font_size_reaches_svg(self):
        """chart.style.spark_bar.font.size (nested) propagates through cascade.

        Proves merge_onto_base re-validates nested BaseModel fields so
        spark_config.font stays a FontStyle instance, not a raw dict.
        """
        import pytest

        from dbt_charts.core.compile.config import (
            get_theme_style,
            reset_config,
        )
        from dbt_charts.core.compile.models.primitives import FontStyle
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.models.style.authored import (
            SparkBarChartStylePatch,
        )
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )
        from dbt_charts.core.render.chart.spark_bar import render_spark_bar_svg

        reset_config()
        board_style, board_context = resolve_style_and_context(get_theme_style())

        chart = SparkBarChart(
            id="test_sb",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="spark_bar",
            x="count",
            y="value",
            style=SparkBarChartStylePatch(font=FontStyle(size=99.0)),
        )
        data = [{"value": "A", "count": 100}, {"value": "B", "count": 50}]

        resolved = resolve(chart, data, chart_style_context=board_context)
        merged_charts = build_chart_style_context(board_context, chart)

        # font must be a FontStyle instance, not a raw dict
        assert hasattr(merged_charts.spark_bar.font, "size"), (
            "spark_config.font must be a FontStyle instance after merge_onto_base, not a dict"
        )
        assert merged_charts.spark_bar.font.size == pytest.approx(99.0)

        svg = render_spark_bar_svg(resolved, data, board_style=board_style)
        assert 'font-size="99.0"' in svg or 'font-size="99"' in svg, (
            "Expected font-size 99 in rendered SVG output"
        )


@pytest.mark.parametrize(
    ("mark_key", "required_stroke_fields"),
    [
        # LineMarkStyle: cap, join required by renderer. width is NOT
        # required at the theme tier -- the resolve layer (line.py/area.py)
        # fills it in from chart_rendering.stroke.fallback_width whenever no
        # cascade tier pins one and adaptive can't compute one either, so a
        # theme leaving it unset is the intended default, not a gap. Area's
        # top-edge stroke is a genuine separate line mark sourced from this
        # SAME global tier (AreaMarkStyle has no stroke of its own — fill
        # only: opacity/curve) so there's no separate "area" case to check.
        ("line", ["cap", "join"]),
        # GeoshapeMarkStyle: color, width required by renderer
        ("geoshape", ["color", "width"]),
    ],
)
def test_mark_family_stroke_required_fields_present(
    mark_key: str, required_stroke_fields: list[str]
) -> None:
    """Regression: required stroke sub-fields must be populated in the compiled theme.

    Strokes are now in global marks.<mark>.stroke. Mark style classes are cascade
    tier sentinels (all-Optional) so validation-error enforcement lives at the theme
    YAML level — the compiled theme must supply every field the renderer reads.
    """
    compiled = get_theme_style()
    mark_style = getattr(compiled.charts.marks, mark_key)
    assert mark_style.stroke is not None, f"marks.{mark_key}.stroke must be set"
    for field in required_stroke_fields:
        val = getattr(mark_style.stroke, field)
        assert val is not None, (
            f"marks.{mark_key}.stroke.{field} is required by the renderer but is None"
        )


def test_slice_stroke_required_fields_present() -> None:
    """SliceMarkStyle (marks.slice): color, width, join must be set in the compiled theme."""
    compiled = get_theme_style()
    slice_mark = compiled.charts.marks.slice
    assert slice_mark is not None, "marks.slice must be set"
    assert slice_mark.stroke is not None, "marks.slice.stroke must be set"
    for field in ("color", "width", "join"):
        val = getattr(slice_mark.stroke, field)
        assert val is not None, (
            f"marks.slice.stroke.{field} is required by the renderer but is None"
        )


def test_spark_empty_stroke_rejects_missing_sub_field() -> None:
    """SparkEmptyStyle: color, width, dasharray required by renderer."""
    from pydantic import ValidationError  # noqa: PLC0415

    from dbt_charts.core.compile.models.style.theme import SparkEmptyStyle

    compiled = get_theme_style()
    base = compiled.charts.table.spark.empty.model_dump()
    for field in ("color", "width", "dasharray"):
        d = {**base, "stroke": {**base["stroke"], field: None}}
        with pytest.raises(ValidationError, match=field):
            SparkEmptyStyle.model_validate(d)


# ---------------------------------------------------------------------------
# Deferred-inherit regression tests
#
# Bug: apply_inherit runs at board scope BEFORE chart-local patches are merged
# in build_chart_style_context. A chart-local patch that sets an InheritSlot SOURCE
# (e.g. kpi.font.color) leaves dependent leaves (kpi.value.font.color, which
# InheritSlots from kpi.font) stale — they hold the board-level inherit value,
# not the chart-local override.
#
# Fix: build_chart_style_context must merge the chart-local patch onto the PRE-
# inherit Style tree, then re-run apply_inherit + _finalize_charts per chart.
#
# These tests FAIL before the fix and PASS after.
# ---------------------------------------------------------------------------

# Sentinel colors not used anywhere in built-in theme YAML.
_KPI_SENTINEL = "#ff1234"
_TABLE_SENTINEL = "#ab5678"
_NEW_KPI_SENTINEL = "#aa1234"
# Font weights no built-in theme sets, so a fill is unambiguous.
_KPI_SENTINEL_WEIGHT = 771.0
_NEW_KPI_SENTINEL_WEIGHT = 813.0
# Distinctive font size not used by any built-in theme's support_table.font.
_SUPPORT_TABLE_FONT_SIZE = 99.0


def _board_resolved():
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,  # noqa: PLC0415
    )

    return resolve_chart_style_context(get_theme_style())


class TestKpiFontInheritPropagates:
    """A chart-local kpi.font patch must re-inherit kpi.value/label/affix/glyph.font.

    All four slots carry InheritSlot(from_path="Style.charts.kpi.font"), so
    re-running apply_inherit after the chart-local merge fills their unset
    leaves from the patch. `weight` pins that — it is unset on all four in the
    built-in themes.

    `color` is the one leaf those slots exclude from the fill. It stays a
    genuine sentinel so the renderer can tell "the author named this slot's ink"
    from "the KPI's ink cascaded down", which is what lets `style.value.font.color`
    beat the whole-chart `style.color` slot.
    """

    @staticmethod
    def _patched(**leaves):
        from dbt_charts.core.compile.models.primitives import FontStyle  # noqa: PLC0415
        from dbt_charts.core.compile.models.style.authored.kpi import (  # noqa: PLC0415
            KpiChartStylePatch,
        )
        from dbt_charts.core.compile.resolve.style.chart_context import (  # noqa: PLC0415
            build_chart_style_context,
        )

        chart = KpiChart(
            id="t",
            type="kpi",
            value="revenue",
            style=KpiChartStylePatch(font=FontStyle(**leaves)),
        )
        return build_chart_style_context(_board_resolved(), chart)

    @pytest.mark.parametrize("slot", ["value", "label", "affix", "glyph"])
    def test_kpi_font_weight_propagates(self, slot):
        got = getattr(self._patched(weight=_KPI_SENTINEL_WEIGHT).kpi, slot).font.weight
        assert got == _KPI_SENTINEL_WEIGHT, (
            f"kpi.{slot}.font.weight should be {_KPI_SENTINEL_WEIGHT} (InheritSlot "
            f"from kpi.font) but got {got!r} — apply_inherit ran before the "
            f"chart-local patch"
        )

    @pytest.mark.parametrize("slot", ["value", "label", "affix", "glyph"])
    def test_kpi_font_color_stays_a_sentinel(self, slot):
        result = self._patched(color=_KPI_SENTINEL)
        assert result.kpi.font.color == _KPI_SENTINEL
        got = getattr(result.kpi, slot).font.color
        assert got is None, (
            f"kpi.{slot}.font.color must stay None — the renderer reads it as "
            f"\"the author named this slot's ink\" and resolves the KPI's own "
            f"ink itself; got {got!r}"
        )


class TestTableFontInheritPropagates:
    """Chart-local table.font.color must propagate to table sub-element fonts.

    more_rows.font, empty_state.font, and title_row.font all carry
    InheritSlot(from_path="Style.charts.table.font") and have color=None in the
    theme, so apply_inherit fills them from table.font.color after the chart-local
    patch is merged.

    NOTE: table.header.font.color is NOT tested here because stark.yaml explicitly
    sets it to *color_ink (a non-None value). apply_inherit only fills None leaves;
    it does not override explicitly-set values. Asserting header.font.color would
    test incorrect expected behavior.
    """

    def test_table_font_color_propagates_to_more_rows(self):
        from dbt_charts.core.compile.models.primitives import FontStyle  # noqa: PLC0415
        from dbt_charts.core.compile.models.style.authored.table import (
            TableChartStylePatch,  # noqa: PLC0415
        )
        from dbt_charts.core.compile.resolve.style.chart_context import (  # noqa: PLC0415
            build_chart_style_context,
        )

        chart = TableChart(
            id="t",
            type="table",
            style=TableChartStylePatch(font=FontStyle(color=_TABLE_SENTINEL)),
        )
        result = build_chart_style_context(_board_resolved(), chart)
        assert result.table.more_rows.font.color == _TABLE_SENTINEL

    def test_table_font_color_propagates_to_empty_state(self):
        from dbt_charts.core.compile.models.primitives import FontStyle  # noqa: PLC0415
        from dbt_charts.core.compile.models.style.authored.table import (
            TableChartStylePatch,  # noqa: PLC0415
        )
        from dbt_charts.core.compile.resolve.style.chart_context import (  # noqa: PLC0415
            build_chart_style_context,
        )

        chart = TableChart(
            id="t",
            type="table",
            style=TableChartStylePatch(font=FontStyle(color=_TABLE_SENTINEL)),
        )
        result = build_chart_style_context(_board_resolved(), chart)
        assert result.table.empty_state.font.color == _TABLE_SENTINEL

    def test_table_font_color_propagates_to_title_row(self):
        from dbt_charts.core.compile.models.primitives import FontStyle  # noqa: PLC0415
        from dbt_charts.core.compile.models.style.authored.table import (
            TableChartStylePatch,  # noqa: PLC0415
        )
        from dbt_charts.core.compile.resolve.style.chart_context import (  # noqa: PLC0415
            build_chart_style_context,
        )

        chart = TableChart(
            id="t",
            type="table",
            style=TableChartStylePatch(font=FontStyle(color=_TABLE_SENTINEL)),
        )
        result = build_chart_style_context(_board_resolved(), chart)
        assert result.table.title_row.font.color == _TABLE_SENTINEL


class TestSupportTableFontInheritPropagates:
    """support_table.font.size must propagate to support_table.label.font.size.

    support_table.label.font inherits from support_table.font. support_table is authored
    under the (cartesian) family patch (style.<family>.support_table.font), so its
    font must be merged pre-inherit like the top-level family fonts or the label
    font keeps the stale board value.

    Note: we assert `size` (not `color`). stark.yaml sets label.font.color
    explicitly, so apply_inherit never fills it from support_table.font — asserting
    color propagation would pin behavior the cascade does not perform. size is
    None on label.font in the theme, so it genuinely inherits.
    """

    def test_support_table_font_size_propagates_to_label(self):
        from dbt_charts.core.compile.models.primitives import FontStyle  # noqa: PLC0415
        from dbt_charts.core.compile.models.style.authored import (
            SupportTableStylePatch,  # noqa: PLC0415
        )
        from dbt_charts.core.compile.models.style.authored.bar import (
            BarChartStylePatch,  # noqa: PLC0415
        )
        from dbt_charts.core.compile.resolve.style.chart_context import (  # noqa: PLC0415
            build_chart_style_context,
        )

        chart = BarChart(
            id="t",
            type="bar",
            style=BarChartStylePatch(
                support_table=SupportTableStylePatch(
                    font=FontStyle(size=_SUPPORT_TABLE_FONT_SIZE)
                )
            ),
        )
        result = build_chart_style_context(_board_resolved(), chart)
        assert result.support_table.label.font.size == _SUPPORT_TABLE_FONT_SIZE, (
            f"support_table.label.font.size should be {_SUPPORT_TABLE_FONT_SIZE!r} "
            f"(InheritSlot from support_table.font) but got "
            f"{result.support_table.label.font.size!r} — apply_inherit ran before "
            f"the chart-local patch"
        )


class TestFamilyPatchDoesNotChangeBoardMarks:
    """A chart-local family patch must not leak into board-global marks.

    build_chart_style_context only extracts the patched family slots from the
    re-inherited pre_style; board-global fields like charts.marks are
    never routed through the per-family merge, so they stay identical to the
    board-level value. A regression here would recompute charts.marks.text.font
    from charts.font (#222222) and shift bar-charts_5 (fill="#626366" →
    "#222222").

    Guard: when only bar.axis_x.format is patched, the per-chart result's
    marks.text field must equal the board-level value.
    Visual coverage: test_visual_snapshots.py bar_charts_5 is the golden gate.
    """

    def test_bar_axis_x_format_does_not_change_marks_text(self):
        from dbt_charts.core.compile.models.style.authored import (  # noqa: PLC0415
            AxisXStylePatch,
            BarChartStylePatch,
            DimensionLabelStylePatch,
        )
        from dbt_charts.core.compile.resolve.style.chart_context import (  # noqa: PLC0415
            build_chart_style_context,
        )

        board = _board_resolved()
        chart = BarChart(
            id="t",
            type="bar",
            x="product",
            y="revenue",
            style=BarChartStylePatch(
                axis_x=AxisXStylePatch(
                    labels=DimensionLabelStylePatch(format="currency_whole")
                )
            ),
        )
        result = build_chart_style_context(board, chart)
        # marks.text is board-global — a bar.axis_x.format patch must not touch it.
        board_text = board.marks.text
        assert result.marks.text == board_text, (
            f"marks.text changed after bar.axis_x.format patch — a family patch "
            f"leaked into board-global marks.\n"
            f"  board: {board_text}\n"
            f"  result: {result.marks.text}"
        )


class TestKpiFontPatchLeavesTableFamilyUnchanged:
    """A chart-local kpi.font patch must re-inherit kpi sub-elements without
    touching sibling families at all — identity, not just value equality.

    build_chart_style_context only extracts the patched family slots from the
    re-inherited pre_style; table (and every other unpatched family) is
    the same object reference as in board. A whole-tree re-finalize would
    fail this by creating new objects for every family.
    """

    def test_kpi_font_patch_propagates_to_value_leaf(self):
        from dbt_charts.core.compile.models.primitives import FontStyle  # noqa: PLC0415
        from dbt_charts.core.compile.models.style.authored.kpi import (  # noqa: PLC0415
            KpiChartStylePatch,
        )
        from dbt_charts.core.compile.resolve.style.chart_context import (  # noqa: PLC0415
            build_chart_style_context,
        )

        board = _board_resolved()
        chart = KpiChart(
            id="t",
            type="kpi",
            value="revenue",
            style=KpiChartStylePatch(font=FontStyle(weight=_KPI_SENTINEL_WEIGHT)),
        )
        result = build_chart_style_context(board, chart)
        assert result.kpi.value.font.weight == _KPI_SENTINEL_WEIGHT, (
            f"kpi.value.font.weight should be {_KPI_SENTINEL_WEIGHT!r} (per-family "
            f"inherit) but got {result.kpi.value.font.weight!r}"
        )

    def test_table_family_is_same_object_after_kpi_font_patch(self):
        """table must be the SAME object as in board — not just value-equal.

        Only kpi is extracted from the re-inherited style; table is never
        touched, so its object reference is preserved.
        """
        from dbt_charts.core.compile.models.primitives import FontStyle  # noqa: PLC0415
        from dbt_charts.core.compile.models.style.authored.kpi import (  # noqa: PLC0415
            KpiChartStylePatch,
        )
        from dbt_charts.core.compile.resolve.style.chart_context import (  # noqa: PLC0415
            build_chart_style_context,
        )

        board = _board_resolved()
        chart = KpiChart(
            id="t",
            type="kpi",
            value="revenue",
            style=KpiChartStylePatch(font=FontStyle(color=_KPI_SENTINEL)),
        )
        result = build_chart_style_context(board, chart)
        assert result.table is board.table, (
            "kpi.font patch must not rebuild the table family — "
            "result.table must be the same object as board.table."
        )


class TestKpiFontPatchPreservesSparkAndEmojiFamily:
    """After a chart-local kpi.font patch triggers re-inheritance, the spark
    family must still carry its seeded color AND the kpi value sub-element must
    still carry the emoji font (if the board had emoji enabled).

    This is a regression guard for per-family implementations that might:
    - Forget to append emoji to the rebuilt kpi sub-elements, or
    - Accidentally clear spark.color by working from the pre-emoji source.

    The spark check uses identity (spark must be the same object as in
    board, not just value-equal) — per-family leaves it untouched.
    The kpi.value.font.family check uses value equality to confirm emoji is
    still present in the rebuilt kpi sub-elements.
    """

    def test_spark_family_is_same_object_after_kpi_font_patch(self):
        """spark slot must not be rebuilt when only kpi.font is patched.

        Only kpi is extracted from the re-inherited style, so result.spark is
        the same object as board.spark.
        """
        from dbt_charts.core.compile.models.primitives import FontStyle  # noqa: PLC0415
        from dbt_charts.core.compile.models.style.authored.kpi import (  # noqa: PLC0415
            KpiChartStylePatch,
        )
        from dbt_charts.core.compile.resolve.style.chart_context import (  # noqa: PLC0415
            build_chart_style_context,
        )

        board = _board_resolved()
        chart = KpiChart(
            id="t",
            type="kpi",
            value="revenue",
            style=KpiChartStylePatch(font=FontStyle(color=_KPI_SENTINEL)),
        )
        result = build_chart_style_context(board, chart)
        assert result.spark is board.spark, (
            "kpi.font patch must not rebuild the spark family. "
            "result.spark must be the same object as board.spark."
        )

    def test_kpi_value_font_family_preserved_after_kpi_font_patch(self):
        """kpi.value.font.family must equal the board value after color-only patch.

        A color-only patch must not strip the emoji font family (or any other
        font-family tokens) from kpi sub-element fonts.
        """
        from dbt_charts.core.compile.models.primitives import FontStyle  # noqa: PLC0415
        from dbt_charts.core.compile.models.style.authored.kpi import (  # noqa: PLC0415
            KpiChartStylePatch,
        )
        from dbt_charts.core.compile.resolve.style.chart_context import (  # noqa: PLC0415
            build_chart_style_context,
        )

        board = _board_resolved()
        chart = KpiChart(
            id="t",
            type="kpi",
            value="revenue",
            style=KpiChartStylePatch(font=FontStyle(color=_KPI_SENTINEL)),
        )
        result = build_chart_style_context(board, chart)
        assert result.kpi.value.font.family == board.kpi.value.font.family, (
            f"kpi.value.font.family changed after color-only kpi.font patch — "
            f"emoji or base family was stripped.\n"
            f"  board: {board.kpi.value.font.family!r}\n"
            f"  result: {result.kpi.value.font.family!r}"
        )


class TestSparkColorSeededOnPerChartResolve:
    """spark.bar.color must stay seeded when the chart's own family is patched.

    single_series_palette-derived spark colors are seeded imperatively (lists
    can't be expressed via Inherit markers) — see _seed_spark_colors. The
    board-level resolve seeds them via _finalize_style/_finalize_charts, but
    build_chart_style_context()'s per-chart re-inherit path must independently seed
    them too whenever `table`/`spark_bar` themselves end up in the extracted
    family set — otherwise render's hard `assert ... is not None` on spark
    color crashes for any table/spark_bar chart carrying a style patch.
    """

    def test_spark_bar_chart_patch_keeps_bar_color_seeded(self):
        from dbt_charts.core.compile.models.style.authored import (  # noqa: PLC0415
            PaddingStylePatch,
            SparkBarChartStylePatch,
        )

        board = _board_resolved()
        assert board.spark_bar.bar.color is not None
        chart = SparkBarChart(
            id="t",
            type="spark_bar",
            style=SparkBarChartStylePatch(padding=PaddingStylePatch(top=5)),
        )
        result = build_chart_style_context(board, chart)
        assert result.spark_bar.bar.color == board.spark_bar.bar.color, (
            "spark_bar.bar.color must stay seeded from single_series_palette "
            "when the spark_bar chart itself carries an unrelated style patch.\n"
            f"  board: {board.spark_bar.bar.color!r}\n"
            f"  result: {result.spark_bar.bar.color!r}"
        )

    def test_table_chart_patch_keeps_spark_color_seeded(self):
        from dbt_charts.core.compile.models.style.authored.table import (  # noqa: PLC0415
            TableChartStylePatch,
        )

        board = _board_resolved()
        assert board.table.spark.color is not None
        assert board.table.spark.bar.color is not None
        chart = TableChart(
            id="t",
            type="table",
            style=TableChartStylePatch(background="#000000"),
        )
        result = build_chart_style_context(board, chart)
        assert result.table.spark.color == board.table.spark.color, (
            "table.spark.color must stay seeded when the table chart itself "
            "carries an unrelated style patch."
        )
        assert result.table.spark.bar.color == board.table.spark.bar.color, (
            "table.spark.bar.color must stay seeded when the table chart "
            "itself carries an unrelated style patch."
        )


class TestFontlessFamilyPatchResolves:
    """A chart-local override on a family with no ``font`` field must resolve.

    ``spark`` is a fontless, resolved-only family (``SparkStylePatch`` has no
    ``font``): it merges directly onto the resolved base. Guard: a table with an
    inline ``style.spark`` override must compile without error.
    """

    def test_chart_local_spark_override_resolves(self):
        from dbt_charts.core.compile.models.style.authored import (  # noqa: PLC0415
            SparkStylePatch,
            TableChartStylePatch,
        )
        from dbt_charts.core.compile.resolve.style.chart_context import (  # noqa: PLC0415
            build_chart_style_context,
        )

        chart = TableChart(
            id="t",
            type="table",
            style=TableChartStylePatch(spark=SparkStylePatch(color=_KPI_SENTINEL)),
        )
        result = build_chart_style_context(_board_resolved(), chart)
        # A table's inline spark override lands on the resolved table family's
        # spark (table-cell sparklines) — per-family style stays within its family.
        assert result.table.spark.color == _KPI_SENTINEL


class TestDiscriminatingRegressionIntraFamilyInherit:
    """Explicit value must NOT be overwritten, even when equal to the parent.

    apply_inherit fills only genuinely-None leaves. When kpi.value.font.weight is
    explicitly set to the same number the board cascade filled it with, it must
    survive — inheritance keys on the None sentinel, never on value equality, so
    an explicit value is never overwritten regardless of its content.

    Pinned on `weight`: `color` is excluded from these slots' fill entirely, so
    it cannot discriminate a preserved explicit value from a skipped one.
    """

    def test_explicit_value_not_overwritten_but_sentinel_is_filled(self):
        from dbt_charts.core.compile.models.primitives import FontStyle  # noqa: PLC0415
        from dbt_charts.core.compile.models.style.authored.kpi import (  # noqa: PLC0415
            KpiChartStylePatch,
        )
        from dbt_charts.core.compile.resolve.style.board import (  # noqa: PLC0415
            resolve_chart_style_context,
        )
        from dbt_charts.core.compile.resolve.style.chart_context import (  # noqa: PLC0415
            build_chart_style_context,
        )

        compiled = get_theme_style()
        inherited_weight = resolve_chart_style_context(compiled).kpi.font.weight
        # label.font.weight must be None to confirm it is a genuine sentinel
        assert compiled.charts.kpi.label.font.weight is None

        # Build a MODIFIED source Style where kpi.value.font.weight is EXPLICITLY
        # set to inherited_weight (not None sentinel).
        kpi = compiled.charts.kpi
        new_value = kpi.value.model_copy(
            update={
                "font": kpi.value.font.model_copy(update={"weight": inherited_weight})
            }
        )
        new_kpi = kpi.model_copy(update={"value": new_value})
        modified_compiled = compiled.model_copy(
            update={"charts": compiled.charts.model_copy(update={"kpi": new_kpi})}
        )
        board = resolve_chart_style_context(modified_compiled)

        # Apply chart-local patch kpi.font.weight = _NEW_KPI_SENTINEL_WEIGHT
        chart = KpiChart(
            id="t",
            type="kpi",
            value="revenue",
            style=KpiChartStylePatch(font=FontStyle(weight=_NEW_KPI_SENTINEL_WEIGHT)),
        )
        result = build_chart_style_context(board, chart)

        # Explicit value must NOT be overwritten even though it equaled old parent
        assert result.kpi.value.font.weight == inherited_weight, (
            f"kpi.value.font.weight was explicitly set to {inherited_weight!r} but "
            f"was overwritten to {result.kpi.value.font.weight!r} — the heuristic "
            f"confused an explicit value with an inherited sentinel"
        )
        # Genuine None sentinel IS filled by new parent value
        assert result.kpi.label.font.weight == _NEW_KPI_SENTINEL_WEIGHT, (
            f"kpi.label.font.weight should be {_NEW_KPI_SENTINEL_WEIGHT!r} (genuine "
            f"sentinel) but got {result.kpi.label.font.weight!r}"
        )


class TestChartLocalFontOverrideGetsEmojiFallback:
    """A chart-local font-family override gets the same emoji fallback as every
    other font.

    Board-level and family-root fonts have the bundled emoji family appended so
    emoji glyphs render. A chart-local override on a nested leaf must get the
    same treatment — a font stack that renders emoji at the board level but not
    when a chart overrides it would be a silent inconsistency.
    """

    def test_nested_leaf_font_family_override_appends_emoji(self):
        from dbt_charts.core.compile.models.style.authored import (
            KpiChartStylePatch,  # noqa: PLC0415
        )
        from dbt_charts.core.compile.resolve.style.chart_context import (  # noqa: PLC0415
            build_chart_style_context,
        )
        from dbt_charts.core.fonts import NOTO_EMOJI_FONT_FAMILY  # noqa: PLC0415

        # Default theme emoji mode is "monochrome" → NOTO_EMOJI_FONT_FAMILY.
        board = _board_resolved()
        assert NOTO_EMOJI_FONT_FAMILY in board.kpi.value.font.family
        chart = KpiChart(
            id="t",
            type="kpi",
            value="revenue",
            style=KpiChartStylePatch.model_validate(
                {"value": {"font": {"family": "TestFont"}}}
            ),
        )
        result = build_chart_style_context(board, chart)
        assert "TestFont" in result.kpi.value.font.family
        assert NOTO_EMOJI_FONT_FAMILY in result.kpi.value.font.family, (
            "chart-local nested font-family override must get the emoji fallback "
            f"appended like board-level fonts; got {result.kpi.value.font.family!r}"
        )
