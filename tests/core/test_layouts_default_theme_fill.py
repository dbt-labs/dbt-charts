"""Regression: details layouts must never emit empty fill="" under
the universal default theme.

When the universal default table scaffold removed header_background and
row_stripe_color from the default theme (they're now opt-in via bi.yaml
or per-chart overrides), those style fields return empty strings. Callers
that write the value straight into an SVG `fill="..."` attribute ended up
emitting `fill=""`, which browsers treat as invalid and fall back to the
initial value (black) for presentation attributes.

layouts.py::render_details_summary consumes header_background and
row_stripe for collapsible section summaries. These tests guard that they emit a concrete color
(falling back to the page background when the theme doesn't define the
header/stripe fills).
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.board.normalized import LayoutItem
from dbt_charts.core.compile.resolve.style.board import resolve_style


def _default_resolved_style():
    return resolve_style(get_theme_style())


class TestDetailsSummaryNoEmptyFill:
    """render_details_summary must not emit fill=""."""

    def test_collapsed_summary_fill_is_concrete(self):
        from dbt_charts.core.render.layouts import render_details_summary

        rs = _default_resolved_style()
        item = LayoutItem(
            type="chart",
            width=400.0,
            height=200.0,
            details_variable="section_open",
            details_summary="Click to expand",
            details_expanded_summary="Click to collapse",
        )
        svg = render_details_summary(
            item,
            variables={},
            available_width=400.0,
            expanded=False,
            resolved_style=rs,
        )
        assert 'fill=""' not in svg

    def test_expanded_summary_fill_is_concrete(self):
        from dbt_charts.core.render.layouts import render_details_summary

        rs = _default_resolved_style()
        item = LayoutItem(
            type="chart",
            width=400.0,
            height=200.0,
            details_variable="section_open",
            details_summary="Click to expand",
            details_expanded_summary="Click to collapse",
        )
        svg = render_details_summary(
            item,
            variables={},
            available_width=400.0,
            expanded=True,
            resolved_style=rs,
        )
        assert 'fill=""' not in svg
