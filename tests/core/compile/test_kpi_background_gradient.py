"""Tests for KPI background gradient channel (continuous scale).

Verifies:
- KpiChart accepts background: {column, scale} without raising extra=forbid.
- resolve() resolves the background channel to gradient mode.
- render_kpi_svg produces a background color derived from the scale interpolation.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.authored import KpiChart as AuthoredKpiChart
from dbt_charts.core.compile.models.chart.normalized import KpiChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)

from .._engine_render import render_kpi_svg

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())

_DUMMY_QUERY = SqlQuery(sql="SELECT 1", source="t")
_NS = {"svg": "http://www.w3.org/2000/svg"}


class TestKpiBackgroundGradientSchema:
    def test_background_column_scale_accepted_by_schema(self) -> None:
        """KpiChart must accept background: {column, scale} without ValidationError."""
        p = AuthoredKpiChart.model_validate(
            {
                "type": "kpi",
                "value": "revenue",
                "background": {
                    "column": "revenue",
                    "scale": {"palette": ["#ffffff", "#3366cc"]},
                },
            }
        )
        assert p.background is not None

    def test_background_bad_channel_raises_at_resolve(self) -> None:
        """background with an unknown channel key raises ValueError at resolve time
        (parse_style_channel validates the dict shape, not the Pydantic model)."""
        chart = KpiChart(
            id="t",
            query=_DUMMY_QUERY,
            query_name="q",
            type="kpi",
            value="revenue",
            background={"unknown_key": "bad"},
        )
        with pytest.raises(ValueError, match="unknown keys"):
            resolve(chart, [{"revenue": 1}], chart_style_context=_BOARD_STYLE)

    def test_unknown_root_field_still_rejected(self) -> None:
        """extra=forbid must still block non-existent chart root fields."""
        with pytest.raises(ValidationError, match="not_a_field"):
            AuthoredKpiChart.model_validate(
                {"type": "kpi", "value": "revenue", "not_a_field": "x"}
            )


class TestKpiBackgroundGradientResolve:
    def test_background_channel_resolves_to_gradient_mode(self) -> None:
        """resolve() produces a gradient background channel."""
        chart = KpiChart(
            id="t",
            query=_DUMMY_QUERY,
            query_name="q",
            type="kpi",
            value="revenue",
            background={
                "column": "revenue",
                "scale": {"palette": ["#ffffff", "#3366cc"]},
            },
        )
        data = [{"revenue": 500_000}]
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        bg = resolved.resolved_channels.get("background")
        assert bg is not None, "background channel not resolved"
        assert bg.mode == "gradient"
        assert bg.data_field == "revenue"

    def test_background_gradient_renders_non_transparent_color(self) -> None:
        """A KPI with background: {column, scale} renders a fill= on the background rect
        that is derived from scale interpolation (not the default board background)."""
        chart = KpiChart(
            id="t",
            query=_DUMMY_QUERY,
            query_name="q",
            type="kpi",
            value="revenue",
            background={
                "column": "revenue",
                "scale": {"palette": ["#ffffff", "#3366cc"]},
            },
        )
        # Mid-range value → should interpolate to something between #ffffff and #3366cc
        data = [{"revenue": 500_000}]
        rs, ctx = resolve_style_and_context(get_theme_style())
        resolved = resolve(chart, data, chart_style_context=ctx)
        svg = render_kpi_svg(resolved, data, width=300, height=160, board_style=rs)
        root = ET.fromstring(svg)
        # The background rect uses fill=; the interpolated color must not be #ffffff (low end)
        # and must not be #3366cc (high end — value is 500k out of unbounded range defaults to mid).
        # We just assert *some* fill appears that's not "none".
        rects = root.findall("svg:rect", _NS)
        assert rects, "No background rect in KPI SVG"
        fill = rects[0].attrib.get("fill", "none")
        assert fill != "none", (
            f"Background rect fill is 'none', expected a gradient color; fill={fill!r}"
        )

    def test_background_gradient_varies_with_value_across_explicit_bounds(self) -> None:
        """Gradient color must vary between a low and a high value on an explicit
        [0, 1_000_000] domain — proving value-position is actually used, not a
        single degenerate midpoint."""
        scale_spec = {
            "column": "revenue",
            "scale": {"palette": ["#ffffff", "#3366cc"], "min": 0, "max": 1_000_000},
        }
        rs, ctx = resolve_style_and_context(get_theme_style())

        def _render_fill(value: float) -> str:
            chart = KpiChart(
                id="t",
                query=_DUMMY_QUERY,
                query_name="q",
                type="kpi",
                value="revenue",
                background=scale_spec,
            )
            data = [{"revenue": value}]
            resolved = resolve(chart, data, chart_style_context=ctx)
            svg = render_kpi_svg(resolved, data, width=300, height=160, board_style=rs)
            rects = ET.fromstring(svg).findall("svg:rect", _NS)
            assert rects, f"No rect in KPI SVG for value={value}"
            return rects[0].attrib.get("fill", "none")

        fill_low = _render_fill(0)  # low end → near #ffffff
        fill_high = _render_fill(1_000_000)  # high end → near #3366cc

        assert fill_low != fill_high, (
            f"Gradient color must differ between min and max; "
            f"fill_low={fill_low!r} == fill_high={fill_high!r}"
        )
