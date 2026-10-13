"""Regression tests for #212: base+layers charts honor the same sizing controls
(aspect_ratio, max_height, explicit height, default) as equivalent no-layers charts.

Root cause (historical): `type: layered` had a render-first natural-height path in
layout_sizing.py that bypassed the cartesian aspect-ratio path.  After layered's
removal, bar/line/area/scatter with `layers:` are plain _CartesianChartFields
subclasses, so they flow through the normal `get_chart_content_height` path.

These tests pin that invariant: adding `layers:` to a cartesian chart must not
change the height the sizing pass computes.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    LineChart,
)
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.render.sizing import get_chart_content_height

from .._board_utils import _default_resolved_style

# ── helpers ──────────────────────────────────────────────────────────────────


def _style() -> ResolvedStyle:
    return _default_resolved_style()


def _one_line_layer() -> LineLayer:
    return LineLayer(type="line", y="target")


# ── aspect_ratio ─────────────────────────────────────────────────────────────


class TestAspectRatioWithLayers:
    """A chart with layers: must honor aspect_ratio the same as one without."""

    def test_bar_with_layers_uses_chart_aspect_ratio(self) -> None:
        """chart.aspect_ratio=2.0 → height = width / 2.0, regardless of layers."""
        # 480 / 2.0 = 240 is below max_height and is not default_chart_height,
        # so the assertion fails if either of those takes over.
        width = 480.0
        rs = _style()
        no_layers = BarChart(id="t", type="bar", aspect_ratio=2.0)
        with_layers = BarChart(
            id="t", type="bar", aspect_ratio=2.0, layers=[_one_line_layer()]
        )
        h_no = get_chart_content_height(no_layers, width=width, resolved_style=rs)
        h_with = get_chart_content_height(with_layers, width=width, resolved_style=rs)
        assert h_no == h_with
        # sanity: aspect_ratio actually drove the value
        assert h_with == pytest.approx(width / 2.0)

    def test_line_with_layers_uses_chart_aspect_ratio(self) -> None:
        width = 480.0
        rs = _style()
        no_layers = LineChart(id="t", type="line", aspect_ratio=1.5)
        with_layers = LineChart(
            id="t", type="line", aspect_ratio=1.5, layers=[_one_line_layer()]
        )
        assert get_chart_content_height(
            no_layers, width=width, resolved_style=rs
        ) == get_chart_content_height(with_layers, width=width, resolved_style=rs)

    def test_area_with_layers_uses_chart_aspect_ratio(self) -> None:
        width = 640.0
        rs = _style()
        no_layers = AreaChart(id="t", type="area", aspect_ratio=3.0)
        with_layers = AreaChart(
            id="t", type="area", aspect_ratio=3.0, layers=[_one_line_layer()]
        )
        assert get_chart_content_height(
            no_layers, width=width, resolved_style=rs
        ) == get_chart_content_height(with_layers, width=width, resolved_style=rs)


# ── max_height clamping ───────────────────────────────────────────────────────


class TestMaxHeightWithLayers:
    """chart.max_height must clamp the aspect-ratio-derived height with layers too."""

    def test_bar_with_layers_is_clamped_by_max_height(self) -> None:
        """max_height clamps a wide chart: same height with or without layers.

        Theme min_height=150, so we use max_height=200 (above theme floor).
        aspect_ratio=2.0, width=1000 → raw h=500 → clamped to max_height=200.
        """
        width = 1000.0
        rs = _style()
        no_layers = BarChart(id="t", type="bar", aspect_ratio=2.0, max_height=200.0)
        with_layers = BarChart(
            id="t",
            type="bar",
            aspect_ratio=2.0,
            max_height=200.0,
            layers=[_one_line_layer()],
        )
        h_no = get_chart_content_height(no_layers, width=width, resolved_style=rs)
        h_with = get_chart_content_height(with_layers, width=width, resolved_style=rs)
        assert h_no == h_with
        assert h_with == pytest.approx(200.0)

    def test_line_with_layers_is_clamped_by_max_height(self) -> None:
        width = 1200.0
        rs = _style()
        no_layers = LineChart(id="t", type="line", aspect_ratio=1.0, max_height=150.0)
        with_layers = LineChart(
            id="t",
            type="line",
            aspect_ratio=1.0,
            max_height=150.0,
            layers=[_one_line_layer()],
        )
        assert get_chart_content_height(
            no_layers, width=width, resolved_style=rs
        ) == get_chart_content_height(with_layers, width=width, resolved_style=rs)


# ── explicit height ───────────────────────────────────────────────────────────


class TestExplicitHeightWithLayers:
    """chart.height overrides aspect_ratio regardless of whether layers is set."""

    def test_bar_with_layers_uses_explicit_height(self) -> None:
        rs = _style()
        no_layers = BarChart(id="t", type="bar", height=500)
        with_layers = BarChart(
            id="t", type="bar", height=500, layers=[_one_line_layer()]
        )
        h_no = get_chart_content_height(no_layers, width=800.0, resolved_style=rs)
        h_with = get_chart_content_height(with_layers, width=800.0, resolved_style=rs)
        assert h_no == h_with
        assert h_with == pytest.approx(500.0)

    def test_line_with_layers_uses_explicit_height(self) -> None:
        rs = _style()
        no_layers = LineChart(id="t", type="line", height=350)
        with_layers = LineChart(
            id="t", type="line", height=350, layers=[_one_line_layer()]
        )
        assert get_chart_content_height(
            no_layers, width=800.0, resolved_style=rs
        ) == get_chart_content_height(with_layers, width=800.0, resolved_style=rs)


# ── default (theme) path ──────────────────────────────────────────────────────


class TestDefaultHeightWithLayers:
    """No chart-level overrides: theme aspect_ratio drives height the same way."""

    def test_bar_default_height_unchanged_by_layers(self) -> None:
        """Adding layers: to a plain bar chart must not change its default height."""
        rs = _style()
        width = 480.0
        no_layers = BarChart(id="t", type="bar")
        with_layers = BarChart(id="t", type="bar", layers=[_one_line_layer()])
        assert get_chart_content_height(
            no_layers, width=width, resolved_style=rs
        ) == get_chart_content_height(with_layers, width=width, resolved_style=rs)

    def test_line_default_height_unchanged_by_layers(self) -> None:
        rs = _style()
        width = 480.0
        no_layers = LineChart(id="t", type="line")
        with_layers = LineChart(id="t", type="line", layers=[_one_line_layer()])
        assert get_chart_content_height(
            no_layers, width=width, resolved_style=rs
        ) == get_chart_content_height(with_layers, width=width, resolved_style=rs)

    def test_area_default_height_unchanged_by_layers(self) -> None:
        rs = _style()
        width = 480.0
        no_layers = AreaChart(id="t", type="area")
        with_layers = AreaChart(id="t", type="area", layers=[_one_line_layer()])
        assert get_chart_content_height(
            no_layers, width=width, resolved_style=rs
        ) == get_chart_content_height(with_layers, width=width, resolved_style=rs)
