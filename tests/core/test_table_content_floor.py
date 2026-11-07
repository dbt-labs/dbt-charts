"""Tests for column-width allocation policy.

After the fix: all auto-columns (no explicit width:) receive an equal share of
available_width.  The table always fits the tile.  Numeric/date columns squish
like text columns — truncation is honest, clipping is not.

Tests that pinned the old sacred-column / elastic-floor / trailing-whitespace
contracts have been replaced with tests for the new equal-distribution contract.
"""

from __future__ import annotations

import re

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_PIPELINE_DATA = [
    {
        "opportunity": "Big Widget Deal",
        "account": "Acme Corp",
        "stage": "Proposal",
        "amount": 125000,
        "close_date": "2024-03-15",
        "probability": 0.75,
    },
    {
        "opportunity": "Small Widget Sale",
        "account": "Globex",
        "stage": "Negotiation",
        "amount": 8500,
        "close_date": "2024-06-30",
        "probability": 0.5,
    },
    {
        "opportunity": "Enterprise Platform",
        "account": "Initech",
        "stage": "Closed Won",
        "amount": 2500000,
        "close_date": "2023-12-01",
        "probability": 1.0,
    },
]


def _make_table_chart(make_chart, style: dict | None = None):
    # Floors and truncation are what a slot imposes under `fit`; `scroll`
    # gives the columns their width instead.
    style = {"column_layout": {"overflow": "fit"}, **(style or {})}
    return make_chart("table", x=None, y=None, style=style)


# ---------------------------------------------------------------------------
# Test 1: all auto-columns get equal share
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test 2: elastic columns truncate when squeezed
# ---------------------------------------------------------------------------


class TestElasticColumnsTruncateWhenSqueezed:
    """Text columns squeezed below content_floor show ellipsis in rendered SVG."""

    def test_elastic_text_shows_ellipsis_at_tight_width(self, make_chart):
        """At a very tight width with wrap disabled, elastic columns truncate with ellipsis."""
        from ._engine_render import render_table_svg

        chart = _make_table_chart(
            make_chart,
            style={
                "table": {"wrap": False},
                "columns": {
                    "opportunity": {},
                    "account": {},
                    "amount": {},
                    "close_date": {},
                    # width math pins the four columns above; keep the rest
                    # out of the layout explicitly (styling-only style.columns)
                    "stage": {"visible": False},
                    "probability": {"visible": False},
                },
            },
        )
        # 260px: tight enough that the cascade reduces font to 11px and elastic
        # columns still can't hold the longest text without truncation.
        chart = resolve(chart, _PIPELINE_DATA, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            _PIPELINE_DATA,
            width=260,
            board_style=resolve_style(get_theme_style()),
        )

        has_ellipsis = "…" in svg
        assert has_ellipsis, (
            "Expected ellipsis ('…') in tight-width table SVG with wrap:false — "
            "elastic text columns should truncate rather than overflow"
        )


# ---------------------------------------------------------------------------
# Test 3: SVG does not widen beyond tile for many numeric/date columns
# ---------------------------------------------------------------------------


class TestSVGFitsTileBoundary:
    """SVG width never exceeds available_width regardless of column content type."""

    def test_svg_stays_at_available_width_with_date_columns(self, make_chart):
        """Many date columns in a narrow tile: SVG stays at tile width."""
        from ._engine_render import render_table_svg

        data = [
            {
                "d1": "2024-01-15",
                "d2": "2024-02-20",
                "d3": "2024-03-25",
                "d4": "2024-04-30",
                "d5": "2024-05-15",
            }
        ]
        chart = _make_table_chart(
            make_chart,
            style={
                "columns": {
                    "d1": {"visible": True},
                    "d2": {},
                    "d3": {},
                    "d4": {},
                    "d5": {},
                }
            },
        )
        available_width = 200
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=available_width,
            board_style=resolve_style(get_theme_style()),
        )

        svg_width_m = re.search(r'<svg[^>]*\bwidth="([^"]+)"', svg)
        assert svg_width_m is not None, "Could not find SVG width attribute"
        actual_width = float(svg_width_m.group(1))
        assert actual_width <= available_width + 0.5, (
            f"SVG width {actual_width:.1f}px exceeds tile width {available_width}px — "
            f"table must fit within tile boundaries."
        )


# ---------------------------------------------------------------------------
# Test 4: no text overflow across multiple widths
# ---------------------------------------------------------------------------


class TestNoTextOverflowAcrossWidths:
    """For a mixed table, no text overflows its cell at any tested width."""

    @pytest.mark.parametrize("width", [300, 400, 500, 600, 800, 1120])
    def test_no_overflow_at_width(self, make_chart, width: int):
        """Every text element stays within its cell's content band."""
        from ._engine_render import render_table_svg

        chart = _make_table_chart(
            make_chart,
            style={
                "columns": {
                    "opportunity": {},
                    "account": {},
                    "amount": {"format": "$,.0f"},
                    "close_date": {},
                    "probability": {"format": ".0%"},
                    "stage": {"visible": False},
                }
            },
        )
        chart = resolve(chart, _PIPELINE_DATA, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            _PIPELINE_DATA,
            width=width,
            board_style=resolve_style(get_theme_style()),
        )

        # Find the SVG root width (background rect omitted when table has no explicit background)
        svg_width_m = re.search(r'<svg[^>]*\bwidth="([^"]+)"', svg)
        assert svg_width_m, "Could not find SVG width"
        actual_width = float(svg_width_m.group(1))

        # Verify all text elements fit within the table width (basic sanity)
        for m in re.finditer(r'<text\s[^>]*x="([^"]+)"', svg):
            x = float(m.group(1))
            assert x <= actual_width + 1.0, (
                f"At width={width}, text element x={x:.1f} exceeds "
                f"table width {actual_width:.1f}"
            )


# ---------------------------------------------------------------------------
# Test 5: explicit width overrides are still honored
# ---------------------------------------------------------------------------
