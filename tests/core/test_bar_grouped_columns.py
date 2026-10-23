"""Tests for grouped column chart default behavior.

When a bar chart has a color field and stack is not authored (None), the
renderer defaults to side-by-side grouped columns via xOffset (vertical) or
yOffset (horizontal) instead of VL's stacked default.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
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

from ._engine_render import render_chart_svg
from .conftest import chart_pane

_RESOLVED_STYLE, _BOARD_STYLE = resolve_style_and_context(get_theme_style())


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


class TestGroupedBarOverlap:
    """style.bar.overlap controls within-group bar spacing.

    Gap/touch (fraction ≤ 0) and vertical overlap emit a band-relative bar width;
    horizontal overlap emits a ``bandwidth('yOffset')`` size expression (Vega
    clamps height band > 1, so the band shorthand can't overlap horizontal
    bars — the expression form isn't clamped and needs no panel dimensions).
    Positive overlap is restricted to exactly 2 series.
    """

    def _spec(self, data, *, width=None, height=None, **kw):
        return render_chart_svg(
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
        with pytest.raises(ChartDataError, match="exactly 2 series \\(got 4\\)") as exc:
            render_chart_svg(chart, data)
        assert exc.value.code.code == "ERR-BAR-OVERLAP-SERIES-COUNT"

    @pytest.mark.parametrize("overlap", ["full", 1.0, 1.5])
    def test_full_overlap_over_a_non_unique_color_raises(self, overlap):
        """A full overlap would stack the series end to end past the axis."""
        with pytest.raises(ChartDataError) as err:
            self._spec(MULTI_SERIES_DATA, overlap=overlap)
        assert err.value.code.code == "ERR-BAR-OVERLAP-FULL-NON-UNIQUE-COLOR"
        assert "'region'" in str(err.value)

    def test_full_overlap_over_wide_measures_names_the_measures(self):
        chart = BarChart(
            id="test_bar",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="bar",
            x="category",
            y=["revenue", "cost"],
            stack="none",
            style=BarChartStylePatch(orientation="vertical", overlap="full"),
        )
        data = [
            {"category": "Alpha", "revenue": 10, "cost": 4},
            {"category": "Beta", "revenue": 12, "cost": 6},
        ]
        with pytest.raises(ChartDataError, match="revenue, cost"):
            render_chart_svg(chart, data)

    def test_three_series_positive_overlap_raises(self):
        with pytest.raises(ChartDataError, match="2 series") as exc:
            render_chart_svg(
                _chart(overlap="partial"),
                THREE_SERIES_DATA,
            )
        assert exc.value.code.code == "ERR-BAR-OVERLAP-SERIES-COUNT"


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
        with pytest.raises(ChartDataError, match="continuous x-axis") as exc:
            render_chart_svg(chart, NUMERIC_X_DATA)
        assert exc.value.code.code == "ERR-BAR-OVERLAP-CONTINUOUS-AXIS"

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
