"""Cascade tests for build_chart_style_context — chart-local axis/legend/scale merge.

The directive: after build_chart_style_context, resolved_style.axis_x carries the
chart-local override. Renderer reads resolved_style.axis_x.<field> with no
patch lookup, no getattr chain, no Any-typed escape hatch.

These tests exercise the merge end-to-end: chart-local patch fields flow into
the corresponding resolved fields, AND the theme cascade is preserved when no
override is authored.
"""

from __future__ import annotations

import dataclasses

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
    list_built_in_themes,
    reset_config,
)
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    LineChart,
)
from dbt_charts.core.compile.models.chart.resolved import ResolvedBarStyle
from dbt_charts.core.compile.models.style.authored import (
    AreaChartStylePatch,
    AxisLabelStylePatch,
    AxisTicksStylePatch,
    AxisXStylePatch,
    AxisYStylePatch,
    BandAxisStylePatch,
    BarChartStylePatch,
    BaseAxisGridStylePatch,
    DimensionLabelStylePatch,
    LegendStylePatch,
    LineChartStylePatch,
    QuantitativeAxisStylePatch,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedAxisStyle
from dbt_charts.core.compile.models.style.theme import AxisXStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.axis_cascade import (
    _merge_axis_cascade,
    build_resolved_axis,
    resolved_axis_style,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)

from ..conftest import baked_format

# `_base` is the hidden completeness floor beneath every built-in theme, not
# itself an author-facing pick; `diagnostics-*` themes are render-diagnostic
# fixtures. Everything else -- including `stark`, the structural root every
# user-facing theme extends -- gets the unresolved-token property check.
_RESOLVABLE_BUILT_IN_THEMES = [
    name
    for name in list_built_in_themes()
    if name != "_base" and not name.startswith("diagnostics-")
]


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_config()
    yield
    reset_config()


def _board() -> ChartStyleContext:
    return resolve_chart_style_context(get_theme_style())


class TestThemeOnlyFastPath:
    """When the chart has no local style, build_chart_style_context returns the board charts unchanged."""

    def test_no_chart_style_returns_board_charts_identity(self) -> None:
        board = _board()
        # Use "line": editorial theme has no per-family legend patch for line,
        # so the fast path (return board unchanged) fires.
        result = build_chart_style_context(board, LineChart(id="t", type="line"))
        assert result is board

    def test_empty_chart_style_returns_board_charts_identity(self) -> None:
        board = _board()
        from dbt_charts.core.compile.models.style.authored import LineChartStylePatch

        result = build_chart_style_context(
            board, LineChart(id="t", type="line", style=LineChartStylePatch())
        )
        assert result is board


class TestChartLocalAxisYMerge:
    """Chart-local style.axis_y flows through resolved_axis_style at emit time."""

    def test_label_padding_propagates(self) -> None:
        patch = BarChartStylePatch(
            axis_y=AxisYStylePatch(labels=AxisLabelStylePatch(padding=42))
        )
        result = build_chart_style_context(
            _board(), BarChart(id="t", type="bar", style=patch)
        )
        merged = resolved_axis_style(
            result, "axis_y", "quantitative", chart_type="", labeled=False
        )
        assert merged.labels.padding == 42

    def test_ticks_length_propagates(self) -> None:
        patch = BarChartStylePatch(
            axis_y=AxisYStylePatch(ticks=AxisTicksStylePatch(length=8.5))
        )
        result = build_chart_style_context(
            _board(), BarChart(id="t", type="bar", style=patch)
        )
        merged = resolved_axis_style(
            result, "axis_y", "quantitative", chart_type="", labeled=False
        )
        assert merged.ticks.length == 8.5

    def test_grid_visible_propagates(self) -> None:
        patch = BarChartStylePatch(
            axis_y=AxisYStylePatch(grid=BaseAxisGridStylePatch(visible=False))
        )
        result = build_chart_style_context(
            _board(), BarChart(id="t", type="bar", style=patch)
        )
        merged = resolved_axis_style(
            result, "axis_y", "quantitative", chart_type="", labeled=False
        )
        assert merged.grid.visible is False

    def test_format_propagates(self) -> None:
        patch = BarChartStylePatch(
            axis_y=AxisYStylePatch(labels=AxisLabelStylePatch(format="$,.0f"))
        )
        result = build_chart_style_context(
            _board(), BarChart(id="t", type="bar", style=patch)
        )
        merged = resolved_axis_style(
            result, "axis_y", "quantitative", chart_type="", labeled=False
        )
        assert merged.labels.format.spec == "$,.0f"

    def test_axis_y_does_not_leak_to_axis_x(self) -> None:
        patch = BarChartStylePatch(
            axis_y=AxisYStylePatch(labels=AxisLabelStylePatch(padding=42))
        )
        board = _board()
        result = build_chart_style_context(
            board, BarChart(id="t", type="bar", style=patch)
        )
        merged_x = resolved_axis_style(
            result, "axis_x", "ordinal", chart_type="", labeled=False
        )
        baseline_x = resolved_axis_style(
            board, "axis_x", "ordinal", chart_type="", labeled=False
        )
        assert merged_x.labels.padding == baseline_x.labels.padding


class TestChartLocalAxisXMerge:
    def test_time_unit_propagates(self) -> None:
        patch = BarChartStylePatch(axis_x=AxisXStylePatch(time_unit="yearmonth"))
        result = build_chart_style_context(
            _board(), BarChart(id="t", type="bar", style=patch)
        )
        merged = resolved_axis_style(
            result, "axis_x", "temporal", chart_type="", labeled=False
        )
        assert merged.time_unit == "yearmonth"

    def test_label_angle_propagates(self) -> None:
        patch = BarChartStylePatch(
            axis_x=AxisXStylePatch(labels=DimensionLabelStylePatch(angle=-45))
        )
        result = build_chart_style_context(
            _board(), BarChart(id="t", type="bar", style=patch)
        )
        merged = resolved_axis_style(
            result, "axis_x", "ordinal", chart_type="", labeled=False
        )
        assert merged.labels.angle == -45


class TestChartLocalAxisGlobalMerge:
    """Chart-local style applies to both axis_x and axis_y when set on the family."""

    def test_global_grid_not_visible_applies_to_both(self) -> None:
        patch = BarChartStylePatch(
            axis_x=AxisXStylePatch(grid=BaseAxisGridStylePatch(visible=False)),
            axis_y=AxisYStylePatch(grid=BaseAxisGridStylePatch(visible=False)),
        )
        result = build_chart_style_context(
            _board(), BarChart(id="t", type="bar", style=patch)
        )
        merged_x = resolved_axis_style(
            result, "axis_x", "ordinal", chart_type="", labeled=False
        )
        merged_y = resolved_axis_style(
            result, "axis_y", "quantitative", chart_type="", labeled=False
        )
        assert merged_x.grid.visible is False
        assert merged_y.grid.visible is False

    def test_per_axis_overrides_global(self) -> None:
        """style.axis_y.grid.visible=True wins over style.axis_x.grid.visible=False for axis_y."""
        patch = BarChartStylePatch(
            axis_x=AxisXStylePatch(grid=BaseAxisGridStylePatch(visible=False)),
            axis_y=AxisYStylePatch(grid=BaseAxisGridStylePatch(visible=True)),
        )
        result = build_chart_style_context(
            _board(), BarChart(id="t", type="bar", style=patch)
        )
        merged_x = resolved_axis_style(
            result, "axis_x", "ordinal", chart_type="", labeled=False
        )
        merged_y = resolved_axis_style(
            result, "axis_y", "quantitative", chart_type="", labeled=False
        )
        assert merged_y.grid.visible is True
        assert merged_x.grid.visible is False


class TestChartLocalAxisQuantitativeMerge:
    def test_quantitative_grid_dash_propagates(self) -> None:
        patch = BarChartStylePatch(
            axis_quantitative=QuantitativeAxisStylePatch(
                grid=BaseAxisGridStylePatch(dash=[2, 2])
            )
        )
        result = build_chart_style_context(
            _board(), BarChart(id="t", type="bar", style=patch)
        )
        merged = resolved_axis_style(
            result, "axis_y", "quantitative", chart_type="", labeled=False
        )
        assert merged.grid.dash == [2, 2]


class TestChartLocalAxisBandMerge:
    def test_axis_band_label_padding_propagates(self) -> None:
        patch = BarChartStylePatch(
            axis_band=BandAxisStylePatch(labels=AxisLabelStylePatch(padding=18))
        )
        result = build_chart_style_context(
            _board(), BarChart(id="t", type="bar", style=patch)
        )
        merged = resolved_axis_style(
            result, "axis_x", "ordinal", chart_type="", labeled=False
        )
        assert merged.labels.padding == 18

    def test_no_band_patch_leaves_band_unset(self) -> None:
        result = build_chart_style_context(
            _board(), BarChart(id="t", type="bar", style=BarChartStylePatch())
        )
        assert result.axis_overrides_band is None


class TestChartTypeGapHandlingCascade:
    """Scatter-only theme pin for fill (cascade layer 4)."""

    def test_scatter_theme_pins_null_other_families_inherit_global(self) -> None:
        board = _board()
        assert board.scatter.axis_x is not None
        assert board.scatter.axis_x.fill == "null"

        assert board.bar.axis_x is not None
        assert board.bar.axis_x.fill is None
        for family in (board.line, board.area):
            assert family.axis_x is None

        line_axis = resolved_axis_style(
            board,
            "axis_x",
            "ordinal",
            chart_type="line",
            labeled=False,
        )
        assert line_axis.fill == "null"

    def test_scatter_stays_null_when_theme_global_axis_x_is_linear(self) -> None:
        """The scatter honest-dots pin survives a *theme* change to the global
        axis_x.fill. A theme-internal global change (simulated by replacing the
        theme passthrough) must not connect gaps on scatter — the pin
        (charts.scatter.axis_x.fill: null) still wins. Deleting that pin from
        _base.yaml makes scatter inherit the linear global and fails this test.
        """
        board = _board()
        global_x = board.axis_x.model_copy(update={"fill": "linear"})
        board_linear = dataclasses.replace(board, axis_x=global_x)
        effective = build_chart_style_context(
            board_linear, BarChart(id="t", type="bar")
        )

        assert effective.scatter.axis_x is not None
        assert effective.scatter.axis_x.fill == "null"
        scatter_axis = resolved_axis_style(
            effective,
            "axis_x",
            "ordinal",
            chart_type="scatter",
            labeled=False,
        )
        assert scatter_axis.fill == "null"

        line_axis = resolved_axis_style(
            effective,
            "axis_x",
            "ordinal",
            chart_type="line",
            labeled=False,
        )
        assert line_axis.fill == "linear"

    def test_board_axis_x_fill_loses_to_scatter_pin(self) -> None:
        """The theme's scatter honest-dots pin (``charts.scatter.axis_x.fill``)
        is more specific than a board's ``charts.axis_x.fill``, so the board's
        general value does not reach scatter; the board's own
        ``charts.scatter.axis_x.fill`` does. Other families take the general one.
        """
        from dbt_charts.core.compile.models.style.authored import StylePatch

        base = get_theme_style()

        def fill(patch: dict, chart_type: str) -> str | None:
            ctx = resolve_chart_style_context(base, StylePatch.model_validate(patch))
            return resolved_axis_style(
                ctx,
                "axis_x",
                "quantitative",
                chart_type=chart_type,
                labeled=False,
            ).fill

        general = {"charts": {"axis_x": {"fill": "linear"}}}
        assert fill({}, "scatter") == "null"
        assert fill(general, "scatter") == "null"
        assert fill(general, "line") == "linear"
        specific = {"charts": {"scatter": {"axis_x": {"fill": "linear"}}}}
        assert fill(specific, "scatter") == "linear"


class TestChartTypeAxisAlignCascade:
    """``label.align: inward`` is theme-settable via the existing Layer-4
    chart-type axis patch (``charts.<family>.axis_x``/``axis_y``) — not a
    global ``axis_band`` slice, which has no theme-resolved representation
    (see ``resolved_axis_style``'s comment: "axis_band has no theme-resolved
    representation; only chart-local applies"). Reusing the live per-family
    wiring means a theme default reaches the categorical axis with no cascade
    surgery.

    ``inward``/``outward`` only resolve to a concrete left/right once the
    axis's final edge is known — ``resolved_axis_style()``'s default
    ``edge=None`` can't see that (it's a generic cascade reader, not a
    per-chart resolver), so propagation here is checked at the merge stage
    (``_merge_axis_cascade`` + the raw patch) and via an explicit
    ``build_resolved_axis(...,
        format=baked_format(...), edge=...)`` call. Edge resolution itself is
    covered by ``TestOwnSideAlignEdgeMapping`` below.
    """

    def test_stark_bar_axis_x_align_is_none(self) -> None:
        """The neutral base theme ships no align default — stark/plain unchanged."""
        stark = resolve_chart_style_context(get_theme_style("stark"))
        merged = resolved_axis_style(
            stark,
            "axis_x",
            "ordinal",
            chart_type="bar",
            labeled=False,
        )
        assert merged.labels.align is None

    def test_editorial_bar_axis_x_align_is_inward_and_resolves_to_an_edge(
        self,
    ) -> None:
        """The default (editorial) theme opts horizontal-bar rows in via the
        per-family axis_x patch (align: inward), which resolves to the
        concrete edge once one is known."""
        board = _board()
        patch = board.bar.axis_x
        assert patch is not None
        assert patch.labels is not None
        assert patch.labels.align == "inward"

        merged, band_position = _merge_axis_cascade(
            board, "axis_x", "ordinal", chart_type="bar", labeled=False
        )
        assert (
            build_resolved_axis(
                merged,
                format=baked_format(merged),
                band_position=band_position,
                edge="left",
                chart_id="test",
                formats=None,
            ).labels.align
            == "left"
        )
        assert (
            build_resolved_axis(
                merged,
                format=baked_format(merged),
                band_position=band_position,
                edge="right",
                chart_id="test",
                formats=None,
            ).labels.align
            == "right"
        )
        assert (
            build_resolved_axis(
                merged,
                format=baked_format(merged),
                band_position=band_position,
                edge=None,
                chart_id="test",
                formats=None,
            ).labels.align
            is None
        )

    def test_bar_axis_x_label_align_propagates_via_chart_type_patch(self) -> None:
        board = _board()
        align_patch = AxisXStylePatch(labels=DimensionLabelStylePatch(align="inward"))
        custom_bar = board.bar.model_copy(update={"axis_x": align_patch})
        custom_charts = dataclasses.replace(board, bar=custom_bar)

        merged, band_position = _merge_axis_cascade(
            custom_charts,
            "axis_x",
            "ordinal",
            chart_type="bar",
            labeled=False,
        )
        assert (
            build_resolved_axis(
                merged,
                format=baked_format(merged),
                band_position=band_position,
                edge="left",
                chart_id="test",
                formats=None,
            ).labels.align
            == "left"
        )

    def test_heatmap_axis_y_label_align_propagates_via_chart_type_patch(self) -> None:
        board = _board()
        align_patch = AxisYStylePatch(labels=AxisLabelStylePatch(align="inward"))
        custom_heatmap = board.heatmap.model_copy(update={"axis_y": align_patch})
        custom_charts = dataclasses.replace(board, heatmap=custom_heatmap)

        merged, band_position = _merge_axis_cascade(
            custom_charts,
            "axis_y",
            "nominal",
            chart_type="heatmap",
            labeled=False,
        )
        assert (
            build_resolved_axis(
                merged,
                format=baked_format(merged),
                band_position=band_position,
                edge="left",
                chart_id="test",
                formats=None,
            ).labels.align
            == "left"
        )


class TestOwnSideAlignEdgeMapping:
    """``build_resolved_axis``'s ``edge`` parameter is the sole place
    ``label.align``/``title.align``'s ``inward``/``outward`` resolve to a
    concrete ``left``/``right`` — the render layer never sees the directive
    itself. Exercises the mapping directly against a real theme-cascaded
    ``AxisXStyle`` (not a hand-built stub) so every other field stays
    cascade-complete.
    """

    def _merged_axis(
        self, label_align: str | None, title_align: str | None = None
    ) -> AxisXStyle:
        board = _board()
        merged, _ = _merge_axis_cascade(
            board, "axis_x", "ordinal", chart_type="", labeled=False
        )
        label = merged.labels.model_copy(update={"align": label_align})
        title = merged.title.model_copy(update={"align": title_align})
        return merged.model_copy(update={"labels": label, "title": title})

    def test_inward_on_left_edge_resolves_to_left(self) -> None:
        axis = self._merged_axis("inward")
        assert (
            build_resolved_axis(
                axis,
                format=baked_format(axis),
                edge="left",
                chart_id="test",
                formats=None,
            ).labels.align
            == "left"
        )

    def test_inward_on_right_edge_resolves_to_right(self) -> None:
        axis = self._merged_axis("inward")
        assert (
            build_resolved_axis(
                axis,
                format=baked_format(axis),
                edge="right",
                chart_id="test",
                formats=None,
            ).labels.align
            == "right"
        )

    def test_outward_on_left_edge_resolves_to_right(self) -> None:
        """outward is the opposite side from inward — away from the plot."""
        axis = self._merged_axis("outward")
        assert (
            build_resolved_axis(
                axis,
                format=baked_format(axis),
                edge="left",
                chart_id="test",
                formats=None,
            ).labels.align
            == "right"
        )

    def test_outward_on_right_edge_resolves_to_left(self) -> None:
        axis = self._merged_axis("outward")
        assert (
            build_resolved_axis(
                axis,
                format=baked_format(axis),
                edge="right",
                chart_id="test",
                formats=None,
            ).labels.align
            == "left"
        )

    def test_inward_with_no_edge_is_a_no_op(self) -> None:
        """A bottom/top axis (or an edge not yet known) has no side to
        resolve inward/outward against — collapses to None, not an error."""
        axis = self._merged_axis("inward")
        assert (
            build_resolved_axis(
                axis,
                format=baked_format(axis),
                edge=None,
                chart_id="test",
                formats=None,
            ).labels.align
            is None
        )

    def test_outward_with_no_edge_is_a_no_op(self) -> None:
        axis = self._merged_axis("outward")
        assert (
            build_resolved_axis(
                axis,
                format=baked_format(axis),
                edge=None,
                chart_id="test",
                formats=None,
            ).labels.align
            is None
        )

    def test_absolute_left_passes_through_regardless_of_edge(self) -> None:
        axis = self._merged_axis("left")
        assert (
            build_resolved_axis(
                axis,
                format=baked_format(axis),
                edge="right",
                chart_id="test",
                formats=None,
            ).labels.align
            == "left"
        )

    def test_absolute_right_passes_through_regardless_of_edge(self) -> None:
        axis = self._merged_axis("right")
        assert (
            build_resolved_axis(
                axis,
                format=baked_format(axis),
                edge="left",
                chart_id="test",
                formats=None,
            ).labels.align
            == "right"
        )

    def test_title_align_resolves_the_same_as_label_align(self) -> None:
        axis = self._merged_axis(None, title_align="inward")
        assert (
            build_resolved_axis(
                axis,
                format=baked_format(axis),
                edge="right",
                chart_id="test",
                formats=None,
            ).title.align
            == "right"
        )

    def test_center_passes_through_regardless_of_edge(self) -> None:
        """center is absolute (not own-side-relative), unlike inward/outward —
        it stays center on every edge, including no edge at all. Kept as a
        valid authored value (e.g. a bottom-orient category axis centering
        its tick labels, a legitimate non-invading use with no left/right
        edge in play at all)."""
        axis = self._merged_axis("center")
        assert (
            build_resolved_axis(
                axis,
                format=baked_format(axis),
                edge="left",
                chart_id="test",
                formats=None,
            ).labels.align
            == "center"
        )
        assert (
            build_resolved_axis(
                axis,
                format=baked_format(axis),
                edge="right",
                chart_id="test",
                formats=None,
            ).labels.align
            == "center"
        )
        assert (
            build_resolved_axis(
                axis,
                format=baked_format(axis),
                edge=None,
                chart_id="test",
                formats=None,
            ).labels.align
            == "center"
        )


class TestPreInheritPatchReMerge:
    """A patch targeting a slot that is None pre-inherit (e.g. pie.marks.slice.labels,
    which only gets populated by the global inherit from charts.marks.slice.labels)
    must still win after the post-inherit re-merge — not silently fall back to the
    inherited default. See build_chart_style_context's re-merge block comment.
    """

    def test_pie_slice_labels_template_override_beats_inherited_default(self) -> None:
        from dbt_charts.core.compile.models.chart.normalized import PieChart
        from dbt_charts.core.compile.models.style.authored import PieChartStylePatch

        board = _board()
        # board is already fully inherited (labels populated from
        # charts.marks.slice.labels); the None pre-inherit state this test
        # guards only exists transiently inside build_chart_style_context's
        # per-chart re-merge, not at the board level.
        assert board.pie.marks.slice.labels is not None
        board_template = board.pie.marks.slice.labels.template

        patch = PieChartStylePatch.model_validate(
            {"marks": {"slice": {"labels": {"template": "custom {{ value }}"}}}}
        )
        chart = PieChart(id="t", type="pie", theta="value", style=patch)
        result = build_chart_style_context(board, chart)

        assert result.pie.marks.slice.labels is not None
        assert result.pie.marks.slice.labels.template == "custom {{ value }}"
        assert result.pie.marks.slice.labels.template != board_template


class TestChartLocalLegendMerge:
    def test_legend_leaf_propagates(self) -> None:
        patch = BarChartStylePatch(legend=LegendStylePatch(symbol_limit=5))
        result = build_chart_style_context(
            _board(), BarChart(id="t", type="bar", style=patch)
        )
        assert result.legend.symbol_limit == 5


class TestAxisScaleCascade:
    """Chart-local style.axis_y.scale lands on the resolved axis style."""

    def test_per_axis_scale_propagates(self) -> None:
        patch = BarChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"zero": False}}}}
        )
        result = build_chart_style_context(
            _board(), BarChart(id="t", type="bar", style=patch)
        )
        merged = resolved_axis_style(
            result, "axis_y", "quantitative", chart_type="", labeled=False
        )
        assert merged.scale is not None
        assert merged.scale.continuous is not None
        assert merged.scale.continuous.zero is False


class TestChartLocalPaletteMerge:
    """Chart-local style.color.categorical resolves and overrides theme palette."""

    def test_named_palette_overrides_theme_palette(self) -> None:
        from dbt_charts.core.compile.resolve.style.palette import (
            palette as resolve_palette,
        )

        patch = BarChartStylePatch.model_validate(
            {"color": {"categorical": {"palette": "dbt-seq-rust"}}}
        )
        result = build_chart_style_context(
            _board(), BarChart(id="t", type="bar", style=patch)
        )
        expected = resolve_palette("dbt-seq-rust")
        assert result.palette == expected

    def test_no_palette_override_preserves_theme_palette(self) -> None:
        board = _board()
        result = build_chart_style_context(
            board, BarChart(id="t", type="bar", style=BarChartStylePatch())
        )
        assert result.palette == board.palette

    def test_unknown_palette_raises(self) -> None:
        from dbt_charts.core.compile.resolve.style.palette import UnknownPaletteError

        patch = BarChartStylePatch.model_validate(
            {"color": {"categorical": {"palette": "not-a-real-palette"}}}
        )
        with pytest.raises(UnknownPaletteError):
            build_chart_style_context(
                _board(), BarChart(id="t", type="bar", style=patch)
            )

    def test_alias_palette_sets_requested_alias_palette(self) -> None:
        """A chart-local WARN-PALETTE-UNSUPPORTED anti-pattern alias survives
        the style cascade onto ResolvedChartsStyle, so a render-stage detector
        can see it — palette() itself resolves silently."""
        patch = BarChartStylePatch.model_validate(
            {"color": {"categorical": {"palette": "RdYlGn"}}}
        )
        result = build_chart_style_context(
            _board(), BarChart(id="t", type="bar", style=patch)
        )
        assert result.requested_alias_palette == "RdYlGn"

    def test_non_alias_palette_leaves_requested_alias_palette_none(self) -> None:
        patch = BarChartStylePatch.model_validate(
            {"color": {"categorical": {"palette": "dbt-seq-rust"}}}
        )
        result = build_chart_style_context(
            _board(), BarChart(id="t", type="bar", style=patch)
        )
        assert result.requested_alias_palette is None

    def test_no_palette_override_preserves_board_requested_alias_palette(self) -> None:
        board = _board()
        result = build_chart_style_context(
            board, BarChart(id="t", type="bar", style=BarChartStylePatch())
        )
        assert result.requested_alias_palette == board.requested_alias_palette


class TestRendererReadsNoPatch:
    """The directive: a built ResolvedChart exposes resolved_style with chart-local
    patches already merged. Code that constructs ResolvedChart still threads the
    Patch through resolve(), but downstream consumers read the merged
    value only — never the patch.
    """

    def test_resolve_chart_resolved_style_has_axis_y_override(self) -> None:

        chart = BarChart(
            id="t",
            type="bar",
            x="a",
            y="b",
            style=BarChartStylePatch(
                axis_y=AxisYStylePatch(labels=AxisLabelStylePatch(padding=42))
            ),
        )
        resolved = resolve(chart, [{"a": "x", "b": 1}], chart_style_context=_board())
        assert resolved.style.axis_y.labels.padding == 42

    def test_resolve_chart_orientation_promotes_from_style(self) -> None:

        chart = BarChart(
            id="t",
            type="bar",
            x="a",
            y="b",
            style=BarChartStylePatch(orientation="horizontal"),
        )
        resolved = resolve(chart, [{"a": "x", "b": 1}], chart_style_context=_board())
        assert resolved.orientation == "horizontal"

    def test_resolve_chart_default_orientation_vertical(self) -> None:
        """Continuous (quantitative) x → default vertical. D-019: nominal x would
        flip horizontal, so use a quantitative field to exercise the vertical default.
        """

        chart = BarChart(id="t", type="bar", x="a", y="b")
        resolved = resolve(chart, [{"a": 1, "b": 1}], chart_style_context=_board())
        assert resolved.orientation == "vertical"


class TestMergedStyleIsMergedChartsStyle:
    """ResolvedBarChart.style is a ResolvedBarStyle (the merged value)."""

    def test_type(self) -> None:

        chart = BarChart(id="t", type="bar", x="a", y="b")
        resolved = resolve(chart, [{"a": "x", "b": 1}], chart_style_context=_board())
        assert isinstance(resolved.style, ResolvedBarStyle)


class TestStyleFormats:
    """style.formats cascades from theme to resolved_style.charts.formats.

    The 5 canonical aliases (currency, percent, compact, integer, number) are
    now engine-owned predefined names. They are NOT in the theme's formats
    dict — the engine resolves them without needing a user-alias entry.
    """

    def test_compiled_style_formats_field_type(self):
        style = get_theme_style()
        assert style.formats is None or isinstance(style.formats, dict)

    def test_default_theme_has_no_predefined_names_in_formats(self):
        # Predefined names are engine-owned; the default theme ships no formats dict at all.
        style = get_theme_style()
        assert style.formats is None

    def test_formats_propagate_to_resolved_charts(self):
        from dbt_charts.core.compile.models.style.authored import StylePatch
        from dbt_charts.core.compile.resolve.style.board import resolve_style

        base_style = get_theme_style()
        # Custom (non-predefined) user aliases propagate.
        patch = StylePatch(formats={"revenue": "$~s", "cost": ",.0f"})
        resolved = resolve_style(base_style, patch)
        formats = resolved.chart_defaults.formats
        assert formats is not None
        assert formats["revenue"] == "$~s"
        assert formats["cost"] == ",.0f"

    def test_board_formats_override_theme_key_wise(self):
        from dbt_charts.core.compile.merge import merge_onto_base
        from dbt_charts.core.compile.models.style.authored import StylePatch
        from dbt_charts.core.compile.models.style.theme import Style

        base_style = get_theme_style()
        # Board adds custom aliases; predefined names are never in the dict.
        patch = StylePatch(formats={"revenue": "$~s", "cost": ",.0f"})
        merged = merge_onto_base(base_style, patch)
        assert isinstance(merged, Style)
        assert merged.formats["revenue"] == "$~s"
        assert merged.formats["cost"] == ",.0f"

    def test_resolved_charts_formats_carries_board_override(self):
        from dbt_charts.core.compile.models.style.authored import StylePatch
        from dbt_charts.core.compile.resolve.style.board import resolve_style

        base_style = get_theme_style()
        # A custom alias propagates to resolved chart defaults.
        patch = StylePatch(formats={"revenue": "$~s"})
        resolved = resolve_style(base_style, patch)
        assert resolved.chart_defaults.formats["revenue"] == "$~s"

    def test_formats_none_when_no_theme_key(self):
        # A Style with formats=None is valid (no aliases defined).
        style = get_theme_style().model_copy(update={"formats": None})
        assert style.formats is None


class TestResolveStyleCacheIdentity:
    """resolve_style cache-hit path returns the same frozen instance without copying."""

    def test_cache_hit_returns_same_instance(self) -> None:
        base = get_theme_style()
        r1 = resolve_style(base)
        r2 = resolve_style(base)
        assert r1 is r2, (
            "cache hit must return the identical frozen instance, not a copy"
        )

    def test_resolved_style_is_frozen(self) -> None:
        resolved = resolve_style(get_theme_style())
        with pytest.raises(dataclasses.FrozenInstanceError):
            resolved.background = "#abc"  # type: ignore[misc]

    def test_every_merged_dataclass_in_tree_is_frozen(self) -> None:
        """Walks both dataclass and pydantic nodes; asserts frozen on every compiled node.

        Patch types are excluded from the frozen assertion — patches are authored-overlay
        inputs, never cached output. A class is a patch if its name ends with "Patch"
        (covers BorderStylePatch, BaseAxisStylePatch, and dynamically-built *Patch bases)
        or if _PatchBase is anywhere in its MRO (covers FontStyle, which doubles as a
        patch at every cascade level, and subclasses like RootFontStyle that inherit
        the same double-duty via FontStyle rather than subclassing _PatchBase
        directly — reached via ResolvedStyle.pre_style, the pre-inherit Style tree).
        """
        from pydantic import BaseModel

        from dbt_charts.core.compile.models.factories import _PatchBase

        resolved = resolve_style(get_theme_style())

        def _is_patch(cls: type) -> bool:
            return cls.__name__.endswith("Patch") or issubclass(cls, _PatchBase)

        def _walk(obj: object, seen: set[int]) -> None:
            obj_id = id(obj)
            if obj_id in seen:
                return
            seen.add(obj_id)
            if dataclasses.is_dataclass(obj):
                cls = type(obj)
                params = getattr(cls, "__dataclass_params__", None)
                assert params is not None and params.frozen, (
                    f"{cls.__name__} must be frozen=True to safely share cached instances"
                )
                for field in dataclasses.fields(obj):
                    _walk(getattr(obj, field.name), seen)
            elif isinstance(obj, BaseModel):
                cls = type(obj)
                if _is_patch(cls):
                    for name in cls.model_fields:
                        _walk(getattr(obj, name), seen)
                    return
                assert cls.model_config.get("frozen") is True, (
                    f"{cls.__name__} must have model_config frozen=True to safely share cached instances"
                )
                for name in cls.model_fields:
                    _walk(getattr(obj, name), seen)

        _walk(resolved, set())

    def test_resolve_style_cached_pydantic_node_rejects_mutation(self) -> None:
        """Attribute assignment on a cached chart-family pydantic node raises ValidationError."""
        from pydantic import ValidationError

        resolved = resolve_style(get_theme_style())
        # resolved.chart_defaults.bar is a frozen BarChartStyle (chart-family compiled type).
        with pytest.raises(ValidationError):
            resolved.chart_defaults.bar.aspect_ratio = 99.0  # type: ignore[misc]


class TestNoUnresolvedTokensInBuiltInThemes:
    """Property: no color token survives resolve_style for any built-in theme."""

    @pytest.mark.parametrize("theme_name", _RESOLVABLE_BUILT_IN_THEMES)
    def test_no_color_token_survives_resolve_style(self, theme_name: str) -> None:
        from pydantic import BaseModel as _BaseModel

        from dbt_charts.core.colors import is_color_token
        from dbt_charts.core.compile.config import get_theme_style

        def _collect_string_fields(obj: object, seen: set[int]) -> list[str]:
            obj_id = id(obj)
            if obj_id in seen:
                return []
            seen.add(obj_id)
            strings: list[str] = []
            if dataclasses.is_dataclass(obj):
                for field in dataclasses.fields(obj):
                    strings.extend(
                        _collect_string_fields(getattr(obj, field.name), seen)
                    )
            elif isinstance(obj, _BaseModel):
                for name in type(obj).model_fields:
                    strings.extend(_collect_string_fields(getattr(obj, name), seen))
            elif isinstance(obj, str):
                strings.append(obj)
            elif isinstance(obj, list):
                for item in obj:
                    strings.extend(_collect_string_fields(item, seen))
            return strings

        base = get_theme_style(theme_name)
        resolved = resolve_style(base)
        all_strings = _collect_string_fields(resolved, set())
        token_strings = [s for s in all_strings if is_color_token(s)]
        assert token_strings == [], (
            f"Theme {theme_name!r}: unresolved color tokens in resolved ResolvedStyle:"
            f" {token_strings!r}"
        )


class TestPatchIntroducedTokenIsResolved:
    """A board style: patch that introduces a color token must resolve to hex."""

    def test_patch_direct_palette_token_resolves_to_hex(self) -> None:
        # dbt-grays.canvas is a valid direct palette.slot token → #FAFAFA
        from dbt_charts.core.colors import is_color_token
        from dbt_charts.core.compile.models.style.authored import StylePatch

        base = get_theme_style()
        patch = StylePatch.model_validate({"background": "dbt-grays.canvas"})
        resolved = resolve_style(base, patch)
        assert not is_color_token(resolved.background), (
            f"patch-introduced token was not resolved: {resolved.background!r}"
        )
        assert resolved.background.startswith("#"), (
            f"expected hex color, got {resolved.background!r}"
        )


class TestSelfTokenInPatchResolvesToBaseBackground:
    """A patch with style: {background: 'theme.background'} resolves to the base's background."""

    def test_self_token_resolves_to_theme_background(self) -> None:
        from dbt_charts.core.compile.models.style.authored import StylePatch

        base = get_theme_style()
        base_background = base.background
        patch = StylePatch.model_validate({"background": "theme.background"})
        resolved = resolve_style(base, patch)
        assert resolved.background == base_background, (
            f"theme.background token in patch should resolve to {base_background!r},"
            f" got {resolved.background!r}"
        )


class TestEmojiInResolvedStyle:
    """Emoji family is always appended when the mode maps to a bundled family; idempotent."""

    def test_no_patch_emoji_appears_exactly_once(self) -> None:
        from dbt_charts.core.fonts import NOTO_EMOJI_FONT_FAMILY

        base = get_theme_style()
        resolved = resolve_style(base)
        family = resolved.font.family
        count = family.count(NOTO_EMOJI_FONT_FAMILY)
        assert count == 1, (
            f"Emoji font family should appear exactly once, found {count} times: {family!r}"
        )

    def test_patch_with_non_emoji_change_still_has_emoji_family(self) -> None:
        """A patch that changes background (not emoji) still gets emoji family appended."""
        from dbt_charts.core.compile.models.style.authored import StylePatch
        from dbt_charts.core.fonts import NOTO_EMOJI_FONT_FAMILY

        base = get_theme_style()
        patch = StylePatch.model_validate({"background": "dbt-grays.canvas"})
        resolved = resolve_style(base, patch)
        family = resolved.font.family
        assert NOTO_EMOJI_FONT_FAMILY in family, (
            f"Expected emoji font in family stack even for non-emoji patch, got: {family!r}"
        )

    def test_patch_rejects_deleted_color_emoji_mode(self) -> None:
        """The color emoji font is deleted — 'color' must fail validation, not
        silently downgrade or resolve to a family."""
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.authored import StylePatch

        with pytest.raises(ValidationError):
            StylePatch.model_validate({"font": {"emoji": "color"}})


class TestResolvedAxisStyleReturnType:
    """resolved_axis_style must return ResolvedAxisStyle, not a raw axis style."""

    def test_returns_resolved_axis_style(self) -> None:
        board = _board()
        result = build_chart_style_context(board, BarChart(id="t", type="bar"))
        merged = resolved_axis_style(
            result, "axis_y", "quantitative", chart_type="", labeled=False
        )
        assert isinstance(merged, ResolvedAxisStyle), (
            f"expected ResolvedAxisStyle, got {type(merged).__name__}"
        )


_SAMPLE_DATA = [{"month": "Jan", "revenue": 100}]


class TestAxisFormatPropagation:
    """A chart's own ``axis_*.labels.format`` flows through the style cascade.

    The fast path in build_chart_style_context skips the per-chart inherit pass
    for a chart with no style block, so the axis formats are baked at
    axis-resolution time and these fixtures go through the full resolve()
    pipeline.
    """

    @pytest.mark.parametrize(
        ("chart_cls", "style_cls", "chart_type"),
        [
            (BarChart, BarChartStylePatch, "bar"),
            (LineChart, LineChartStylePatch, "line"),
            (AreaChart, AreaChartStylePatch, "area"),
        ],
        ids=("bar", "line", "area"),
    )
    def test_chart_axis_y_format_beats_theme_family_axis_y_format(
        self, chart_cls, style_cls, chart_type
    ) -> None:
        """A chart's own axis_y format outranks a theme-family axis format."""
        theme = get_theme_style()
        family = getattr(theme.charts, chart_type).model_copy(
            update={
                "axis_y": AxisYStylePatch(labels=AxisLabelStylePatch(format=",.2f"))
            }
        )
        board = resolve_chart_style_context(
            theme.model_copy(
                update={
                    "charts": theme.charts.model_copy(
                        update={chart_type: family},
                    )
                }
            )
        )
        style = style_cls.model_validate({"axis_y": {"labels": {"format": ",.0f"}}})
        chart = chart_cls(id="t", type=chart_type, x="month", y="revenue", style=style)

        resolved = resolve(chart, _SAMPLE_DATA, chart_style_context=board)

        assert resolved.style.axis_y.labels.format.spec == ",.0f"

    def test_axis_y_format_as_a_format_block_propagates(self) -> None:
        board = _board()
        patch = LineChartStylePatch.model_validate(
            {"axis_y": {"labels": {"format": {"spec": ",.0f"}}}}
        )
        chart = LineChart(id="t", type="line", x="month", y="revenue", style=patch)
        resolved = resolve(chart, _SAMPLE_DATA, chart_style_context=board)
        assert resolved.style.axis_y.labels.format.spec == ",.0f"

    def test_histogram_axis_y_format_propagates(self) -> None:
        board = _board()
        patch = BarChartStylePatch.model_validate(
            {"axis_y": {"labels": {"format": ",.2f"}}}
        )
        chart = BarChart(id="t", type="histogram", x="price", style=patch)
        resolved = resolve(
            chart, [{"price": 10.0}, {"price": 20.0}], chart_style_context=board
        )
        assert resolved.style.axis_y.labels.format.spec == ",.2f"

    def test_axis_x_format_reaches_a_temporal_axis(self) -> None:
        board = _board()
        patch = LineChartStylePatch.model_validate(
            {"axis_x": {"labels": {"format": "%b %Y"}}}
        )
        chart = LineChart(id="t", type="line", x="month", y="revenue", style=patch)
        data = [{"month": "2024-01-01", "revenue": 100}]
        resolved = resolve(chart, data, chart_style_context=board)
        assert resolved.style.axis_x.labels.format.spec == "%b %Y"

    def test_authored_mark_label_format_survives_the_cascade(self) -> None:
        """An authored ``marks.bar.labels.format`` is never overwritten by the axis
        inherit link."""
        board = _board()
        patch = BarChartStylePatch.model_validate(
            {
                "axis_y": {"labels": {"format": ",.0f"}},
                "marks": {"bar": {"labels": {"format": ".2%"}}},
            }
        )
        chart = BarChart(id="t", type="bar", style=patch)
        result = build_chart_style_context(board, chart)
        assert result.bar.marks.bar.labels.format == ".2%"
