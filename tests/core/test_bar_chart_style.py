"""Tests for BarStyle field wiring through the VL emit and render pipeline.

Covers:
- size → VL mark.width (literal pixel width, any scale)
- padding → VL encoding.x.scale.paddingInner (band-scale gutter)
- bar.axis_x.labels.padding → categorical axis labelPadding (vertical x, horizontal y after swap)
- bar.axis_x.labels.align → categorical axis labelAlign (vertical x, horizontal y after swap)
- horizontal bar value-axis labelFont pop (proportional, not tabular)
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    Chart,
    LineChart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AxisLabelStylePatch,
    AxisXStylePatch,
    AxisYStylePatch,
    BarChartStylePatch,
    BaseAxisStylePatch,
    DimensionLabelStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

from .conftest import chart_pane

_BOARD_RS, _BOARD_STYLE = resolve_style_and_context(get_theme_style())


def _mark(spec: dict) -> dict:
    """Get mark dict from single-spec, layered spec, or hconcat-wrapped spec."""
    # When endpoint labels are enabled (clarity default), line/area charts emit
    # hconcat with the main chart pane as hconcat[0].
    main = spec.get("hconcat", [spec])[0] if "hconcat" in spec else spec
    m = main.get("mark", {})
    if isinstance(m, dict) and m:
        return m
    layers = main.get("layer", [])
    return layers[0].get("mark", {}) if layers else {}


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _bar_chart(orientation: str | None = None, **kwargs) -> Chart:
    style = BarChartStylePatch(orientation=orientation) if orientation else None
    return BarChart(
        id="test_bar",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="bar",
        x="category",
        y="revenue",
        style=style,
        **kwargs,
    )


def _board_with_bar(**bar_mark_overrides):
    """Return (resolved_style, chart_style_context) with marks.bar fields set to distinctive test values.

    After ADR-015, bar geometry (size, padding, band_width, border) lives under
    charts.marks.bar (global mark tier), not on BarChartStyle directly.
    """
    compiled = get_theme_style()
    custom_bar_mark = compiled.charts.marks.bar.model_copy(update=bar_mark_overrides)
    custom_marks = compiled.charts.marks.model_copy(update={"bar": custom_bar_mark})
    custom_charts = compiled.charts.model_copy(update={"marks": custom_marks})
    return resolve_style_and_context(
        compiled.model_copy(update={"charts": custom_charts})
    )


def _board_with_bar_axis_x_label(*, theme: str | None = None, **label_overrides):
    """Return a ResolvedStyle with bar.axis_x.labels fields set to distinctive values.

    Uses the distinctive-value-plus-propagation pattern: sets a recognizable
    value via the cascade, then asserts it reaches the VL encoding.

    ``theme`` defaults to the project default (clarity), which ships
    ``axis_x.labels.align: inward`` — the own-side measured gutter overrides
    an authored ``label.padding`` on that theme, since own-side align always
    fires once it resolves to the axis's own edge. Callers asserting a plain
    authored-padding passthrough (no align in play) should pass
    ``theme="stark"``.
    """
    compiled = get_theme_style(theme)
    label_patch = AxisLabelStylePatch(**label_overrides)
    axis_x_patch = BaseAxisStylePatch(labels=label_patch)
    bar = compiled.charts.bar
    existing_axis_x = bar.axis_x
    if existing_axis_x is not None:
        new_axis_x = merge_onto_base(existing_axis_x, axis_x_patch)
    else:
        new_axis_x = axis_x_patch
    custom_bar = bar.model_copy(update={"axis_x": new_axis_x})
    custom_charts = compiled.charts.model_copy(update={"bar": custom_bar})
    return resolve_style_and_context(
        compiled.model_copy(update={"charts": custom_charts})
    )


SAMPLE_DATA = [
    {"category": "Alpha", "revenue": 100},
    {"category": "Beta", "revenue": 200},
    {"category": "Gamma", "revenue": 150},
]


MULTI_SERIES_DATA = [
    {"category": "Alpha", "revenue": 100, "region": "North"},
    {"category": "Alpha", "revenue": 80, "region": "South"},
    {"category": "Beta", "revenue": 120, "region": "North"},
    {"category": "Beta", "revenue": 90, "region": "South"},
]


class TestChartSortOrderEmitsCanonicalVLValues:
    """``ChartSort.order`` is stored as ``"asc" | "desc"`` (dbt charts canonical
    form), but Vega-Lite's ``sort.order`` accepts only ``"ascending"`` or
    ``"descending"``. The emit layer must translate at the boundary; passing
    raw ``"asc"``/``"desc"`` to VL silently falls back to ascending — the
    sort contract is broken and ``order: desc`` produces ascending output.

    Regression: chart-lab `bar_review` had `sort: {by: value, order: desc}`
    and rendered smallest-on-top (ascending behavior) instead of biggest-
    on-top (the documented intent).
    """

    def test_desc_emits_descending(self):
        chart = BarChart(
            id="t",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            x="category",
            y="revenue",
            sort={"by": "revenue", "order": "desc"},
            style=BarChartStylePatch(orientation="horizontal"),
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, SAMPLE_DATA)
        # Horizontal bar swaps x/y, so the categorical axis is VL.y
        sort_spec = spec["encoding"]["y"]["sort"]
        assert sort_spec["order"] == "descending", (
            f"expected order='descending' (canonical VL value), got "
            f"{sort_spec['order']!r}. VL silently treats unknown values "
            f"as ascending — the contract is broken if the raw 'desc' "
            f"string reaches VL."
        )

    def test_asc_emits_ascending(self):
        chart = BarChart(
            id="t",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            x="category",
            y="revenue",
            sort={"by": "revenue", "order": "asc"},
            style=BarChartStylePatch(orientation="horizontal"),
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, SAMPLE_DATA)
        sort_spec = spec["encoding"]["y"]["sort"]
        assert sort_spec["order"] == "ascending"


class TestHorizontalBarValueDescDefaultSort:
    def test_horizontal_bar_defaults_categorical_axis_to_value_desc_sort(self):
        """Unsorted horizontal bars default to largest measure at the top."""
        chart = _bar_chart(orientation="horizontal")
        resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, SAMPLE_DATA)

        y_enc = spec["encoding"]["y"]
        assert y_enc["type"] == "nominal"
        assert y_enc["sort"] == {"field": "revenue", "order": "descending"}
        assert "sort" not in spec["encoding"]["x"]

    def test_auto_flipped_horizontal_bar_gets_value_desc_default(self):
        """Implicit horizontal bars get the same value-desc default."""
        chart = _bar_chart()
        resolved = resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, SAMPLE_DATA)

        assert resolved.orientation == "horizontal"
        assert spec["encoding"]["y"]["sort"] == {
            "field": "revenue",
            "order": "descending",
        }

    def test_horizontal_bar_authored_sort_overrides_value_desc_default(self):
        """Authored sort wins over the horizontal-bar value-desc default."""
        chart = _bar_chart(
            orientation="horizontal",
            sort={"by": "category", "order": "asc"},
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, SAMPLE_DATA)

        assert spec["encoding"]["y"]["sort"] == {
            "field": "category",
            "order": "ascending",
            "op": "min",
        }

    def test_grouped_horizontal_bar_does_not_get_value_desc_default(self):
        """Grouped horizontal bars preserve query-owned category order."""
        chart = _bar_chart(orientation="horizontal", color="region")
        resolve(chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, MULTI_SERIES_DATA)

        assert spec["encoding"]["y"]["sort"] is None

    def test_stacked_horizontal_bar_does_not_get_value_desc_default(self):
        """Stacked horizontal bars preserve query-owned category order."""
        chart = _bar_chart(
            orientation="horizontal",
            color="region",
            stack="zero",
        )
        resolve(chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, MULTI_SERIES_DATA)

        assert chart_pane(spec)["encoding"]["y"]["sort"] is None

    def test_vertical_bar_does_not_get_value_desc_default(self):
        """The horizontal-bar default does not apply to vertical bars."""
        chart = _bar_chart(orientation="vertical")
        resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, SAMPLE_DATA)

        assert spec["encoding"]["x"].get("sort") is None

    def test_wide_stacked_horizontal_bar_preserves_query_category_order(self):
        """Folded measures keep the first category row at the top."""
        data = [
            {"category": "Before", "revenue": 40, "cost": 60},
            {"category": "After", "revenue": 10, "cost": 90},
        ]
        chart = BarChart(
            id="wide_horizontal",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="bar",
            x="category",
            y=["revenue", "cost"],
            stack="zero",
            style=BarChartStylePatch(orientation="horizontal"),
        )
        resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        assert chart_pane(spec)["encoding"]["y"]["sort"] is None


def _board_with_family_override(family: str, **mark_overrides):
    """Return (resolved_style, chart_style_context) with a mark-tier field overridden on the given family.

    After ADR-015, mark-level fields (halo_multiplier, stroke, opacity, etc.)
    live under charts.marks.<family> (global mark tier). Passing, for example,
    ``_board_with_family_override("line", halo_multiplier=0)`` now sets
    ``charts.marks.line.halo_multiplier = 0``.

    Used to disable halo on line/area so the no-halo branch runs.
    """
    compiled = get_theme_style()
    current_mark = getattr(compiled.charts.marks, family)
    custom_mark = current_mark.model_copy(update=mark_overrides)
    custom_marks = compiled.charts.marks.model_copy(update={family: custom_mark})
    custom_charts = compiled.charts.model_copy(update={"marks": custom_marks})
    return resolve_style_and_context(
        compiled.model_copy(update={"charts": custom_charts})
    )


class TestColorEncodedMarkOmitsPalette0Fill:
    """When a chart has a color encoding (any mode — series, gradient,
    conditional), the mark must NOT hardcode ``palette[0]`` as its
    fill/stroke. The palette[0] default is for charts WITHOUT a color
    encoding (single-series); when an encoding is present, the color
    scale (or conditional encoding) owns mark color.

    Regression: stacked column with ``color: region`` rendered every bar
    in palette[0] blue because the bar profile unconditionally emitted
    ``mark.fill = palette[0]``.

    Note on coverage: ``has_color_encoding`` in ``_map_mark`` is computed
    as ``color_ch is not None`` — broader than just series mode. The
    series-mode test below pins the most common case; gradient and
    conditional color encodings flow through the same guard, since they
    all populate ``resolved_chart.resolved_channels["color"]``.
    """

    def test_color_encoded_bar_mark_has_no_fill(self):
        chart = BarChart(
            id="test_bar_color",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="bar",
            x="category",
            y="revenue",
            color="region",
        )
        resolve(chart, MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, MULTI_SERIES_DATA)
        mark = _mark(spec)
        assert "fill" not in mark, (
            f"bar mark with color encoding should not hardcode fill — "
            f"got fill={mark.get('fill')!r}, which overrides the color scale"
        )

    def test_color_encoded_line_foreground_mark_has_no_stroke(self):
        """Forces ``halo_multiplier=0`` so the no-halo branch in
        ``_map_line`` runs, which calls ``_map_mark`` directly — the
        path the fix actually patches. With default theme halo on, the
        halo wrapping path builds its own ``fg_line`` without calling
        ``_map_mark``, so the test would silently pass without the fix.
        """
        chart = LineChart(
            id="test_line_color",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="line",
            x="category",
            y="revenue",
            color="region",
        )
        board_rs, board_style = _board_with_family_override("line", halo_multiplier=0)
        resolve(chart, MULTI_SERIES_DATA, chart_style_context=board_style)
        spec = generate_vega_lite_spec(
            chart,
            MULTI_SERIES_DATA,
            board_style=board_rs,
            chart_style_context=board_style,
        )
        # With halo off, the spec collapses to a single line mark (no layer).
        mark = _mark(spec)
        assert mark.get("type") == "line", (
            f"halo-off line should be a single-mark spec; got {mark!r}"
        )
        assert "stroke" not in mark, (
            f"line mark with color encoding (halo off) should not hardcode "
            f"stroke — got stroke={mark.get('stroke')!r}"
        )

    def test_color_encoded_area_foreground_mark_has_no_fill(self):
        """Same as the line case: force the no-halo branch in ``_map_area``
        so ``_map_mark``'s color guard is exercised. Area's edge-line halo
        lives on the shared ``marks.line`` tier (a genuine separate line
        mark); the fill backdrop lives on ``marks.area.backdrop`` and is
        independent of it — both must be disabled to collapse to a single
        fg mark, or ``_mark(spec)`` would return the backdrop layer instead."""
        chart = AreaChart(
            id="test_area_color",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="area",
            x="category",
            y="revenue",
            color="region",
        )
        compiled = get_theme_style()
        new_line = compiled.charts.marks.line.model_copy(update={"halo_multiplier": 0})
        new_area = compiled.charts.marks.area.model_copy(update={"backdrop": False})
        new_marks = compiled.charts.marks.model_copy(
            update={"line": new_line, "area": new_area}
        )
        charts = compiled.charts.model_copy(update={"marks": new_marks})
        board_rs, board_ctx = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )
        resolve(chart, MULTI_SERIES_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart,
            MULTI_SERIES_DATA,
            board_style=board_rs,
            chart_style_context=board_ctx,
        )
        mark = _mark(spec)
        assert mark.get("type") == "area", (
            f"halo-off area should be a single-mark spec; got {mark!r}"
        )
        assert "fill" not in mark, (
            f"area mark with color encoding (halo off) should not hardcode "
            f"fill — got fill={mark.get('fill')!r}"
        )

    def test_single_series_bar_keeps_palette0_fill(self):
        """Inverse: charts WITHOUT color encoding keep the palette[0] default —
        that's the single-series authoring contract."""
        chart = _bar_chart()  # no color encoding
        resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, SAMPLE_DATA)
        mark = _mark(spec)
        assert "fill" in mark and mark["fill"], (
            "single-series bar should keep palette[0] fill default"
        )


def _board_with_bar_border_color(color: str):
    """Return (resolved_style, chart_style_context) with marks.bar.border.color set to a distinctive value.

    After ADR-015, bar mark border lives under charts.marks.bar (global mark tier).
    """
    compiled = get_theme_style()
    custom_border = compiled.charts.marks.bar.border.model_copy(update={"color": color})
    custom_bar_mark = compiled.charts.marks.bar.model_copy(
        update={"border": custom_border}
    )
    custom_marks = compiled.charts.marks.model_copy(update={"bar": custom_bar_mark})
    custom_charts = compiled.charts.model_copy(update={"marks": custom_marks})
    return resolve_style_and_context(
        compiled.model_copy(update={"charts": custom_charts})
    )


class TestBarStrokeEmitsInVLConfig:
    """bar.border.color → VL mark.stroke (always emitted — required field)."""

    def test_distinctive_stroke_appears_in_vl_config(self):
        board_rs, board_ctx = _board_with_bar_border_color("#abcdef")
        chart = _bar_chart()
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert _mark(spec)["stroke"] == "#abcdef"

    def test_different_stroke_produces_different_output(self):
        rs_a, ctx_a = _board_with_bar_border_color("#111111")
        rs_b, ctx_b = _board_with_bar_border_color("#eeeeee")
        spec_a = generate_vega_lite_spec(
            _bar_chart(),
            SAMPLE_DATA,
            board_style=rs_a,
            chart_style_context=ctx_a,
        )
        spec_b = generate_vega_lite_spec(
            _bar_chart(),
            SAMPLE_DATA,
            board_style=rs_b,
            chart_style_context=ctx_b,
        )
        assert _mark(spec_a)["stroke"] != _mark(spec_b)["stroke"]


class TestBarSizeIsALiteralFixedWidth:
    """bar.size → VL mark.width, a literal pixel width, on ANY x scale."""

    def test_distinctive_size_appears_as_mark_width_on_band_scale(self):
        # _bar_chart() authors x="category" (string data) -> band scale.
        board_rs, board_ctx = _board_with_bar(size=42.0)
        chart = _bar_chart(orientation="vertical")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert _mark(spec)["width"] == 42.0

    def test_different_size_produces_different_output(self):
        rs_a, ctx_a = _board_with_bar(size=10.0)
        rs_b, ctx_b = _board_with_bar(size=30.0)
        spec_a = generate_vega_lite_spec(
            _bar_chart(orientation="vertical"),
            SAMPLE_DATA,
            board_style=rs_a,
            chart_style_context=ctx_a,
        )
        spec_b = generate_vega_lite_spec(
            _bar_chart(orientation="vertical"),
            SAMPLE_DATA,
            board_style=rs_b,
            chart_style_context=ctx_b,
        )
        assert _mark(spec_a)["width"] != _mark(spec_b)["width"]

    def test_continuousBandSize_never_emitted(self):
        # Dead VL key (measured: Vega-Lite's bar mark never reads it, on
        # any scale) — must never appear in the emitted spec.
        board_rs, board_ctx = _board_with_bar(size=42.0)
        chart = _bar_chart(orientation="vertical")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert "continuousBandSize" not in _mark(spec)


NUMERIC_X_DATA = [
    {"hour": 0, "count": 3},
    {"hour": 1, "count": 5},
    {"hour": 2, "count": 2},
    {"hour": 3, "count": 8},
]


def _numeric_x_bar_chart(**kwargs) -> Chart:
    return BarChart(
        id="test_numeric_bar",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="bar",
        x="hour",
        y="count",
        **kwargs,
    )


class TestBarWidthOnContinuousXScale:
    """A numeric x resolves to a quantitative (continuous) VL scale — no VL
    band exists for band_width/continuousBandSize to size against. bar.size
    (authored) is a literal width; unauthored, mark.width is a Vega
    expression computed from bar.gap/min_size/max_size.
    """

    def test_authored_size_is_a_literal_mark_width(self):
        board_rs, board_ctx = _board_with_bar(size=8.0)
        chart = _numeric_x_bar_chart()
        resolve(chart, NUMERIC_X_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, NUMERIC_X_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert _mark(spec)["width"] == 8.0

    def test_unauthored_size_emits_a_scale_based_expr(self):
        board_rs, board_ctx = _board_with_bar(gap=5.0, min_size=1.0, max_size=15.0)
        chart = _numeric_x_bar_chart()
        resolve(chart, NUMERIC_X_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, NUMERIC_X_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        width = _mark(spec)["width"]
        assert isinstance(width, dict) and "expr" in width
        expr = width["expr"]
        assert "scale('x'" in expr
        assert "- 5.0" in expr
        assert "1.0, 15.0)" in expr

    def test_continuousBandSize_never_emitted(self):
        board_rs, board_ctx = _board_with_bar()
        chart = _numeric_x_bar_chart()
        resolve(chart, NUMERIC_X_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, NUMERIC_X_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert "continuousBandSize" not in _mark(spec)
        assert "band" not in _mark(spec).get("width", {})


class TestBarPaddingEmitsPaddingInner:
    """bar.padding → encoding.x.scale.paddingInner (bar-specific, not global).

    VL only honors ``paddingInner`` at the encoding level; ``bandPaddingInner``
    works at the global config level only.
    """

    def test_distinctive_padding_appears_on_encoding_scale(self):
        board_rs, board_ctx = _board_with_bar(padding=0.7)
        chart = _bar_chart(orientation="vertical")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        x_scale = spec["encoding"]["x"].get("scale", {})
        assert x_scale.get("paddingInner") == pytest.approx(0.7)

    def test_different_padding_produces_different_output(self):
        rs_a, ctx_a = _board_with_bar(padding=0.1)
        rs_b, ctx_b = _board_with_bar(padding=0.9)
        spec_a = generate_vega_lite_spec(
            _bar_chart(orientation="vertical"),
            SAMPLE_DATA,
            board_style=rs_a,
            chart_style_context=ctx_a,
        )
        spec_b = generate_vega_lite_spec(
            _bar_chart(orientation="vertical"),
            SAMPLE_DATA,
            board_style=rs_b,
            chart_style_context=ctx_b,
        )
        assert (
            spec_a["encoding"]["x"]["scale"]["paddingInner"]
            != spec_b["encoding"]["x"]["scale"]["paddingInner"]
        )

    def test_padding_not_in_global_scale_config(self):
        """bar.padding must not pollute the global config.scale for non-bar charts."""
        board_rs, board_ctx = _board_with_bar(padding=0.7)
        chart = _bar_chart()
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert "scale" not in spec["config"] or "paddingInner" not in spec[
            "config"
        ].get("scale", {})


class TestBarAxisXLabelPadding:
    """bar.axis_x.labels.padding cascades to the bar categorical axis.

    For vertical bar: the categorical axis is VL x.
    For horizontal bar: the categorical axis is VL y — axis_x cascade drives
    encoding.y directly so labelPadding reaches the y-axis from the start.
    """

    def test_horizontal_bar_categorical_y_labelPadding_from_cascade(self):
        """bar.axis_x.labels.padding flows to horizontal bar's categorical y-axis.

        stark theme: clarity's default align: inward would resolve to
        own-side align and override the authored padding with a measured
        gutter (see TestBarAxisXLabelAlignInward) — this test isolates the
        plain passthrough.
        """
        board_rs, board_ctx = _board_with_bar_axis_x_label(theme="stark", padding=99.0)
        chart = _bar_chart(orientation="horizontal")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis.get("labelPadding") == pytest.approx(99.0)

    def test_vertical_bar_categorical_x_labelPadding_from_cascade(self):
        """bar.axis_x.labels.padding flows to vertical bar's categorical x-axis."""
        board_rs, board_ctx = _board_with_bar_axis_x_label(padding=99.0)
        chart = _bar_chart(orientation="vertical")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        x_axis = spec["encoding"]["x"]["axis"]
        assert x_axis.get("labelPadding") == pytest.approx(99.0)

    def test_padding_delta_horizontal_bar(self):
        """Delta between two padding values equals delta in labelPadding.

        stark theme: isolates the plain padding passthrough from clarity's
        default align: inward (own-side align would override both values
        with the same measured gutter, collapsing the delta to 0).
        """
        rs_low, ctx_low = _board_with_bar_axis_x_label(theme="stark", padding=0.0)
        rs_high, ctx_high = _board_with_bar_axis_x_label(theme="stark", padding=50.0)
        spec_low = generate_vega_lite_spec(
            _bar_chart(orientation="horizontal"),
            SAMPLE_DATA,
            board_style=rs_low,
            chart_style_context=ctx_low,
        )
        spec_high = generate_vega_lite_spec(
            _bar_chart(orientation="horizontal"),
            SAMPLE_DATA,
            board_style=rs_high,
            chart_style_context=ctx_high,
        )
        low_pad = spec_low["encoding"]["y"]["axis"].get("labelPadding", 0)
        high_pad = spec_high["encoding"]["y"]["axis"].get("labelPadding", 0)
        assert high_pad - low_pad == pytest.approx(50.0)


class TestBarAxisXLabelAlign:
    """bar.axis_x.labels.align cascades to the bar categorical axis.

    For vertical bar: emitted to VL x via the standard cascade.
    For horizontal bar: axis_x cascade drives VL y directly (categorical axis),
    so labelAlign reaches encoding.y from the start — no strip-and-reapply.
    """

    def test_horizontal_bar_categorical_y_labelAlign_from_cascade(self):
        """bar.axis_x.labels.align flows to horizontal bar's categorical y-axis.

        ``align="left"`` on the default left-orient categorical axis (matching
        the deleted ``categorical_orient`` field's static default) is the
        own-side/invading case — dbt charts computes a measured labelPadding
        for it rather than clipping (symmetric to the y-axis quantitative
        guard in vl_field_maps.py); see
        ``test_horizontal_bar_categorical_y_labelAlign_center_falls_back``
        below for the case that still falls back to the away-side default.
        """
        board_rs, board_ctx = _board_with_bar_axis_x_label(align="left")
        chart = _bar_chart(orientation="horizontal")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis.get("labelAlign") == "left"
        assert y_axis.get("labelPadding", 0) > 0

    def test_horizontal_bar_categorical_y_labelAlign_center_falls_back(self):
        """center invades the plot from either side (half-strength,
        regardless of measurability) — falls back to the away-side default
        rather than erroring, same as an own-side align that can't be
        measured. Also confirms "center" stays a valid authored value
        (needed for a bottom-orient category axis, e.g. examples/ai_spend's
        seasonal_hour_bar) rather than being dropped from the Literal.
        """
        board_rs, board_ctx = _board_with_bar_axis_x_label(align="center")
        chart = _bar_chart(orientation="horizontal")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis.get("labelAlign") is None

    def test_labelPadding_stays_capped_when_category_labels_exceed_labelLimit(self):
        """A category label far wider than Vega-Lite's own labelLimit (its
        180px default, since no theme sets axis.labels.max_width here) must
        not reserve gutter for its full untruncated width — VL truncates the
        rendered label with an ellipsis at labelLimit regardless, so a wider
        gutter only leaves dead whitespace between the truncated text and the
        axis (the exact bug this test pins).
        """
        board_rs, board_ctx = _board_with_bar_axis_x_label(align="left")
        chart = _bar_chart(orientation="horizontal")
        long_label_data = [
            {
                "category": "Connection timeout — upstream service returned a malformed response",
                "revenue": 100,
            },
            {"category": "Beta", "revenue": 200},
        ]
        resolve(chart, long_label_data, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, long_label_data, board_style=board_rs, chart_style_context=board_ctx
        )
        y_axis = spec["encoding"]["y"]["axis"]
        padding = y_axis.get("labelPadding", 0)
        assert 0 < padding <= 220  # labelLimit(180) + tickSize(5) + breathing room

    def test_chart_local_bar_axis_x_align_flows_to_horizontal_y(self):
        """Chart-local style.bar.axis_x.labels.align flows to horizontal bar y-axis."""
        style = BarChartStylePatch(
            orientation="horizontal",
            axis_x={"labels": {"align": "left"}},
        )
        chart = BarChart(
            id="test_bar",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="bar",
            x="category",
            y="revenue",
            style=style,
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, SAMPLE_DATA)
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis.get("labelAlign") == "left"


class TestBarAxisXLabelAlignInward:
    """bar.axis_x.labels.align: inward (theme switch) resolves to own-side
    align for a left/right-oriented categorical axis — reusing #5845's
    measured-padding path (see vl_field_maps.py / bar.py's own-side gate).

    Path 2's design: the switch lives on the existing per-family
    ``charts.bar.axis_x`` slot (Layer 4), not ``charts.axis_band`` (which
    would set it on every family). ``inward``/``outward``
    resolve to concrete left/right at resolve time (see
    dbt-charts/tests/core/compile/test_style_cascade.py's
    TestOwnSideAlignEdgeMapping for the mapping itself) — the emitter only
    ever sees the resolved left/right value.
    """

    def test_horizontal_bar_categorical_y_own_side_align_from_inward(self):
        """align: inward resolves to horizontal bar's left-orient categorical
        axis's own side and reserves a measured gutter — same effect as
        authoring align="left" explicitly, but via the theme switch."""
        board_rs, board_ctx = _board_with_bar_axis_x_label(align="inward")
        chart = _bar_chart(orientation="horizontal")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis.get("labelAlign") == "left"
        assert y_axis.get("labelPadding", 0) > 0

    def test_vertical_bar_categorical_x_inward_has_no_effect(self):
        """charts.bar.axis_x applies to both orientations, but a vertical
        bar's bottom category axis has no left/right edge to resolve inward
        against — it collapses to None, not an own-side align."""
        board_rs, board_ctx = _board_with_bar_axis_x_label(align="inward")
        chart = _bar_chart(orientation="vertical")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        x_axis = spec["encoding"]["x"]["axis"]
        assert x_axis.get("labelAlign") is None

    def test_horizontal_bar_authored_align_wins_over_inward(self):
        """An explicit chart-local style.axis_x.labels.align still wins over
        the theme's inward switch — chart-local (Layer 12) always wins over
        the theme's chart-type patch (Layer 4)."""
        board_rs, board_ctx = _board_with_bar_axis_x_label(align="inward")
        style = BarChartStylePatch(
            orientation="horizontal",
            axis_x=AxisXStylePatch(labels=DimensionLabelStylePatch(align="right")),
        )
        chart = BarChart(
            id="test_bar",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="bar",
            x="category",
            y="revenue",
            style=style,
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis.get("labelAlign") == "right"

    def test_horizontal_bar_empty_data_inward_no_error(self):
        """Zero rows means no category labels to measure — own-side align
        must fall back to the away-from-plot default (no own-side align, no
        RenderError), not invade with nothing to size the gutter from.

        Regression: clarity's default axis_x.labels.align: inward turned a
        valid empty render into an error card on any zero-row horizontal bar.
        """
        board_rs, board_ctx = _board_with_bar_axis_x_label(align="inward")
        chart = _bar_chart(orientation="horizontal")
        resolve(chart, [], chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, [], board_style=board_rs, chart_style_context=board_ctx
        )
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis.get("labelAlign") is None

    def test_clarity_default_theme_horizontal_bar_empty_data_no_error(self):
        """Same regression, exercised through the actual shipped clarity
        theme default (not a synthetic align=inward board)."""
        chart = _bar_chart(orientation="horizontal")
        resolve(chart, [], chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(
            chart, [], board_style=_BOARD_RS, chart_style_context=_BOARD_STYLE
        )
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis.get("labelAlign") is None

    def test_clarity_default_theme_horizontal_bar_label_expr_no_error(self):
        """An authored label.expr means VL renders that expression instead of
        the raw category value — measuring the raw value while a wider expr
        output actually renders would silently under-reserve the gutter, so
        own-side align must not invade here either. Under clarity's default
        axis_x.labels.align: inward, this must render cleanly (no
        RenderError), falling back to the away-from-plot default rather than
        raising.
        """
        style = BarChartStylePatch(
            orientation="horizontal",
            axis_x=AxisXStylePatch(
                labels=DimensionLabelStylePatch(expr="upper(datum.label)")
            ),
        )
        chart = BarChart(
            id="test_bar",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="bar",
            x="category",
            y="revenue",
            style=style,
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=_BOARD_RS, chart_style_context=_BOARD_STYLE
        )
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis.get("labelAlign") is None


# ── Horizontal bar value-axis labelFont: axis_y cascade ─────────────────────
#
# After upstream routing, horizontal bar's measure axis is VL x, driven by the
# axis_y cascade (dbt charts semantic: axis_y = measure axis regardless of
# orientation). The axis_quantitative layer within axis_y cascade applies to the
# measure axis, so labelFont from axis_quantitative reaches horizontal bar's VL x
# directly — no post-hoc pop. Authors control the measure-axis font via
# style.axis_y.labels.font.family (which feeds the axis_y channel cascade).

TABULAR_SEED = "'TabularSeed', sans-serif"


def _board_with_tabular_seed():
    """Seed axis_quantitative.labels.font.family with a distinctive tabular
    family so we can tell whether the cascade reached the encoding."""
    compiled = get_theme_style()
    aq_label_font = compiled.charts.axis_quantitative.labels.font.model_copy(
        update={"family": TABULAR_SEED}
    )
    aq_label = compiled.charts.axis_quantitative.labels.model_copy(
        update={"font": aq_label_font}
    )
    aq = compiled.charts.axis_quantitative.model_copy(update={"labels": aq_label})
    custom_charts = compiled.charts.model_copy(update={"axis_quantitative": aq})
    return resolve_style_and_context(
        compiled.model_copy(update={"charts": custom_charts})
    )


class TestHorizontalBarValueAxisLabelFont:
    def test_horizontal_value_axis_uses_quantitative_cascade(self):
        """Horizontal bar's measure axis (VL x) receives labelFont from the
        axis_quantitative cascade via axis_y.  The upstream routing maps
        axis_y → VL x for horizontal bar, so the full axis_y cascade (including
        axis_quantitative's labelFont) reaches encoding.x.axis directly."""
        board_rs, board_ctx = _board_with_tabular_seed()
        chart = _bar_chart(orientation="horizontal")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        x_axis = spec["encoding"]["x"]["axis"]
        assert x_axis.get("labelFont", "").startswith(
            TABULAR_SEED.split(",")[0].strip()
        )

    def test_vertical_value_axis_uses_quantitative_cascade(self):
        """Vertical bars: y-axis (measure) gets labelFont from axis_quantitative.
        Unchanged by the upstream-routing refactor."""
        board_rs, board_ctx = _board_with_tabular_seed()
        chart = _bar_chart(orientation="vertical")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis.get("labelFont", "").startswith(
            TABULAR_SEED.split(",")[0].strip()
        )

    def test_horizontal_bar_measure_axis_has_no_sort(self):
        """Horizontal bar's measure axis (VL x) must not carry sort: null.

        ``sort: null`` on a quantitative VL x encoding causes Vega to use a
        discrete band scale (one band per raw data value) instead of a linear
        scale.  The symptom is rotated axis labels showing the raw values
        (97000, 83000, …) rather than evenly-spaced quantitative ticks.
        """
        chart = _bar_chart(orientation="horizontal")
        resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, SAMPLE_DATA)
        x_enc = spec["encoding"]["x"]
        assert "sort" not in x_enc, (
            "sort key must be absent from a quantitative x encoding; "
            "sort: null on quantitative triggers VL's discrete band-scale path"
        )

    def test_horizontal_bar_value_axis_font_from_axis_y_cascade(self):
        """style.axis_y.labels.font.family reaches horizontal bar's measure axis.

        Under dbt charts semantics axis_y = measure axis.  For horizontal bar the
        measure axis is VL x, so an authored style.axis_y.labels.font.family
        override flows directly to encoding.x.axis.labelFont.
        """
        from dbt_charts.core.compile.models.primitives import FontStyle
        from dbt_charts.core.compile.models.style.authored import (
            AxisLabelStylePatch,
        )

        author_font = "'AuthorFont', sans-serif"
        style = BarChartStylePatch(
            orientation="horizontal",
            axis_y=AxisYStylePatch(
                labels=AxisLabelStylePatch(font=FontStyle(family=author_font))
            ),
        )
        chart = BarChart(
            id="test_bar",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="bar",
            x="category",
            y="revenue",
            style=style,
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, SAMPLE_DATA)
        x_axis = spec["encoding"]["x"]["axis"]
        assert x_axis.get("labelFont") == author_font
