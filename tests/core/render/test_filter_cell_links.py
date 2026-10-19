"""Tests for click-to-filter cell links in the /data browser table.

TDD: these tests are written first and drive the implementation.

Phase 2 scope: dimension cells in the /data table-index carry a
``?<column>=<percent-encoded-value>`` link when the column is a declared
board variable and the value is non-null.

Per-column cell-link precedence (highest to lowest):
  1. Explicit per-column ``link:`` from column config
  2. Per-column filter link ``?col=value`` (when col is a declared board variable)

A column with neither is not a cell link. The chart-root link (``links.auto``
detail URL) is separate: the renderer paints it as a whole-row band beneath
the cells, so identity/key columns (e.g. ``id``, ``user_id``) — excluded from
``plan_variables`` because numerics are not default-selected — are part of the
clickable row rather than filter links. Dimension/string columns (e.g.
``property_industry``) ARE in plan_variables so they get their own filter
links, which win the click over the row band.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_BOARD_CTX = resolve_chart_style_context(get_theme_style())

# The row-link band is one static class; its theme link color rides as
# the --dbt-link custom property the host's hover rule reads.
_ROW_LINK_CLASS = r"dbt-table-row-link"

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle

# ---------------------------------------------------------------------------
# resolve_filter_cell_link — pure helper
# ---------------------------------------------------------------------------


class TestResolveFilterCellLink:
    """Unit tests for the pure filter-link helper in automatic_link.py."""

    def test_dimension_column_in_filter_vars_returns_link(self) -> None:
        from dbt_charts.core.render.chart.automatic_link import resolve_filter_cell_link

        link = resolve_filter_cell_link(
            "property_industry", "Retail", {"property_industry"}
        )
        assert link == "?property_industry=Retail"

    def test_value_with_spaces_is_percent_encoded(self) -> None:
        from dbt_charts.core.render.chart.automatic_link import resolve_filter_cell_link

        link = resolve_filter_cell_link(
            "property_industry", "Professional Services", {"property_industry"}
        )
        assert link == "?property_industry=Professional+Services"

    def test_value_with_special_chars_is_encoded(self) -> None:
        from dbt_charts.core.render.chart.automatic_link import resolve_filter_cell_link

        link = resolve_filter_cell_link("status", "a&b=c", {"status"})
        # urlencode-safe: & → %26, = → %3D (no raw special chars survive)
        assert link == "?status=a%26b%3Dc"

    def test_column_not_in_filter_vars_returns_none(self) -> None:
        from dbt_charts.core.render.chart.automatic_link import resolve_filter_cell_link

        link = resolve_filter_cell_link("id", 42, {"property_industry"})
        assert link is None

    def test_null_value_returns_none(self) -> None:
        from dbt_charts.core.render.chart.automatic_link import resolve_filter_cell_link

        link = resolve_filter_cell_link(
            "property_industry", None, {"property_industry"}
        )
        assert link is None

    def test_empty_filter_vars_returns_none(self) -> None:
        from dbt_charts.core.render.chart.automatic_link import resolve_filter_cell_link

        link = resolve_filter_cell_link("property_industry", "Retail", frozenset())
        assert link is None

    def test_numeric_value_is_encoded_as_string(self) -> None:
        from dbt_charts.core.render.chart.automatic_link import resolve_filter_cell_link

        # Temporal/boolean variables can also be filtered
        link = resolve_filter_cell_link("year", 2024, {"year"})
        assert link == "?year=2024"


# ---------------------------------------------------------------------------
# SVG table rendering — filter links appear in rendered SVG
# ---------------------------------------------------------------------------


class TestTableFilterCellLinksInSVG:
    """Integration tests: filter links appear in rendered table SVG."""

    def _make_resolved_chart(
        self, make_chart: Any
    ) -> tuple[ResolvedChart, list[dict[str, Any]], ResolvedStyle]:
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.resolve.style.board import resolve_style

        chart = make_chart("table", title="Companies")
        data = [
            {"id": 1, "property_industry": "Retail", "property_city": "New York"},
            {
                "id": 2,
                "property_industry": "Professional Services",
                "property_city": "Austin",
            },
        ]
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        board_style = resolve_style(get_theme_style())
        return resolved, data, board_style

    def test_dimension_cell_has_filter_link(self, make_chart) -> None:
        """A column declared as a board variable gets ?col=value links."""
        from dbt_charts.core.render.chart.automatic_link import (
            set_filter_variables_context,
        )

        from .._engine_render import render_table_svg

        resolved, data, board_style = self._make_resolved_chart(make_chart)
        set_filter_variables_context(frozenset({"property_industry", "property_city"}))
        try:
            svg = render_table_svg(resolved, data, width=600, board_style=board_style)
        finally:
            set_filter_variables_context(frozenset())

        assert "?property_industry=Retail" in svg

    def test_value_with_space_is_percent_encoded_in_svg(self, make_chart) -> None:
        """Multi-word values are percent-encoded so the link is a valid query string."""
        from dbt_charts.core.render.chart.automatic_link import (
            set_filter_variables_context,
        )

        from .._engine_render import render_table_svg

        resolved, data, board_style = self._make_resolved_chart(make_chart)
        set_filter_variables_context(frozenset({"property_industry"}))
        try:
            svg = render_table_svg(resolved, data, width=600, board_style=board_style)
        finally:
            set_filter_variables_context(frozenset())

        # "Professional Services" must be encoded, not raw
        assert "Professional+Services" in svg or "Professional%20Services" in svg
        assert "?property_industry=" in svg

    def test_column_not_in_filter_vars_has_no_filter_link(self, make_chart) -> None:
        """A column absent from filter vars gets no filter link."""
        from dbt_charts.core.render.chart.automatic_link import (
            set_filter_variables_context,
        )

        from .._engine_render import render_table_svg

        resolved, data, board_style = self._make_resolved_chart(make_chart)
        # Only property_industry is a filter var — property_city is not
        set_filter_variables_context(frozenset({"property_industry"}))
        try:
            svg = render_table_svg(resolved, data, width=600, board_style=board_style)
        finally:
            set_filter_variables_context(frozenset())

        assert "?property_city=" not in svg

    def test_no_filter_vars_context_no_filter_links(self, make_chart) -> None:
        """When no filter vars are set (default), no filter links are emitted."""
        from .._engine_render import render_table_svg

        resolved, data, board_style = self._make_resolved_chart(make_chart)
        # No set_filter_variables_context call — default is empty
        svg = render_table_svg(resolved, data, width=600, board_style=board_style)

        assert "?property_industry=" not in svg
        assert "?property_city=" not in svg

    def test_explicit_column_link_takes_priority_over_filter_link(
        self, make_chart
    ) -> None:
        """Per-column link: overrides filter link even when column is in filter vars."""
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.resolve.style.board import resolve_style
        from dbt_charts.core.render.chart.automatic_link import (
            set_filter_variables_context,
        )

        from .._engine_render import render_table_svg

        chart = make_chart(
            "table",
            title="Companies",
            style={
                "table": {
                    "columns": {
                        "property_industry": {
                            "link": "/industry/{{ property_industry }}",
                        }
                    }
                }
            },
        )
        data = [{"id": 1, "property_industry": "Retail"}]
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        board_style = resolve_style(get_theme_style())

        set_filter_variables_context(frozenset({"property_industry"}))
        try:
            svg = render_table_svg(resolved, data, width=600, board_style=board_style)
        finally:
            set_filter_variables_context(frozenset())

        # Explicit link fires
        assert "/industry/Retail" in svg
        # Filter link does NOT fire
        assert "?property_industry=Retail" not in svg

    def test_row_detail_link_renders_as_row_band_with_filter_cell_on_top(
        self, make_chart
    ) -> None:
        """Chart-root detail URL renders as a whole-row band; filter cells win.

        The detail URL is no longer a per-cell link inherited by non-variable
        columns — it is the row band. A filter-variable column keeps its own
        cell link, painted after the band so it wins the click.
        """
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.resolve.style.board import resolve_style
        from dbt_charts.core.render.chart.automatic_link import (
            set_filter_variables_context,
        )

        from .._engine_render import render_table_svg

        chart = make_chart(
            "table",
            title="Companies",
            link="/data/db/hubspot/company/detail/?id={{ id }}",
        )
        data = [{"id": 1, "property_industry": "Retail"}]
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        board_style = resolve_style(get_theme_style())

        # Only property_industry is a filter var; id is not
        set_filter_variables_context(frozenset({"property_industry"}))
        try:
            svg = render_table_svg(resolved, data, width=600, board_style=board_style)
        finally:
            set_filter_variables_context(frozenset())

        # The detail URL is the row band, not a per-cell link on the id column.
        assert '<a href="/data/db/hubspot/company/detail/?id=1" aria-label' in svg
        row_link_match = re.search(rf'class="{_ROW_LINK_CLASS}"', svg)
        assert row_link_match is not None
        # The filter link fires for property_industry (is a filter var) and
        # paints AFTER the band, so a click on it wins over the row link.
        assert "?property_industry=Retail" in svg
        assert row_link_match.start() < svg.index("?property_industry=Retail")
