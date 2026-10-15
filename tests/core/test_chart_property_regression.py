"""Fast-lane regression tests for chart property propagation.

Derived from the chart property audit (ai_notes/charts/chart-property-audit-2026-03-27.md).
These tests assert that authored properties reach the final Vega-Lite spec through
both the chart-authored and style-authored control paths.

These run in the default CI lane (not marked slow) because they are fast spec-level
checks — no SVG rendering, no external dependencies.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.primitives import FontStyle
from dbt_charts.core.compile.models.style.authored import (
    AxisLabelStylePatch,
    AxisLineStylePatch,
    AxisTitleStylePatch,
    AxisXStylePatch,
    AxisYStylePatch,
    BarChartStylePatch,
    BaseAxisGridStylePatch,
    ChartStylePatch,
    DimensionLabelStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())


def _mark(spec: dict) -> dict:
    """Get mark dict from single-spec or first layer."""
    m = spec.get("mark", {})
    if isinstance(m, dict) and m:
        return m
    layers = spec.get("layer", [])
    return layers[0].get("mark", {}) if layers else {}


DATA = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]


# ============================================================================
# Title properties — audit found style path strong, chart path weak
# ============================================================================


class TestTitlePropertyRegression:
    """Title properties reach the final Vega-Lite spec.

    Audit finding: title is style-driven in dbt charts. The style path controlled
    17/28 properties; the chart path controlled only 2/28.
    """

    def test_title_text_via_chart(self, make_chart):
        """chart.title reaches spec.title.text."""
        chart = make_chart("bar", title="Revenue Report")
        _rc = resolve(chart, DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, DATA)
        assert spec["title"]["text"] == "Revenue Report"

    def test_subtitle_via_chart(self, make_chart):
        """chart.subtitle reaches spec.title.subtitle."""
        chart = make_chart("bar", title="Revenue", subtitle="Q1 2026")
        _rc = resolve(chart, DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, DATA)
        assert spec["title"]["subtitle"] == "Q1 2026"

    def test_title_overflow_truncate_is_default(self, make_chart):
        """The densified default overflow is ``truncate`` — long titles
        render as a single ellipsized string, not a list."""
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            title="Revenue performance by enterprise segment and partner region",
        )
        _rc = resolve(chart, DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, DATA, width=180)
        assert spec["title"]["limit"] > 0
        assert isinstance(spec["title"]["text"], str)
        assert spec["title"]["text"].endswith("…")


# ============================================================================
# Axis properties — audit found chart path strong, style path selective
# ============================================================================


class TestAxisPropertyRegression:
    """Axis properties reach the final Vega-Lite spec.

    Audit finding: axis is the healthiest section. Chart path controlled
    53/60 properties; style path was selective at 23/60.
    """

    def test_axis_label_angle_via_style(self, make_chart):
        """style.axis_x.labels.angle reaches encoding.x.axis.labelAngle."""
        chart = make_chart(
            "bar",
            style=BarChartStylePatch(
                axis_x=AxisXStylePatch(labels=DimensionLabelStylePatch(angle=-45))
            ),
        )
        _rc = resolve(chart, DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, DATA)
        assert spec["encoding"]["x"]["axis"]["labelAngle"] == -45

    def test_axis_grid_color_via_style(self, make_chart):
        """style axis grid color reaches config.axis.gridColor."""
        chart = make_chart(
            "bar",
            style=BarChartStylePatch(
                axis_y=AxisYStylePatch(grid=BaseAxisGridStylePatch(color="#cccccc"))
            ),
        )
        _rc = resolve(chart, DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, DATA)
        gc = spec.get("encoding", {}).get("y", {}).get("axis", {}).get("gridColor")
        assert gc == "#cccccc"

    def test_axis_grid_toggle_via_style(self, make_chart):
        """style axis grid visible=False reaches config.axis.grid=False."""
        chart = make_chart(
            "bar",
            style=BarChartStylePatch(
                axis_y=AxisYStylePatch(grid=BaseAxisGridStylePatch(visible=False))
            ),
        )
        _rc = resolve(chart, DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, DATA)
        assert (
            spec.get("encoding", {}).get("y", {}).get("axis", {}).get("grid") is False
        )

    def test_axis_label_color_via_style(self, make_chart):
        """style axis label font color reaches config.axis.labelColor."""
        chart = make_chart(
            "bar",
            style=BarChartStylePatch(
                axis_y=AxisYStylePatch(
                    labels=AxisLabelStylePatch(font=FontStyle(color="#333333"))
                )
            ),
        )
        _rc = resolve(chart, DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, DATA)
        assert (
            spec.get("encoding", {}).get("y", {}).get("axis", {}).get("labelColor")
            == "#333333"
        )

    def test_axis_label_font_size_via_style(self, make_chart):
        """style axis label font size reaches config.axis.labelFontSize."""
        chart = make_chart(
            "bar",
            style=BarChartStylePatch(
                axis_y=AxisYStylePatch(
                    labels=AxisLabelStylePatch(font=FontStyle(size=14.0))
                )
            ),
        )
        _rc = resolve(chart, DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, DATA)
        assert (
            spec.get("encoding", {}).get("y", {}).get("axis", {}).get("labelFontSize")
            == 14
        )

    def test_axis_title_color_via_style(self, make_chart):
        """style axis title font color reaches config.axis.titleColor when title is visible.

        titleColor is only emitted when the axis title is visible.  An authored
        y_label makes the title visible (via the label-visibility flip in
        build_chart_style_context), so the styled color reaches the VL spec.
        """
        chart = make_chart(
            "bar",
            y_label="Revenue",  # authored label → title.visible flipped to True
            style=BarChartStylePatch(
                axis_y=AxisYStylePatch(
                    title=AxisTitleStylePatch(font=FontStyle(color="#444444"))
                )
            ),
        )
        _rc = resolve(chart, DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, DATA)
        assert (
            spec.get("encoding", {}).get("y", {}).get("axis", {}).get("titleColor")
            == "#444444"
        )

    def test_axis_domain_color_via_style(self, make_chart):
        """style axis domain color reaches config.axis.domainColor."""
        chart = make_chart(
            "bar",
            style=BarChartStylePatch(
                axis_y=AxisYStylePatch(line=AxisLineStylePatch(color="#aaaaaa"))
            ),
        )
        _rc = resolve(chart, DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, DATA)
        assert (
            spec.get("encoding", {}).get("y", {}).get("axis", {}).get("domainColor")
            == "#aaaaaa"
        )

    def test_axis_label_expr_via_style(self, make_chart):
        """style.axis_x.labels.expr reaches encoding.x.axis.labelExpr."""
        chart = make_chart(
            "bar",
            style=BarChartStylePatch(
                axis_x=AxisXStylePatch(
                    labels=DimensionLabelStylePatch(expr="upper(datum.label)")
                )
            ),
        )
        _rc = resolve(chart, DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, DATA)
        assert spec["encoding"]["x"]["axis"]["labelExpr"] == "upper(datum.label)"

    def test_axis_y_grid_width_via_style(self, make_chart):
        """style.axis_y.grid.width reaches encoding.y.axis.gridWidth via the axis encoding."""
        chart = make_chart(
            "bar",
            style=BarChartStylePatch(
                axis_y=AxisYStylePatch(grid=BaseAxisGridStylePatch(width=2))
            ),
        )
        _rc = resolve(chart, DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, DATA)
        assert spec["encoding"]["y"]["axis"]["gridWidth"] == 2


# ============================================================================
# Mark properties — audit found chart path strong, style path weak
# ============================================================================


class TestBarApiNarrowingPins:
    """bar/line/area on ChartStylePatch narrowed from MarkStyle to *StylePatch.

    These pin the narrowing as intentional: MarkStyle fields like opacity/color/
    corner_radius no longer accepted on chart.style.bar — ValidationError is correct.
    """

    def test_bar_rejects_opacity_was_markstyle(self):
        """chart.style.bar.opacity is rejected — BarStylePatch has no opacity."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="opacity"):
            ChartStylePatch(bar={"opacity": 0.5})

    def test_bar_accepts_color_chart_level(self):
        """chart.style.bar.color.static is accepted — it's a chart-level paint override."""
        patch = BarChartStylePatch.model_validate({"color": {"static": "#336699"}})
        assert patch.color is not None
        assert patch.color.static == "#336699"

    def test_bar_rejects_corner_radius_was_markstyle(self):
        """chart.style.bar.corner_radius is rejected — use bar.border.radius instead."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="corner_radius"):
            ChartStylePatch(bar={"corner_radius": 4})
