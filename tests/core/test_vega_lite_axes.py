"""Tests for Vega-Lite axis orientation and format application.

Split from test_vega_lite.py; axis and format-specific tests.
"""

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.authored import ChartSort
from dbt_charts.core.compile.models.primitives import FontStyle, FormatConfig
from dbt_charts.core.compile.models.style.authored import (
    AxisLabelStylePatch,
    AxisTitleStylePatch,
    AxisXStylePatch,
    AxisYStylePatch,
    BandAxisStylePatch,
    BarChartStylePatch,
    BaseAxisStylePatch,
    BaseScaleStylePatch,
    DimensionLabelStylePatch,
    LineChartStylePatch,
    QuantitativeAxisStylePatch,
    ScaleContinuousStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.render.chart.vega_lite import (
    generate_vega_lite_spec,
    render_resolved_chart,
)

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())


def _bare_axis(
    *,
    tick_values: tuple[float, ...],
    format: str | None,
    align: str,
    tick_label: Any = None,
    prefix: str = "",
) -> Any:
    """Minimal duck-typed stand-in for a ``ResolvedAxisStyle`` covering only
    the attributes ``axis_to_vl``/``measure_axis_to_vl`` read — used to drive
    the axis-mapping layer directly for states the authored/cascade pipeline
    can't reach (e.g. an explicitly-None format the theme always overrides).

    ``tick_label`` accepts a real ``ResolvedTickLabel`` for the ladder-less
    ``si_format`` measurement path — the default ``None`` matches every
    existing caller's plain (non-ladder) shape.
    """
    from types import SimpleNamespace

    from dbt_charts.core.compile.models.primitives import ResolvedFormat

    resolved_format = (
        None
        if format is None
        else ResolvedFormat(spec=format, prefix=prefix, sign_placement="before_prefix")
    )
    return SimpleNamespace(
        position="right",
        labels=SimpleNamespace(
            align=align,
            font=SimpleNamespace(case="none", family="Inter", size=11.0),
            max_width=None,
            format=resolved_format,
            expr=None,
        ),
        title=SimpleNamespace(font=SimpleNamespace()),
        ticks=SimpleNamespace(length=None),
        grid=SimpleNamespace(),
        line=SimpleNamespace(),
        scale=None,
        tick_values=tick_values,
        tick_label=tick_label,
        ruler=None,
    )


class TestYAxisOrientation:
    """Tests for y-axis orientation defaults and overrides."""

    def test_y_axis_orient_right_by_default(self, make_chart):
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style=BarChartStylePatch(orientation="vertical"),
        )
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        # axisY orient lands at encoding level (not config.axisY)
        assert spec["encoding"]["y"]["axis"]["orient"] == "right"
        assert "axisY" not in spec.get("config", {})

    def test_y_axis_orient_override_via_style(self, make_chart):
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style=BarChartStylePatch(axis_y=AxisYStylePatch(position="left")),
        )
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        assert spec["encoding"]["y"]["axis"]["orient"] == "left"

    def test_y_axis_si_format_in_spec(self, make_chart):
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style=BarChartStylePatch(orientation="vertical"),
        )
        data = [{"month": "Jan", "revenue": 1_500_000}]
        spec = generate_vega_lite_spec(chart, data)
        fmt = spec["encoding"]["y"]["axis"].get("format", "")
        assert "$" in fmt or "s" in fmt, f"Expected currency/SI format, got {fmt}"

    def test_y_axis_no_si_format_for_small_values(self, make_chart):
        chart = make_chart("bar", x="month", y="revenue")
        data = [{"month": "Jan", "revenue": 500}]
        spec = generate_vega_lite_spec(chart, data)
        fmt = spec["encoding"]["y"]["axis"].get("format", "")
        assert "~s" not in fmt, f"Small values should not get SI prefix, got {fmt}"

    def test_horizontal_bar_no_orient_on_x(self, make_chart):
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style=BarChartStylePatch(orientation="horizontal"),
        )
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        x_axis = spec["encoding"]["x"].get("axis", {})
        assert "orient" not in x_axis

    def test_horizontal_bar_y_axis_orient_left(self, make_chart):
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style=BarChartStylePatch(orientation="horizontal"),
        )
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        y_axis = spec["encoding"]["y"].get("axis", {})
        assert y_axis.get("orient") == "left"

    def test_horizontal_bar_chart_local_axis_x_label_padding_reaches_y(
        self, make_chart
    ):
        """Chart-local style.axis_x.labels.padding flows to horizontal bar's
        categorical y-axis.  Under dbt charts semantics axis_x = categorical axis;
        for horizontal bar the categorical axis is VL y, so axis_x cascade drives
        encoding.y directly — labelPadding reaches the y-axis from the start.

        This supersedes the old bar.label.axis_padding slot which has been
        dissolved into bar.axis_x.labels.padding (the cascade-canonical tier).

        Uses the stark theme (no default align): editorial's default
        axis_x.labels.align: inward would resolve to own-side align and
        override the authored padding with a measured gutter (see
        test_bar_chart_style.py's TestBarAxisXLabelAlignInward) — this test
        isolates the plain passthrough.
        """
        stark_rs, stark_ctx = resolve_style_and_context(get_theme_style("stark"))
        chart = make_chart(
            "bar",
            x="product",
            y="revenue",
            style=BarChartStylePatch(
                orientation="horizontal",
                axis_x=AxisXStylePatch(labels=DimensionLabelStylePatch(padding=99)),
            ),
        )
        data = [{"product": "Widget A", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=stark_ctx)
        spec = generate_vega_lite_spec(
            chart, data, board_style=stark_rs, chart_style_context=stark_ctx
        )
        y_axis = spec["encoding"]["y"].get("axis", {})
        # axis_x.labels.padding flows through to the post-swap categorical y-axis.
        assert y_axis.get("labelPadding") == 99

    def test_x_axis_options_pass_through_to_encoding(self, make_chart):
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style=BarChartStylePatch(
                orientation="vertical",
                axis_x=AxisXStylePatch(
                    labels=DimensionLabelStylePatch(angle=-45),
                ),
            ),
        )
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        x_axis = spec["encoding"]["x"].get("axis", {})
        assert x_axis.get("labelAngle") == -45

    def test_horizontal_bar_sort_applies_to_categorical_axis(self, make_chart):
        chart = make_chart(
            "bar",
            x="product",
            y="revenue",
            style=BarChartStylePatch(orientation="horizontal"),
            sort=ChartSort(by="revenue", order="desc"),
        )
        data = [
            {"product": "Widget A", "revenue": 66300},
            {"product": "Widget B", "revenue": 86500},
        ]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        # dbt charts 'desc' translates to VL canonical 'descending' at emit time.
        # Nothing stacks here, so the measure sort means each product's own
        # revenue — bar pins that aggregate rather than leaving it to VL.
        assert spec["encoding"]["y"]["sort"] == {
            "field": "revenue",
            "order": "descending",
            "op": "min",
        }

    def test_horizontal_bar_default_label_style(self, make_chart):
        """Horizontal bar's categorical y-axis has non-zero labelPadding by default.

        labelAlign is not emitted explicitly — VL defaults to right-aligned
        labels on a left-oriented y-axis, so no explicit override is needed.
        Authors who want a non-default alignment use style.bar.axis_x.labels.align.
        """
        chart = make_chart(
            "bar",
            x="product",
            y="revenue",
            style=BarChartStylePatch(orientation="horizontal"),
        )
        data = [
            {"product": "Long Product Label", "revenue": 100},
            {"product": "Short", "revenue": 80},
        ]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        y_axis = spec["encoding"]["y"].get("axis", {})
        assert y_axis.get("labelPadding", 0) > 0

    def test_layered_spec_reuses_profile_y_defaults_and_format(self, make_chart):
        chart = make_chart(
            "line",
            x="month",
            y=["revenue", "cost"],
            style=LineChartStylePatch(
                axis_y=AxisYStylePatch(
                    scale=BaseScaleStylePatch(
                        continuous=ScaleContinuousStylePatch(zero=False)
                    )
                ),
                number_format="$,.0f",
            ),
        )
        data = [{"month": "Jan", "revenue": 100, "cost": 50}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)

        # Fold-based multi-metric line: y encoding is top-level, not per-layer.
        # Endpoint labels (editorial theme) wrap the spec in hconcat.
        unit = spec.get("hconcat", [spec])[0]
        assert "layer" in unit, "expected fold-based layered spec"
        y_enc = unit["encoding"]["y"]
        assert y_enc["format"] == "$,.0f"
        assert y_enc["axis"]["format"] == "$,.0f"
        assert y_enc["scale"]["zero"] is False


class TestAxisYLabelAlignInvasion:
    """axis_y.labels.align must never leave labels clipped by growing back
    across the axis line with no reserved gutter.

    Vega-Lite's ``autosize: {type: "fit"}`` sizing estimate reserves gutter
    width for a left/right-oriented axis only when labels grow AWAY from the
    plot (the per-orient smart default VL applies when ``labelAlign`` is
    unset). Explicitly setting ``label.align`` to the axis's OWN side
    (``"right"`` on a right-orient axis, ``"left"`` on a left-orient axis)
    flips the text-anchor so labels grow back toward the plot instead.
    dbt charts computes an explicit ``labelPadding`` from the real (baked)
    tick values and font metrics, which the same vl-convert probe found
    widens the reserved gutter 1:1 regardless of align direction — so the
    combination renders correctly provided dbt charts has baked tick content
    to measure from. When it doesn't (no format, no baked ticks, an authored
    labelExpr, or an upper/lower font.case), own-side align falls back to
    the away-side default instead of reserving an unmeasured gutter — the
    render-time safety net for facts resolve can't see when it maps
    ``inward``/``outward`` to a concrete side (see the ``_falls_back`` tests
    below).
    """

    def test_right_orient_align_right_computes_measured_padding(self, make_chart):
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style=LineChartStylePatch(
                axis_y=AxisYStylePatch(
                    position="right",
                    labels=AxisLabelStylePatch(align="right"),
                )
            ),
        )
        data = [{"month": "Jan", "revenue": 100}]
        spec = generate_vega_lite_spec(chart, data)
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis["labelAlign"] == "right"
        assert y_axis["labelPadding"] > 0

    def test_left_orient_align_left_computes_measured_padding(self, make_chart):
        """Symmetric case: left-orient axis + align: left grows the same way."""
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style=LineChartStylePatch(
                axis_y=AxisYStylePatch(
                    position="left",
                    labels=AxisLabelStylePatch(align="left"),
                )
            ),
        )
        data = [{"month": "Jan", "revenue": 100}]
        spec = generate_vega_lite_spec(chart, data)
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis["labelAlign"] == "left"
        assert y_axis["labelPadding"] > 0

    def test_notation_on_a_non_si_spec_does_not_discard_alignment_or_padding(
        self, make_chart
    ):
        """Authoring `notation` on a fixed-point spec must not compose a labelExpr byte-
        identical to `format`.
        """

        def _spec(fmt: str | FormatConfig) -> dict:
            chart = make_chart(
                "line",
                x="month",
                y="revenue",
                style=LineChartStylePatch(
                    axis_y=AxisYStylePatch(
                        position="right",
                        labels=AxisLabelStylePatch(align="right", format=fmt),
                    ),
                ),
            )
            data = [{"month": "Jan", "revenue": 1_000_000}]
            return generate_vega_lite_spec(chart, data)["encoding"]["y"]["axis"]

        without_notation = _spec(",.0f")
        with_notation = _spec(FormatConfig(spec=",.0f", notation="narrative"))

        assert with_notation["labelAlign"] == without_notation["labelAlign"] == "right"
        assert with_notation["labelPadding"] == pytest.approx(
            without_notation["labelPadding"]
        )
        assert with_notation["labelPadding"] > 0
        assert "labelExpr" not in with_notation
        assert with_notation["format"] == without_notation["format"] == ",.0f"

    def test_measured_padding_excludes_tick_length(self, make_chart):
        """Regression: Vega-Lite already adds the axis's own tick length
        before applying ``labelPadding`` (confirmed via a direct vl-convert
        probe: the rendered label anchor lands at ``tickSize + labelPadding``
        whenever ticks are visible, ``labelPadding`` alone when they aren't).
        Adding tick length again inside the computed padding — as this code
        used to — double-counts it whenever ticks are visible and reserves
        unearned space when they aren't, pushing every own-side-aligned
        label further from the axis than intended. The computed labelPadding
        must equal exactly the widest label's measured width plus the fixed
        cosmetic breathing room, with no tick-length term at all.
        """
        import vl_convert as vlc

        from d3_format import format as d3_format
        from dbt_charts.core.font_measure import get_font_measurer

        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style=LineChartStylePatch(
                number_format=",.0f",  # raw literal spec -- predictable "30,000" content
                axis_y=AxisYStylePatch(
                    position="right",
                    labels=AxisLabelStylePatch(align="right"),
                ),
            ),
        )
        data = [{"month": "Jan", "revenue": 30_000}]
        spec = generate_vega_lite_spec(chart, data)
        y_axis = spec["encoding"]["y"]["axis"]

        measurer = get_font_measurer(y_axis["labelFont"])
        widest = d3_format(y_axis["format"], 30_000)
        assert widest == "30,000"
        expected = measurer.measure(widest, y_axis["labelFontSize"]) + 4.0
        assert y_axis["labelPadding"] == pytest.approx(expected)

        # Real-Vega proof, not just the Python-side formula: parse the
        # actual rendered SVG and confirm the label anchor lands exactly at
        # tick_size (if ticks are visible; 0 otherwise) + labelPadding --
        # never at 2x tick_size + labelPadding, which is what the
        # double-counting bug produced.
        import re

        svg = vlc.vegalite_to_svg(spec)
        tick_size = y_axis["tickSize"] if y_axis.get("ticks") else 0.0
        expected_anchor_x = tick_size + y_axis["labelPadding"]
        rendered = re.search(
            r'text-anchor="end" transform="translate\(([\d.]+),[^)]*\)"[^>]*>30,000<',
            svg,
        )
        assert rendered is not None, svg
        assert float(rendered.group(1)) == pytest.approx(expected_anchor_x, abs=0.5)

    def test_measured_padding_grows_with_wider_labels(self, make_chart):
        """The computed labelPadding tracks the actual label text width — the
        whole point of measuring instead of a fixed padding constant.
        """

        def _padding(revenue: float) -> float:
            chart = make_chart(
                "line",
                x="month",
                y="revenue",
                style=LineChartStylePatch(
                    axis_y=AxisYStylePatch(
                        position="right",
                        labels=AxisLabelStylePatch(align="right"),
                    )
                ),
            )
            data = [{"month": "Jan", "revenue": revenue}]
            spec = generate_vega_lite_spec(chart, data)
            return float(spec["encoding"]["y"]["axis"]["labelPadding"])

        assert _padding(5) < _padding(1_500_000)

    def test_own_side_align_measures_tick_label_not_label_format(self, make_chart):
        """A non-compacting ladder bakes ``tick_label.format`` (plain,
        comma-grouped digits, e.g. ``60,000``) while ``labels.format`` stays
        the theme's untouched SI default (``.3~s``, e.g. ``60k``) -- Vega
        paints the plain-digit text via ``labelExpr``, so the gutter must be
        measured from that, not from the shorter SI string. Contrasted
        against an explicitly authored ``.3~s`` (which suppresses the bake
        entirely, per ``ResolvedAxisStyle.tick_label``'s authored-format
        gate) so both cases share the same data and ticks and differ only
        in which string the padding is measured from. If
        ``measure_axis_to_vl`` ever stops preferring ``tick_label``
        over ``labels.format``, both paddings collapse to the SI-measured
        value and this assertion catches it.
        """

        def _padding(labels_patch: AxisLabelStylePatch) -> float:
            chart = make_chart(
                "line",
                x="month",
                y="revenue",
                style=LineChartStylePatch(
                    axis_y=AxisYStylePatch(position="right", labels=labels_patch)
                ),
            )
            data = [
                {"month": "Jan", "revenue": 500},
                {"month": "Feb", "revenue": 60_000},
            ]
            spec = generate_vega_lite_spec(chart, data)
            return float(spec["encoding"]["y"]["axis"]["labelPadding"])

        # Omitting `format` entirely (not passing format=None, which the
        # patch-merge cascade treats as an explicit "clear the inherited
        # value" authoring, not "unauthored") leaves it unauthored -- the
        # theme's SI default bakes tick_label instead.
        unauthored_padding = _padding(AxisLabelStylePatch(align="right"))
        authored_si_padding = _padding(
            AxisLabelStylePatch(align="right", format=".3~s")
        )
        assert unauthored_padding > authored_si_padding

    def test_own_side_align_without_baked_format_falls_back(self):
        """No exact-string guarantee without a concrete format means dbt charts
        can't safely measure a labelPadding — falls back to the away-side
        default rather than guessing at VL's own auto-format derivation. The
        theme cascade always supplies a concrete format for a real
        quantitative measure axis, so this state is exercised directly
        against the axis-mapping layer rather than through the full
        authored/cascade pipeline.
        """
        from dbt_charts.core.render.chart.vl_field_maps import measure_axis_to_vl

        axis = _bare_axis(tick_values=(0.0, 100.0), format=None, align="right")
        d = measure_axis_to_vl(axis, [], ())
        assert "labelAlign" not in d

    def test_center_on_left_right_axis_falls_back_regardless_of_measurability(
        self,
    ):
        """center invades at half strength regardless of baked tick content —
        no single-sided labelPadding fixes a bidirectional gutter, so it
        always falls back, even with a fully measurable format + baked ticks
        (unlike an own-side align, which only falls back when unmeasurable).
        """
        from dbt_charts.core.render.chart.vl_field_maps import measure_axis_to_vl

        axis = _bare_axis(tick_values=(0.0, 100.0), format="~s", align="center")
        d = measure_axis_to_vl(axis, [], ())
        assert "labelAlign" not in d

    def test_own_side_align_on_stark_theme_now_computes_padding(self, make_chart):
        """End-to-end regression: ``stark`` (and ``plain``) leave
        ``axis_quantitative.ticks.count`` unset, so ``tick_values`` never
        bakes there — the exact case reported as still erroring. Must now
        compute a labelPadding from the real chart data instead of rejecting.
        """
        stark_rs, stark_ctx = resolve_style_and_context(get_theme_style("stark"))
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style=LineChartStylePatch(
                axis_y=AxisYStylePatch(
                    position="right",
                    labels=AxisLabelStylePatch(align="right"),
                )
            ),
        )
        data = [
            {"month": "Jan", "revenue": 5},
            {"month": "Feb", "revenue": 1_500_000},
        ]
        spec = generate_vega_lite_spec(
            chart, data, board_style=stark_rs, chart_style_context=stark_ctx
        )
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis["labelAlign"] == "right"
        assert y_axis["labelPadding"] > 0

    def test_estimate_covers_an_authored_domain_wider_than_the_data(self, make_chart):
        """An authored ``scale.domain`` is a first-class field VL renders
        ticks up to, regardless of the actual data range. Estimating from
        `data` alone would under-reserve when the authored domain max is
        wider than any data value — silently reintroducing the clip this
        task exists to eliminate.
        """
        stark_rs, stark_ctx = resolve_style_and_context(get_theme_style("stark"))
        chart_narrow = make_chart(
            "line",
            x="month",
            y="revenue",
            style=LineChartStylePatch(
                axis_y=AxisYStylePatch(
                    position="right",
                    labels=AxisLabelStylePatch(align="right"),
                )
            ),
        )
        chart_wide_domain = make_chart(
            "line",
            x="month",
            y="revenue",
            style=LineChartStylePatch(
                axis_y=AxisYStylePatch(
                    position="right",
                    labels=AxisLabelStylePatch(align="right"),
                    scale=BaseScaleStylePatch(
                        continuous=ScaleContinuousStylePatch(domain=[0, 2_000_000])
                    ),
                )
            ),
        )
        # Same (narrow) data for both — only the authored domain differs.
        data = [{"month": "Jan", "revenue": 5}, {"month": "Feb", "revenue": 500}]
        narrow_padding = generate_vega_lite_spec(
            chart_narrow, data, board_style=stark_rs, chart_style_context=stark_ctx
        )["encoding"]["y"]["axis"]["labelPadding"]
        wide_domain_padding = generate_vega_lite_spec(
            chart_wide_domain, data, board_style=stark_rs, chart_style_context=stark_ctx
        )["encoding"]["y"]["axis"]["labelPadding"]
        assert wide_domain_padding > narrow_padding

    def test_own_side_align_without_baked_ticks_estimates_from_data(self):
        """No baked tick_values (e.g. a theme like ``stark`` that leaves
        ``axis.ticks.count`` unset) no longer means an automatic reject — as
        long as the actual data domain is known, dbt charts can still bound the
        widest label Vega-Lite could plausibly render and compute a
        labelPadding from that estimate.
        """
        from dbt_charts.core.render.chart.vl_field_maps import measure_axis_to_vl

        axis = _bare_axis(tick_values=(), format="~s", align="right")
        data = [{"revenue": 5}, {"revenue": 1_500_000}]
        d = measure_axis_to_vl(axis, data, ("revenue",))
        assert d["labelPadding"] > 0

    def test_own_side_align_without_baked_ticks_or_data_falls_back(self):
        """No baked tick_values AND no data domain to estimate from (e.g. a
        bar chart's ``aggregate: count`` measure axis, whose values Vega-Lite
        computes client-side) — no safe basis to measure, so it falls back
        to the away-side default instead of reserving an unmeasured gutter.
        """
        from dbt_charts.core.render.chart.vl_field_maps import measure_axis_to_vl

        axis = _bare_axis(tick_values=(), format="~s", align="right")
        d = measure_axis_to_vl(axis, [], ())
        assert "labelAlign" not in d

    def test_own_side_align_si_format_arm_reads_the_ladders_own_affix(self) -> None:
        """A ladder-less axis (``tick_label.si_format`` set) must measure the authored
        affix carried on ``labels.format``.
        """
        from dbt_charts.core.compile.models.style.resolved import ResolvedTickLabel
        from dbt_charts.core.render.chart.vl_field_maps import measure_axis_to_vl

        tick_label_with_affix = ResolvedTickLabel(
            format="d", si_format=".3~s", scientific_format="d"
        )
        axis_with_affix = _bare_axis(
            tick_values=(),
            format="d",
            align="right",
            tick_label=tick_label_with_affix,
            prefix="EUR ",
        )
        tick_label_bare = ResolvedTickLabel(
            format="d", si_format=".3~s", scientific_format="d"
        )
        axis_bare = _bare_axis(
            tick_values=(), format="d", align="right", tick_label=tick_label_bare
        )
        data = [{"revenue": 5}, {"revenue": 1_500_000}]
        with_affix = measure_axis_to_vl(axis_with_affix, data, ("revenue",))[
            "labelPadding"
        ]
        bare = measure_axis_to_vl(axis_bare, data, ("revenue",))["labelPadding"]
        assert with_affix > bare

    def test_own_side_align_with_label_expr_falls_back(self, make_chart):
        """An authored ``label.expr`` (labelExpr) renders in preference to
        ``format`` in Vega-Lite — measuring from the d3-format string while
        the axis actually renders the expr's (possibly wider) output would
        silently under-reserve the gutter. Baked ticks + a concrete format
        both present, but the expr override still forces the fallback rather
        than an unmeasured own-side align.
        """
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style=LineChartStylePatch(
                axis_y=AxisYStylePatch(
                    position="right",
                    labels=AxisLabelStylePatch(
                        align="right", expr="datum.label + ' units'"
                    ),
                )
            ),
        )
        data = [{"month": "Jan", "revenue": 100}]
        spec = generate_vega_lite_spec(chart, data)
        assert "labelAlign" not in spec["encoding"]["y"]["axis"]

    def test_own_side_align_with_native_predefined_format_falls_back(self, make_chart):
        """A PREDEFINED_NATIVE format name (e.g. "percent_number") bypasses
        d3 entirely -- it paints via a Python lambda (KPI/table slots only),
        not a d3-format spec. Measuring it here would call d3_format() on a
        bare name like "percent_number" and raise D3FormatError deep in
        render -- falls back to the away-side default instead, the same
        safety net as an authored label.expr.
        """
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style=LineChartStylePatch(
                number_format="percent_number",
                axis_y=AxisYStylePatch(
                    position="right",
                    labels=AxisLabelStylePatch(align="right"),
                ),
            ),
        )
        data = [{"month": "Jan", "revenue": 100}]
        spec = generate_vega_lite_spec(chart, data)
        assert "labelAlign" not in spec["encoding"]["y"]["axis"]

    def test_own_side_align_with_font_case_falls_back(self, make_chart):
        """``label.font.case: upper``/``lower`` injects a ``labelExpr`` too
        (``inject_axis_label_case``, applied after ``measure_axis_to_vl``) —
        the same VL labelExpr-over-format precedence the explicit-``expr``
        test above guards against, just injected downstream instead of
        authored directly. Falls back to the away-side default rather than
        measuring the un-cased d3-format string while a cased
        (different-width) string actually renders.
        """
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style=LineChartStylePatch(
                axis_y=AxisYStylePatch(
                    position="right",
                    labels=AxisLabelStylePatch(
                        align="right", font=FontStyle(case="upper")
                    ),
                )
            ),
        )
        data = [{"month": "Jan", "revenue": 100}]
        spec = generate_vega_lite_spec(chart, data)
        assert "labelAlign" not in spec["encoding"]["y"]["axis"]

    def test_right_orient_align_left_is_safe(self, make_chart):
        """align matching the away-from-plot direction is a no-op vs. the default.

        Uses an explicit RAW (non-predefined) format: a house-alias-derived
        format (including the theme's own unauthored default) is now
        force-right-aligned regardless of an authored align, which is a
        different, deliberate behavior this test doesn't exercise -- see
        TestAxisYLabelAlignInvasion's house-alias-specific tests for that.
        """
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style=LineChartStylePatch(
                axis_y=AxisYStylePatch(
                    position="right",
                    labels=AxisLabelStylePatch(align="left", format=".2f"),
                )
            ),
        )
        data = [{"month": "Jan", "revenue": 100}]
        spec = generate_vega_lite_spec(chart, data)
        assert spec["encoding"]["y"]["axis"]["labelAlign"] == "left"

    def test_left_orient_align_right_is_safe(self, make_chart):
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style=LineChartStylePatch(
                axis_y=AxisYStylePatch(
                    position="left",
                    labels=AxisLabelStylePatch(align="right"),
                )
            ),
        )
        data = [{"month": "Jan", "revenue": 100}]
        spec = generate_vega_lite_spec(chart, data)
        assert spec["encoding"]["y"]["axis"]["labelAlign"] == "right"

    def test_default_align_unset_never_rejected(self, make_chart):
        """No explicit align — VL's own per-orient default — is always safe.

        Uses an explicit RAW (non-predefined) format: the theme's own
        unauthored default format is a predefined name (number),
        which now gets an explicit forced labelAlign regardless of whether
        anything was authored -- that's this task's own deliberate feature,
        not the "no override at all" case this test means to check.
        """
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style=LineChartStylePatch(
                axis_y=AxisYStylePatch(
                    position="right", labels=AxisLabelStylePatch(format=".2f")
                )
            ),
        )
        data = [{"month": "Jan", "revenue": 100}]
        spec = generate_vega_lite_spec(chart, data)
        assert "labelAlign" not in spec["encoding"]["y"].get("axis", {})

    def test_horizontal_bar_measure_axis_align_not_rejected(self, make_chart):
        """Horizontal bar's measure axis renders on VL x (orient stripped) — the
        left/right invasion bug is geometrically impossible there, so the same
        align value that would be rejected on a vertical measure axis must not
        raise here.
        """
        chart = make_chart(
            "bar",
            x="product",
            y="revenue",
            style=BarChartStylePatch(
                orientation="horizontal",
                axis_y=AxisYStylePatch(labels=AxisLabelStylePatch(align="right")),
            ),
        )
        data = [{"product": "Widget A", "revenue": 100}]
        spec = generate_vega_lite_spec(chart, data)
        assert spec["encoding"]["x"]["axis"]["labelAlign"] == "right"

    def test_authored_inward_on_quantitative_y_axis_resolves_to_the_real_edge(
        self, make_chart
    ):
        """Unlike the old inset boolean (band-only by construction), align's
        inward/outward resolve against WHATEVER edge the axis actually has —
        including a genuinely quantitative measure axis. A line chart's y
        defaults to position: right, so authoring inward there resolves to
        own-side "right" and computes a measured labelPadding, exactly like
        an explicit align: right (see test_own_side_align_on_stark_theme...
        above) — inward is just a position-relative spelling of the same
        already-supported quantitative-axis capability.
        """
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style=LineChartStylePatch(
                axis_y=AxisYStylePatch(labels=AxisLabelStylePatch(align="inward"))
            ),
        )
        data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
        spec = generate_vega_lite_spec(chart, data)
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis.get("labelAlign") == "right"
        assert y_axis.get("labelPadding", 0) > 0

    def test_normalize_stack_padding_uses_domain_bounds_not_raw_data(self):
        """When scale.continuous.domain is set, measure_axis_to_vl must measure from
        ONLY those domain bounds, not raw_data + bounds.

        Normalize-stack bakes domain=[0.0, 1.0] onto the axis.  Without the fix,
        raw data with million-scale y values is mixed with [0,1] and the padding is
        sized as if labels spanned millions (e.g. "200000000%") rather than the actual
        rendered range (e.g. "100%").  With the fix, the domain-constrained axis
        produces a labelPadding that is strictly smaller than an axis with no domain
        constraint over the same data.
        """
        from types import SimpleNamespace

        from dbt_charts.core.compile.models.primitives import ResolvedFormat
        from dbt_charts.core.render.chart.vl_field_maps import measure_axis_to_vl

        def _axis(domain: list[float] | None) -> object:
            """Build a minimal measure-axis stand-in with percent format."""
            return SimpleNamespace(
                position="right",
                labels=SimpleNamespace(
                    align="right",
                    font=SimpleNamespace(case="none", family="Inter", size=11.0),
                    max_width=None,
                    format=ResolvedFormat(spec=".0%"),
                    expr=None,
                ),
                title=SimpleNamespace(font=SimpleNamespace()),
                ticks=SimpleNamespace(length=None),
                grid=SimpleNamespace(),
                line=SimpleNamespace(),
                scale=(
                    SimpleNamespace(continuous=SimpleNamespace(domain=domain))
                    if domain is not None
                    else None
                ),
                tick_values=(),
                tick_label=None,
                ruler=None,
            )

        # Data whose raw values, formatted as percent, are orders of magnitude
        # larger than the normalize-stack rendered range [0, 1].
        data = [
            {"category": "A", "value": 1_000_000},
            {"category": "B", "value": 2_000_000},
        ]
        # Without a domain constraint, estimation uses the raw data extent.
        # With domain=[0,1], it must use ONLY those bounds — not raw_data + [0,1].
        d_domain = measure_axis_to_vl(_axis([0.0, 1.0]), data, ("value",))
        d_no_domain = measure_axis_to_vl(_axis(None), data, ("value",))

        # Both should produce a labelPadding (own-side align + format present).
        assert "labelPadding" in d_domain, (
            "domain-constrained axis must compute padding"
        )
        assert "labelPadding" in d_no_domain, "unconstrained axis must compute padding"

        # The key invariant: domain bounds REPLACE the raw data in estimation.
        # Without the fix both paddings are similar (raw millions pollute domain case).
        # With the fix, domain=[0,1] produces labels at most "100%" — far narrower
        # than the unconstrained path's estimate from millions.
        assert d_domain["labelPadding"] < d_no_domain["labelPadding"], (
            f"domain-constrained padding ({d_domain['labelPadding']}) is not smaller "
            f"than unconstrained ({d_no_domain['labelPadding']}), "
            "suggesting raw values are still included in the domain-bounded estimate"
        )

    def test_normalize_stack_resolve_bakes_unit_domain(self):
        """_resolve_bar with stack='normalize' bakes domain=[0.0, 1.0] onto the axis.

        This test catches deletion of the normalize bake block in bar.py (the
        _norm_cont / _norm_scale block).  Without the bake, measure_axis_to_vl
        receives a None domain and estimates labelPadding from million-scale raw
        values instead of the rendered [0, 1] range.
        """
        from dbt_charts.core.compile.config import (
            get_default_theme_name,
            get_theme_style,
            reset_config,
        )
        from dbt_charts.core.compile.models.chart.normalized.bar import BarChart
        from dbt_charts.core.compile.resolve.chart._chart_rows import partition
        from dbt_charts.core.compile.resolve.chart.bar import _resolve_bar
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        reset_config()
        ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
        chart = BarChart(
            id="normalize_bake",
            type="bar",
            x="category",
            y="value",
            stack="normalize",
            color="series",
        )
        data = [
            {"category": "A", "series": "X", "value": 1_000_000},
            {"category": "A", "series": "Y", "value": 2_000_000},
            {"category": "B", "series": "X", "value": 3_000_000},
            {"category": "B", "series": "Y", "value": 4_000_000},
        ]
        resolved = _resolve_bar(chart, partition(None, data), ctx, 800.0, {}, None, {})
        ay = resolved.style.axis_y
        # The bake sets domain=[0.0, 1.0] on the resolved axis scale.
        # Without the bake, ay.scale is None or ay.scale.continuous is None,
        # and measure_axis_to_vl estimates from raw million-scale values.
        assert ay.scale is not None, "normalize-stack must bake a scale onto the axis"
        assert ay.scale.continuous is not None, (
            "normalize-stack must bake a continuous scale domain"
        )
        assert ay.scale.continuous.domain == (0.0, 1.0), (
            f"normalize-stack domain must be [0.0, 1.0], got {ay.scale.continuous.domain!r}"
        )

    def test_normalize_stack_resolve_bakes_unit_domain_area(self):
        """_resolve_area with stack='normalize' bakes domain=[0.0, 1.0] onto the axis.

        Mirrors the bar version above. Without the bake in area.py, measure_axis_to_vl
        receives a None domain and estimates labelPadding from million-scale raw values
        instead of the rendered [0, 1] range.
        """
        from dbt_charts.core.compile.config import (
            get_default_theme_name,
            get_theme_style,
            reset_config,
        )
        from dbt_charts.core.compile.models.chart.normalized.area import AreaChart
        from dbt_charts.core.compile.resolve.chart._chart_rows import partition
        from dbt_charts.core.compile.resolve.chart.area import _resolve_area
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        reset_config()
        ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
        chart = AreaChart(
            id="normalize_bake_area",
            type="area",
            x="category",
            y="value",
            stack="normalize",
            color="series",
        )
        data = [
            {"category": "A", "series": "X", "value": 1_000_000},
            {"category": "A", "series": "Y", "value": 2_000_000},
            {"category": "B", "series": "X", "value": 3_000_000},
            {"category": "B", "series": "Y", "value": 4_000_000},
        ]
        resolved = _resolve_area(chart, partition(None, data), ctx, 800.0, {}, None, {})
        ay = resolved.style.axis_y
        assert ay.scale is not None, "normalize-stack must bake a scale onto the axis"
        assert ay.scale.continuous is not None, (
            "normalize-stack must bake a continuous scale domain"
        )
        assert ay.scale.continuous.domain == (0.0, 1.0), (
            f"normalize-stack area domain must be [0.0, 1.0], got {ay.scale.continuous.domain!r}"
        )


class TestHorizontalBarCategoricalLabelAlignInvasion:
    """Symmetric case to ``TestAxisYLabelAlignInvasion``, for horizontal bar's
    categorical axis (``chart.x``, rendered on VL's y channel at the
    resolved ``axis_y.position`` — "left" by default, matching the deleted
    ``categorical_orient`` field's static default). Category tick content is
    always dbt charts' own (the literal per-row field values, in query row
    order) — no format uncertainty at all, unlike the quantitative case.
    """

    def test_own_side_align_computes_measured_padding(self, make_chart):
        chart = make_chart(
            "bar",
            x="product",
            y="revenue",
            style=BarChartStylePatch(
                orientation="horizontal",
                axis_x=AxisXStylePatch(labels=DimensionLabelStylePatch(align="left")),
            ),
        )
        data = [{"product": "Widget A", "revenue": 100}]
        spec = generate_vega_lite_spec(chart, data)
        y_axis = spec["encoding"]["y"]["axis"]
        assert y_axis["labelAlign"] == "left"
        assert "labelPadding" in y_axis

    def test_measured_padding_grows_with_wider_category_labels(self, make_chart):
        def _padding(product: str) -> float:
            chart = make_chart(
                "bar",
                x="product",
                y="revenue",
                style=BarChartStylePatch(
                    orientation="horizontal",
                    axis_x=AxisXStylePatch(
                        labels=DimensionLabelStylePatch(align="left")
                    ),
                ),
            )
            data = [{"product": product, "revenue": 100}]
            spec = generate_vega_lite_spec(chart, data)
            return float(spec["encoding"]["y"]["axis"]["labelPadding"])

        assert _padding("A") < _padding("A Much Longer Product Name Indeed")

    def test_measured_padding_reflects_a_wide_label_past_row_100(self, make_chart):
        """Regression: the gutter must be sized from every distinct category,
        not just the first 100 rows — under-sampling silently under-reserves
        the gutter for a wide label that happens to sit later in the data,
        reintroducing the exact silent-clip failure mode this task exists to
        eliminate.
        """
        chart = make_chart(
            "bar",
            x="product",
            y="revenue",
            style=BarChartStylePatch(
                orientation="horizontal",
                axis_x=AxisXStylePatch(labels=DimensionLabelStylePatch(align="left")),
            ),
        )
        narrow_data = [{"product": f"P{i}", "revenue": i} for i in range(120)]
        wide_data = narrow_data + [
            {"product": "A Much Longer Product Name Indeed", "revenue": 999}
        ]
        narrow_padding = generate_vega_lite_spec(chart, narrow_data)["encoding"]["y"][
            "axis"
        ]["labelPadding"]
        wide_padding = generate_vega_lite_spec(chart, wide_data)["encoding"]["y"][
            "axis"
        ]["labelPadding"]
        assert wide_padding > narrow_padding

    def test_measured_padding_counts_a_falsy_but_real_category_value(self, make_chart):
        """A category value of ``0`` is a real, renderable label — a
        truthiness filter (``if row.get(field)``) drops it, and with only
        that one row the measured set would go empty, collapsing the
        computed padding to 0 even though the axis renders a real "0" label.
        """
        chart = make_chart(
            "bar",
            x="product",
            y="revenue",
            style=BarChartStylePatch(
                orientation="horizontal",
                axis_x=AxisXStylePatch(labels=DimensionLabelStylePatch(align="left")),
            ),
        )
        spec = generate_vega_lite_spec(chart, [{"product": 0, "revenue": 1}])
        assert spec["encoding"]["y"]["axis"]["labelPadding"] > 0

    def test_own_side_align_with_label_expr_falls_back(self, make_chart):
        """Same gap as the quantitative axis's ``measure_axis_to_vl`` guard,
        on the third call site: an authored ``label.expr`` renders in
        preference to the raw category value, so measuring from the row
        values while the axis renders the (possibly wider) expr output would
        silently under-reserve the gutter — falls back to the away-side
        default instead.
        """
        chart = make_chart(
            "bar",
            x="product",
            y="revenue",
            style=BarChartStylePatch(
                orientation="horizontal",
                axis_x=AxisXStylePatch(
                    labels=DimensionLabelStylePatch(
                        align="left", expr="datum.label + ' units'"
                    )
                ),
            ),
        )
        data = [{"product": "A", "revenue": 100}]
        spec = generate_vega_lite_spec(chart, data)
        assert "labelAlign" not in spec["encoding"]["y"]["axis"]

    def test_own_side_align_with_no_measurable_categories_falls_back(self, make_chart):
        """If every row's category value is None, the measured label set is
        empty — ``measured_label_padding`` would return 0.0 for it, which is
        indistinguishable from "no gutter needed" and would silently let the
        invading align through unreserved. Falls back to the away-side
        default instead of emitting a zero labelPadding.
        """
        chart = make_chart(
            "bar",
            x="product",
            y="revenue",
            style=BarChartStylePatch(
                orientation="horizontal",
                axis_x=AxisXStylePatch(labels=DimensionLabelStylePatch(align="left")),
            ),
        )
        data = [{"product": None, "revenue": 100}]
        spec = generate_vega_lite_spec(chart, data)
        assert "labelAlign" not in spec["encoding"]["y"]["axis"]

    def test_opposite_align_is_safe(self, make_chart):
        """align matching VL's away-from-plot default is unaffected."""
        chart = make_chart(
            "bar",
            x="product",
            y="revenue",
            style=BarChartStylePatch(
                orientation="horizontal",
                axis_x=AxisXStylePatch(labels=DimensionLabelStylePatch(align="right")),
            ),
        )
        data = [{"product": "Widget A", "revenue": 100}]
        spec = generate_vega_lite_spec(chart, data)
        assert spec["encoding"]["y"]["axis"]["labelAlign"] == "right"
        assert "orient" not in spec["encoding"]["x"]["axis"]


class TestChartFormatApplied:
    """Test that style.number_format is applied to axis encodings."""

    def test_bar_chart_format_applied_to_y_axis(self, make_chart):
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style=BarChartStylePatch(orientation="vertical", number_format="$,.0f"),
        )
        data = [{"month": "Jan", "revenue": 1000}, {"month": "Feb", "revenue": 2000}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        assert spec["encoding"]["y"]["format"] == "$,.0f"

    def test_bar_chart_format_preset_resolved(self, make_chart):
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style=BarChartStylePatch(
                orientation="vertical", number_format="currency_full"
            ),
        )
        data = [{"month": "Jan", "revenue": 1000}, {"month": "Feb", "revenue": 2000}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        assert spec["encoding"]["y"]["format"] == "$,.2f"

    def test_line_chart_format_applied(self, make_chart):
        chart = make_chart(
            "line",
            x="date",
            y="revenue",
            style=LineChartStylePatch(number_format="$,.0f"),
        )
        data = [
            {"date": "2024-01-01", "revenue": 1000},
            {"date": "2024-01-02", "revenue": 2000},
        ]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        assert spec["encoding"]["y"]["format"] == "$,.0f"

    def test_format_applied_to_tooltips(self, make_chart):
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style=BarChartStylePatch(orientation="vertical", number_format="$,.0f"),
        )
        data = [{"month": "Jan", "revenue": 1000}, {"month": "Feb", "revenue": 2000}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        assert spec["encoding"]["y"]["format"] == "$,.0f"

    def test_custom_alias_resolves_through_board_formats(self, make_chart):
        """End-to-end: board-level style.formats alias flows to VL axis.format AND Python paths.

        Regression guard for DFT_CORE-ALLOW_THEMES_AND_FACES_TO_DEFINE_CUSTOM_FORMAT_PRESETS.
        Board overrides theme; "revenue" alias resolves via cascade, not code fallback.
        Checks both the VL render path (axis.format in spec) and the Python format_value path.
        """
        from dbt_charts.core.compile.models.style.authored import StylePatch
        from dbt_charts.core.render.format_utils import format_kpi_parts, format_value

        base_style = get_theme_style()
        resolved_rs, resolved_ctx = resolve_style_and_context(
            base_style, StylePatch(formats={"revenue": "$~s"})
        )
        formats = resolved_ctx.formats
        assert formats is not None and "revenue" in formats

        # VL path: "revenue" alias resolves to the D3 spec in encoding.y.format
        chart = make_chart(
            "bar",
            x="month",
            y="rev",
            style=BarChartStylePatch(orientation="vertical", number_format="revenue"),
        )
        data = [{"month": "Jan", "rev": 1_500_000}]
        resolved_chart = resolve(chart, data, chart_style_context=resolved_ctx)
        spec = render_resolved_chart(resolved_chart, data, resolved_rs).payload
        y_format = spec["encoding"]["y"].get("format", "")
        assert y_format == "$~s", f"VL path: expected '$~s', got {y_format!r}"

        # Python path: format_value and format_kpi_parts resolve the same alias
        resolved_d3 = formats["revenue"]
        assert format_value(1_500_000, "revenue", formats) == format_value(
            1_500_000, resolved_d3, formats
        ), "Python path: alias must resolve to same output as raw spec"
        _prefix, _main, _suffix = format_kpi_parts(1_500_000, "revenue", formats)
        assert _prefix + _main + _suffix != "", (
            "format_kpi_parts must return non-empty output"
        )

    def test_null_format_suppresses_chart_authored_format(self, make_chart):
        """End-to-end: explicit format=None on chart does not resolve to a format string.

        The compile/resolve/chart/_axes.py gate is:
        resolve_format(format, formats) if format else "".
        format=None → user_format="" → axis.format NOT set from chart.format.
        This is the mechanism that lets format: null override an inherited
        theme-level format: compact per chart.
        """
        from dbt_charts.core.compile.format import resolve_format

        # The resolve_format contract: None input always returns "".
        assert resolve_format(None, {"currency": "$,.2f", "number": "~s"}) == ""
        assert resolve_format(None) == ""

        # VL path: use a known D3 spec alias to confirm the mechanism, then verify None clears it.
        # Avoid pinning default theme values; inject an explicit formats dict via resolved_style.
        from dbt_charts.core.compile.models.style.authored import StylePatch

        base_style = get_theme_style()
        resolved_rs, resolved_ctx = resolve_style_and_context(
            base_style, StylePatch(formats={"myalias": "$,.0f"})
        )
        chart_with = make_chart(
            "bar",
            x="month",
            y="cnt",
            style=BarChartStylePatch(orientation="vertical", number_format="myalias"),
        )
        chart_none = make_chart(
            "bar",
            x="month",
            y="cnt",
            style=BarChartStylePatch(orientation="vertical"),
        )
        data = [{"month": "Jan", "cnt": 100}, {"month": "Feb", "cnt": 200}]

        resolved_with = resolve(chart_with, data, chart_style_context=resolved_ctx)
        spec_with = render_resolved_chart(resolved_with, data, resolved_rs).payload

        resolved_none = resolve(chart_none, data, chart_style_context=resolved_ctx)
        spec_none = render_resolved_chart(resolved_none, data, resolved_rs).payload

        # format="myalias" must resolve to "$,.0f" in the VL spec
        assert spec_with["encoding"]["y"].get("format") == "$,.0f"
        # format=None must NOT inject the alias (the chart didn't request it)
        assert spec_none["encoding"]["y"].get("format") != "$,.0f"


class TestTimeFormatOnOrdinalAxis:
    """Regression: d3-time-format on ordinal x-axis must route through labelExpr.

    Root cause: style.axis_x.format: "%b %Y" is a d3-time-format string, but
    when x-data contains string values like "2024-01", Vega infers the axis as
    ordinal. ``formatType: "time"`` on a string-domain ordinal scale silently
    drops every label because d3-time-format expects a Date, not a string.
    Fix: emit ``axis.labelExpr = utcFormat(toDate(datum.value), <fmt>)`` and
    drop the raw ``format`` directive — labels actually render that way.
    Non-time formats stay on the ``format`` channel (d3-format handles them).
    """

    def test_time_format_on_ordinal_x_routes_through_label_expr(self, make_chart):
        from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style=BarChartStylePatch(
                axis_x=AxisXStylePatch(labels=DimensionLabelStylePatch(format="%b %Y"))
            ),
        )
        data = [
            {"month": "2024-01", "revenue": 10},
            {"month": "2024-02", "revenue": 15},
        ]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        x_axis = spec["encoding"]["x"].get("axis", {})
        assert x_axis.get("labelExpr") == "utcFormat(toDate(datum.value), '%b %Y')", (
            f"Expected utcFormat labelExpr on ordinal date axis: {x_axis}"
        )
        assert "format" not in x_axis, (
            f"Time format must not stay as format on ordinal: {x_axis}"
        )
        assert x_axis.get("formatType") != "time"

    def test_non_time_format_on_ordinal_x_no_format_type(self, make_chart):
        """Non-time formats (d3-format strings) must NOT get formatType='time'.

        A numeric column pinned ordinal by ``axis_x.type`` is the shape that
        keeps both halves true: the scale is a band, and the ticks are numbers
        d3 can actually format. Over category strings the same authoring is
        its own error now — every tick would render NaN.
        """
        chart = make_chart(
            "bar",
            x="bucket",
            y="revenue",
            style=BarChartStylePatch(
                orientation="vertical",
                axis_x=AxisXStylePatch(
                    type="ordinal", labels=DimensionLabelStylePatch(format=".2f")
                ),
            ),
        )
        data = [{"bucket": 1, "revenue": 10}, {"bucket": 2, "revenue": 15}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        assert spec["encoding"]["x"]["type"] == "ordinal"
        x_axis = spec["encoding"]["x"].get("axis", {})
        assert x_axis.get("format") == ".2f"
        assert "formatType" not in x_axis

    def test_time_format_on_temporal_x_no_format_type_needed(self, make_chart):
        """Temporal escape-hatch axes use d3-time-format natively — no formatType needed.

        D-002: first-of-month dates default to ordinal, but ``axis_x.type: temporal``
        opts back into the temporal path where Vega already applies d3-time-format.
        """
        from dbt_charts.core.compile.models.style.authored import LineChartStylePatch

        chart = make_chart(
            "line",
            x="date",
            y="revenue",
            style=LineChartStylePatch(
                axis_x=AxisXStylePatch(
                    labels=DimensionLabelStylePatch(format="%b %Y"), type="temporal"
                )
            ),
        )
        data = [
            {"date": "2024-01-01", "revenue": 10},
            {"date": "2024-02-01", "revenue": 15},
        ]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        x_axis = spec["encoding"]["x"].get("axis", {})
        assert x_axis.get("format") == "%b %Y"
        assert "formatType" not in x_axis, (
            "Temporal axes should not have formatType injected"
        )

    def test_literal_percent_escape_does_not_inject_format_type(self, make_chart):
        """%%Y is the literal characters %Y (escape), NOT a time directive."""
        from dbt_charts.core.compile.models.style.authored import LineChartStylePatch

        chart = make_chart(
            "line",
            x="bucket",
            y="revenue",
            style=LineChartStylePatch(
                axis_x=AxisXStylePatch(
                    type="ordinal", labels=DimensionLabelStylePatch(format="%%Y")
                )
            ),
        )
        data = [{"bucket": 1, "revenue": 10}, {"bucket": 2, "revenue": 15}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        # An ordinal band keeps is_time_format's verdict load-bearing: on a
        # temporal x the injection site is skipped outright, so the guard this
        # test exists for would never execute.
        assert spec["encoding"]["x"]["type"] == "ordinal"
        x_axis = spec["encoding"]["x"].get("axis", {})
        assert x_axis.get("format") == "%%Y"
        assert "formatType" not in x_axis, (
            "%% is a literal-percent escape; must not trigger time-format injection"
        )

    def test_seconds_directive_alone_routes_through_label_expr(self, make_chart):
        """%S (seconds) alone is a d3-time-format directive — must route through
        the UTC labelExpr (not formatType='time' which is runtime-TZ-dependent).
        """
        from dbt_charts.core.compile.models.style.authored import LineChartStylePatch

        chart = make_chart(
            "line",
            x="time_str",
            y="value",
            style=LineChartStylePatch(
                axis_x=AxisXStylePatch(labels=DimensionLabelStylePatch(format="%S"))
            ),
        )
        data = [{"time_str": "00", "value": 1}, {"time_str": "30", "value": 2}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        x_axis = spec["encoding"]["x"].get("axis", {})
        assert x_axis.get("labelExpr") == "utcFormat(toDate(datum.value), '%S')", (
            f"%S must route through UTC labelExpr; got {x_axis}"
        )
        assert "format" not in x_axis, f"format must be popped; got {x_axis}"
        assert x_axis.get("formatType") != "time"

    def test_padding_modifier_directives_route_through_label_expr(self, make_chart):
        """d3-time-format padding modifiers (%-d, %_m, %0H) are still time
        directives and must route through the labelExpr path on ordinal axes.
        The regex must accept the optional [-_0] modifier between % and the
        directive letter.
        """
        from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style=BarChartStylePatch(
                axis_x=AxisXStylePatch(
                    labels=DimensionLabelStylePatch(format="%-m/%-d/%-Y")
                )
            ),
        )
        data = [
            {"month": "2024-01", "revenue": 10},
            {"month": "2024-02", "revenue": 15},
        ]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        x_axis = spec["encoding"]["x"].get("axis", {})
        assert (
            x_axis.get("labelExpr") == "utcFormat(toDate(datum.value), '%-m/%-d/%-Y')"
        ), f"Padding-modifier time format must route through labelExpr: {x_axis}"
        assert "format" not in x_axis

    def test_time_format_on_nominal_y_axis_routes_through_label_expr(self, make_chart):
        """Same guard applies to y-axis: a d3-time-format on a non-temporal
        y-axis (scatter chart with nominal y-type) must route through the UTC
        labelExpr instead of formatType='time' (runtime-TZ-dependent leak).
        """
        from dbt_charts.core.compile.models.style.authored import ScatterChartStylePatch

        chart = make_chart(
            "scatter",
            x="value",
            y="month_str",
            style=ScatterChartStylePatch(
                axis_y=AxisYStylePatch(labels=AxisLabelStylePatch(format="%b %Y"))
            ),
        )
        data = [{"value": 1, "month_str": "Jan"}, {"value": 2, "month_str": "Feb"}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        y_axis = spec["encoding"]["y"].get("axis", {})
        assert y_axis.get("labelExpr") == "utcFormat(toDate(datum.value), '%b %Y')", (
            f"y-axis time-format must route through UTC labelExpr; got {y_axis}"
        )
        assert "format" not in y_axis, f"format must be removed; got {y_axis}"


class TestAxisTitleNull:
    """axis.title must be null (not titleFontSize:0) when a title is suppressed.

    Finding: font.size:0 hides the text visually but the title still occupies
    layout space and fires a11y events.  The correct VL mechanism is
    ``encoding.x.axis.title: null``.
    """

    @pytest.mark.parametrize("chart_type", ["bar"])
    def test_with_authored_labels_no_axis_title_null(self, make_chart, chart_type):
        """Charts with authored labels must NOT emit axis.title:null (any family).

        bar hits the merge path via a family legend patch; line/area do not and
        previously hit the fast-path early return, silently dropping labels.
        Regression coverage for the fast-path bug.
        """
        chart = make_chart(
            chart_type,
            x="month",
            y="revenue",
            x_label="Month",
            y_label="Revenue ($)",
        )
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        x_axis = spec["encoding"]["x"].get("axis", {})
        y_axis = spec["encoding"]["y"].get("axis", {})
        assert x_axis.get("title") is not None or "title" not in x_axis, (
            f"{chart_type}: x-axis with authored label must NOT carry axis.title:null; "
            f"got axis={x_axis}"
        )
        assert y_axis.get("title") is not None or "title" not in y_axis, (
            f"{chart_type}: y-axis with authored label must NOT carry axis.title:null; "
            f"got axis={y_axis}"
        )

    @pytest.mark.parametrize("chart_type", ["line", "area"])
    def test_with_authored_labels_no_axis_title_null_line_area(
        self, make_chart, chart_type
    ):
        """Line/area: authored labels must NOT emit axis.title:null (deferred port)."""
        chart = make_chart(
            chart_type,
            x="month",
            y="revenue",
            x_label="Month",
            y_label="Revenue ($)",
        )
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        x_axis = spec["encoding"]["x"].get("axis", {})
        y_axis = spec["encoding"]["y"].get("axis", {})
        assert x_axis.get("title") is not None or "title" not in x_axis, (
            f"{chart_type}: x-axis with authored label must NOT carry axis.title:null; "
            f"got axis={x_axis}"
        )
        assert y_axis.get("title") is not None or "title" not in y_axis, (
            f"{chart_type}: y-axis with authored label must NOT carry axis.title:null; "
            f"got axis={y_axis}"
        )

    def test_no_title_font_size_zero_in_spec(self, make_chart):
        """titleFontSize:0 must never appear — it hides text but leaks layout space."""
        import json

        chart = make_chart("bar", x="month", y="revenue")
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        spec_json = json.dumps(spec)
        assert '"titleFontSize": 0' not in spec_json, (
            "titleFontSize:0 found in VL spec — use axis.title:null instead"
        )

    def test_title_angle_and_align_reach_the_emitted_spec(self, make_chart):
        """axis.title.angle/align must reach the VL spec as titleAngle/titleAlign,
        mirroring the existing labels.angle/align -> labelAngle/labelAlign mapping.

        y_label is required so the title is actually rendered (an unlabeled
        axis resolves title.visible:False, which nulls the whole title block —
        see test_suppressed_title_carries_no_stray_angle_or_align below)."""
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            y_label="Revenue",
            style=BarChartStylePatch(
                orientation="vertical",
                axis_y=AxisYStylePatch(
                    title=AxisTitleStylePatch(angle=77, align="left")
                ),
            ),
        )
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        y_axis = spec["encoding"]["y"].get("axis", {})
        assert y_axis.get("titleAngle") == 77
        assert y_axis.get("titleAlign") == "left"

    def test_suppressed_title_carries_no_stray_angle_or_align(self, make_chart):
        """title.visible:false suppresses titleAngle/titleAlign along with the
        other title keys — no stray properties on a null title."""
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style=BarChartStylePatch(
                orientation="vertical",
                axis_y=AxisYStylePatch(
                    title=AxisTitleStylePatch(angle=77, align="left", visible=False)
                ),
            ),
        )
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        y_axis = spec["encoding"]["y"].get("axis", {})
        assert y_axis.get("title") is None
        assert "titleAngle" not in y_axis
        assert "titleAlign" not in y_axis

    def test_y_label_with_title_visible_false_suppresses_axis_title(self, make_chart):
        """An explicitly authored axis_y.title.visible:false must win over the
        y_label forcing patch — axis title suppressed, encoding title (legend/
        tooltip name) still carries the authored label."""
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            y_label="% from Data Lakes",
            style=LineChartStylePatch(
                axis_y=AxisYStylePatch(title=AxisTitleStylePatch(visible=False))
            ),
        )
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        y_axis = spec["encoding"]["y"].get("axis", {})
        assert "title" in y_axis and y_axis["title"] is None
        assert spec["encoding"]["y"]["title"] == "% from Data Lakes"

    def test_y_label_alone_still_forces_axis_title_over_theme_default(self, make_chart):
        """No regression: an authored y_label with no title.visible override
        still forces the axis title on over the theme's blanket suppression."""
        chart = make_chart("line", x="month", y="revenue", y_label="Revenue ($)")
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        y_axis = spec["encoding"]["y"].get("axis", {})
        assert "title" not in y_axis
        assert spec["encoding"]["y"]["title"] == "Revenue ($)"

    def test_title_visible_true_with_y_label_shows_axis_title(self, make_chart):
        """An explicitly authored axis_y.title.visible:true plus y_label keeps
        the axis title on (same outcome as the unauthored forcing default)."""
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            y_label="Revenue ($)",
            style=LineChartStylePatch(
                axis_y=AxisYStylePatch(title=AxisTitleStylePatch(visible=True))
            ),
        )
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        y_axis = spec["encoding"]["y"].get("axis", {})
        assert "title" not in y_axis
        assert spec["encoding"]["y"]["title"] == "Revenue ($)"

    def test_x_label_with_title_visible_false_suppresses_axis_title(self, make_chart):
        """Same precedence fix, x-axis side."""
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            x_label="Month (fiscal)",
            style=LineChartStylePatch(
                axis_x=AxisXStylePatch(title=AxisTitleStylePatch(visible=False))
            ),
        )
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        x_axis = spec["encoding"]["x"].get("axis", {})
        assert "title" in x_axis and x_axis["title"] is None
        assert spec["encoding"]["x"]["title"] == "Month (fiscal)"

    def test_x_label_alone_still_forces_axis_title_over_theme_default(self, make_chart):
        """No regression on the x-axis side of the forcing default."""
        chart = make_chart("line", x="month", y="revenue", x_label="Month")
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        x_axis = spec["encoding"]["x"].get("axis", {})
        assert "title" not in x_axis
        assert spec["encoding"]["x"]["title"] == "Month"

    def test_bar_y_label_with_title_visible_false_suppresses_axis_title(
        self, make_chart
    ):
        """Orientation-swapped path: a vertical bar's y_label/axis_y still
        route through encoding.y, same as line's measure axis."""
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            y_label="Revenue ($)",
            style=BarChartStylePatch(
                orientation="vertical",
                axis_y=AxisYStylePatch(title=AxisTitleStylePatch(visible=False)),
            ),
        )
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        y_axis = spec["encoding"]["y"].get("axis", {})
        assert "title" in y_axis and y_axis["title"] is None
        assert spec["encoding"]["y"]["title"] == "Revenue ($)"

    def test_y_label_with_global_axis_title_visible_false_suppresses_axis_title(
        self, make_chart
    ):
        """A chart-local style.axis.title.visible:false — the global axis
        patch shared by both axes, not axis_y specifically — also beats the
        y_label forcing default. The default is injected at Layer 5,
        below every chart-local layer, so this needs no special handling."""
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            y_label="% from Data Lakes",
            style=LineChartStylePatch(
                axis=BaseAxisStylePatch(title=AxisTitleStylePatch(visible=False))
            ),
        )
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        y_axis = spec["encoding"]["y"].get("axis", {})
        assert "title" in y_axis and y_axis["title"] is None
        assert spec["encoding"]["y"]["title"] == "% from Data Lakes"

    def test_y_label_with_axis_y_override_still_wins_over_global_axis_patch(
        self, make_chart
    ):
        """The more specific axis_y.title.visible:true still wins over a
        global axis.title.visible:false — Layer 13 beats Layer 11 as usual.
        Ordinary cascade precedence between two authored layers; the
        label-forcing default sits below both and changes nothing here."""
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            y_label="% from Data Lakes",
            style=LineChartStylePatch(
                axis=BaseAxisStylePatch(title=AxisTitleStylePatch(visible=False)),
                axis_y=AxisYStylePatch(title=AxisTitleStylePatch(visible=True)),
            ),
        )
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        y_axis = spec["encoding"]["y"].get("axis", {})
        assert "title" not in y_axis
        assert spec["encoding"]["y"]["title"] == "% from Data Lakes"

    def test_axis_quantitative_title_visible_false_with_y_label_suppresses_axis_title(
        self, make_chart
    ):
        """axis_quantitative.title.visible:false beats the y_label forcing
        default too. The type-conditional slot is cascade Layer 12; the
        forcing default sits at Layer 5, below it and below every
        other chart-local layer, so every authored slot wins alike."""
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            y_label="% from Data Lakes",
            style=LineChartStylePatch(
                axis_quantitative=QuantitativeAxisStylePatch(
                    title=AxisTitleStylePatch(visible=False)
                )
            ),
        )
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        y_axis = spec["encoding"]["y"].get("axis", {})
        assert "title" in y_axis and y_axis["title"] is None
        assert spec["encoding"]["y"]["title"] == "% from Data Lakes"

    def test_axis_band_title_visible_false_with_x_label_suppresses_axis_title(
        self, make_chart
    ):
        """Same slot-coverage fix on the x-axis / axis_band side."""
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            x_label="Month (fiscal)",
            style=LineChartStylePatch(
                axis_band=BandAxisStylePatch(title=AxisTitleStylePatch(visible=False))
            ),
        )
        data = [{"month": "Jan", "revenue": 100}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        x_axis = spec["encoding"]["x"].get("axis", {})
        assert "title" in x_axis and x_axis["title"] is None
        assert spec["encoding"]["x"]["title"] == "Month (fiscal)"


class TestDefaultAxisTitleCasing:
    """Default axis titles derive from the bound column name and preserve its
    casing — they read like a column name (``order month``), not a title-cased
    headline (``Order Month``). Authored ``x_label``/``y_label`` (chart title,
    legend title) are untouched by this: those still resolve through the
    axis-title font's ``case`` (title-case by default).
    """

    def test_default_axis_titles_lowercase_snake_case_fields(self, make_chart):
        """order_month / total_revenue → lowercase 'order month' / 'total revenue',
        matching the bound column names rather than a title-cased headline."""
        chart = make_chart("line", x="order_month", y="total_revenue")
        data = [{"order_month": "2026-01", "total_revenue": 123}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        assert spec["encoding"]["x"]["title"] == "order month"
        assert spec["encoding"]["y"]["title"] == "total revenue"

    def test_default_axis_titles_preserve_casing_after_orientation_routing(
        self, make_chart
    ):
        """department / user_count on a bar auto-routes horizontal (nominal x),
        so user_count lands on VL x and department on VL y — casing must be
        preserved on whichever channel each field actually renders on."""
        chart = make_chart("bar", x="department", y="user_count")
        data = [
            {"department": "Sales", "user_count": 10},
            {"department": "Eng", "user_count": 20},
        ]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        assert spec["encoding"]["x"]["title"] == "user count"
        assert spec["encoding"]["y"]["title"] == "department"

    def test_authored_labels_pass_through_unchanged(self, make_chart):
        """An authored x_label/y_label is never touched by the default-axis-title
        casing rule — it renders exactly as authored, including title case."""
        chart = make_chart(
            "line",
            x="order_month",
            y="total_revenue",
            x_label="Order Month",
            y_label="Total Revenue",
        )
        data = [{"order_month": "2026-01", "total_revenue": 123}]
        _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, data)
        assert spec["encoding"]["x"]["title"] == "Order Month"
        assert spec["encoding"]["y"]["title"] == "Total Revenue"
