"""Small render files (layouts) consume ResolvedStyle.

Verifies that the layout render functions accept resolved_style and use it
instead of calling get_theme_style().*.
"""

from __future__ import annotations

import dataclasses

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.resolve.style.board import resolve_style


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _default_resolved_style():
    compiled = get_theme_style("clarity")
    return resolve_style(compiled)


# ==========================================================================
# layouts.py — render_tabs_layout uses resolved_style.layout.tabs
# ==========================================================================


class TestRenderTabsLayout:
    def test_runs_with_resolved_style(self):
        """Smoke: render_tabs_layout uses resolved_style.layout.tabs, not the global get_config()."""
        from dbt_charts.core.compile.models.board.resolved import ResolvedLayoutItem
        from dbt_charts.core.render.layouts import render_tabs_layout

        resolved = _default_resolved_style()
        item = ResolvedLayoutItem(
            type="chart",
            chart=None,
            board=None,
            x=0.0,
            y=0.0,
            width=400.0,
            height=200.0,
        )

        svg, height = render_tabs_layout(
            items=(item,),
            executor=None,
            variables={},
            available_width=400.0,
            available_height=300.0,
            tab_titles=["Tab 1"],
            tab_slugs=["tab-1"],
            tab_variable=None,
            active_tab=0,
            tab_position="top",
            background=None,
            resolved_style=resolved,
            render_cache={},
            painted_canvas=None,
        )
        assert isinstance(svg, str)

    def test_tab_bar_height_from_resolved_style(self):
        """render_tabs_layout must use resolved_style.layout.tabs.bar_height."""
        from dbt_charts.core.compile.models.board.resolved import ResolvedLayoutItem
        from dbt_charts.core.render.layouts import render_tabs_layout

        rs = _default_resolved_style()
        custom_bar_height = 999.0
        rs = dataclasses.replace(
            rs,
            layout=rs.layout.model_copy(
                update={
                    "tabs": rs.layout.tabs.model_copy(
                        update={"bar_height": custom_bar_height}
                    )
                }
            ),
        )
        item = ResolvedLayoutItem(
            type="chart",
            chart=None,
            board=None,
            x=0.0,
            y=0.0,
            width=400.0,
            height=100.0,
        )
        _, height = render_tabs_layout(
            items=(item,),
            executor=None,
            variables={},
            available_width=400.0,
            available_height=1100.0,
            tab_titles=["Tab 1"],
            tab_slugs=["tab-1"],
            resolved_style=rs,
            render_cache={},
            painted_canvas=None,
        )
        # Tab bar is 999px; item height is 100px → total = 999 + 100 = 1099
        assert height == pytest.approx(1099.0), (
            f"Expected bar height 999 to affect total, got {height}"
        )


# ==========================================================================
# layouts.py — render_details_summary accepts resolved_style
# ==========================================================================


class TestRenderDetailsSummary:
    def test_runs_with_resolved_style(self):
        from dbt_charts.core.compile.models.board.normalized import LayoutItem
        from dbt_charts.core.render.layouts import render_details_summary

        resolved = _default_resolved_style()
        item = LayoutItem(
            type="chart",
            details_variable="show_details",
            details_summary="Show details",
            details_expanded_summary="Hide details",
        )
        svg = render_details_summary(
            item=item,
            variables={"show_details": "false"},
            available_width=400.0,
            resolved_style=resolved,
        )
        assert isinstance(svg, str)
        assert len(svg) > 0
