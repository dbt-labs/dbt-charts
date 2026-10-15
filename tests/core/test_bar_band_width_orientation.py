"""TDD tests for orientation-aware bar band_width emission.

bar.band_width (default 0.8) must hit the bar dimension's band:
- vertical bar:  mark.width  == {"band": v}  (x categorical band)
- horizontal bar: mark.height == {"band": v}  (y categorical band)

Bar specs go through the layered path; the bar mark lives at layer[0]["mark"].
Tests written before the fix so horizontal assertions fail first.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_RESOLVED_STYLE, _BOARD_STYLE = resolve_style_and_context(get_theme_style())

DATA = [
    {"category": "A", "value": 10},
    {"category": "B", "value": 20},
]


def _render(
    make_chart,
    orientation: str = "vertical",
    band_width: float | None = None,
    max_size: float | None = None,
):
    """Render a bar chart with optional orientation and band_width override.

    ``orientation`` and ``band_width`` both live inside ``ChartStylePatch``.
    Passing ``orientation`` as a top-level kwarg to Chart is silently
    ignored; it must go through ``style.orientation``.
    """
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    bar_kwargs: dict = {"orientation": orientation}
    bar_marks = {
        key: value
        for key, value in (("band_width", band_width), ("max_size", max_size))
        if value is not None
    }
    if bar_marks:
        # band_width lives at bar.marks.bar.band_width (ADR-015 marks namespace)
        bar_kwargs["marks"] = {"bar": bar_marks}

    chart = make_chart(
        "bar",
        x="category",
        y="value",
        style=(BarChartStylePatch.model_validate(bar_kwargs) if bar_kwargs else None),
    )
    resolved = resolve(chart, DATA, chart_style_context=_BOARD_STYLE)
    return render_resolved_chart(resolved, DATA, _RESOLVED_STYLE).payload


def _bar_mark(spec: dict) -> dict:
    """Extract the bar mark dict from a (possibly layered) spec."""
    # Bar charts use the layered path; layer[0] is the bar mark layer.
    if "layer" in spec:
        return spec["layer"][0].get("mark", {})
    return spec.get("mark", {})


class TestVerticalBarBandWidth:
    """Vertical (column) bar: band_width → mark.width == {"band": v}."""

    def test_vertical_bar_band_width_emits_mark_width_band(self, make_chart):
        """Vertical bar with band_width override → mark.width = {"band": override}."""
        spec = _render(make_chart, band_width=0.7)
        mark = _bar_mark(spec)
        assert mark.get("width") == {"band": 0.7}, (
            f"Expected mark.width == {{'band': 0.7}}, got: {mark}"
        )
        # Must NOT emit mark.height as band fraction for vertical
        height = mark.get("height")
        assert not (isinstance(height, dict) and "band" in height), (
            f"Vertical bar must not emit mark.height band fraction, got: {mark}"
        )

    def test_vertical_bar_default_band_width_is_applied(self, make_chart):
        """Vertical bar with no override gets a band fraction from the theme."""
        spec = _render(make_chart)
        mark = _bar_mark(spec)
        width = mark.get("width")
        assert isinstance(width, dict) and "band" in width, (
            f"Expected mark.width to be a band fraction, got: {mark}"
        )


class TestHorizontalBarBandWidth:
    """Horizontal bar: band_width → mark.height == {"band": v}."""

    def test_horizontal_bar_band_width_emits_mark_height_band(self, make_chart):
        """Horizontal bar with band_width override → mark.height = {"band": override}."""
        spec = _render(make_chart, orientation="horizontal", band_width=0.7)
        mark = _bar_mark(spec)
        assert mark.get("height") == {"band": 0.7}, (
            f"Expected mark.height == {{'band': 0.7}}, got: {mark}"
        )
        # Must NOT emit mark.width as band fraction for horizontal
        width = mark.get("width")
        assert not (isinstance(width, dict) and "band" in width), (
            f"Horizontal bar must not emit mark.width band fraction, got: {mark}"
        )

    def test_horizontal_bar_default_band_width_is_applied(self, make_chart):
        """Horizontal bar with no override gets a band fraction from the theme."""
        spec = _render(make_chart, orientation="horizontal")
        mark = _bar_mark(spec)
        height = mark.get("height")
        assert isinstance(height, dict) and "band" in height, (
            f"Expected mark.height to be a band fraction, got: {mark}"
        )


class TestBandWidthExplicitOverride:
    """Explicit band_width override threads through in both orientations."""

    def test_explicit_band_width_vertical(self, make_chart):
        spec = _render(make_chart, band_width=0.5)
        assert _bar_mark(spec).get("width") == {"band": 0.5}

    def test_explicit_band_width_horizontal(self, make_chart):
        spec = _render(make_chart, orientation="horizontal", band_width=0.5)
        assert _bar_mark(spec).get("height") == {"band": 0.5}


class TestBandMaxSizeCap:
    """marks.bar.max_size caps a band-scale bar's thickness (never thicker)."""

    def test_vertical_band_width_is_clamped_to_max_size(self, make_chart):
        mark = _bar_mark(_render(make_chart, band_width=0.7, max_size=24))
        assert mark.get("width") == {"expr": "min(0.7 * bandwidth('x'), 24.0)"}

    def test_horizontal_band_height_is_clamped_to_max_size(self, make_chart):
        mark = _bar_mark(
            _render(make_chart, orientation="horizontal", band_width=0.7, max_size=24)
        )
        assert mark.get("height") == {"expr": "min(0.7 * bandwidth('y'), 24.0)"}

    def test_unauthored_max_size_leaves_band_fraction_uncapped(self, make_chart):
        assert _bar_mark(_render(make_chart)).get("width") == {"band": 0.8}


class TestAllThemesBandWidthSmoke:
    """Every production theme renders bar + horizontal bar without crashing."""

    def test_all_themes_vertical_bar_no_crash(self, make_chart, compiled_themes):

        chart = make_chart("bar", x="category", y="value")
        resolved = resolve(chart, DATA, chart_style_context=_BOARD_STYLE)
        for name in compiled_themes:
            spec = render_resolved_chart(resolved, DATA, _RESOLVED_STYLE).payload
            assert _bar_mark(spec), f"theme {name!r}: empty bar mark"

    def test_all_themes_horizontal_bar_no_crash(self, make_chart, compiled_themes):
        from dbt_charts.core.compile.models.style.authored import (
            BarChartStylePatch,
        )

        chart = make_chart(
            "bar",
            x="category",
            y="value",
            style=BarChartStylePatch(orientation="horizontal"),
        )
        resolved = resolve(chart, DATA, chart_style_context=_BOARD_STYLE)
        for name in compiled_themes:
            spec = render_resolved_chart(resolved, DATA, _RESOLVED_STYLE).payload
            assert _bar_mark(spec), f"theme {name!r}: empty bar mark"
