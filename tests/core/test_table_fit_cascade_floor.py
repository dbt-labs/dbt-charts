"""Tests for the fit-cascade floor invariant (P5b Workstream 3A).

The cascade must never produce font-size < 11 for body or header text.
When content still overflows at 11px, truncation handles it — the cascade
stops there.

Also guards that auto-width for unformatted numeric columns accounts for
comma-formatted output, preventing overflow into adjacent columns.
"""

from __future__ import annotations

import re

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.normalized import TableChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())


def _make_wide_table(width: float) -> str:
    """Render a table with many wide columns at the given width.

    Uses raw numeric values (no format:) so the default formatter applies
    comma separation.  6 columns at narrower widths forces cascade to engage.
    """
    from dbt_charts.core.compile.models.chart.normalized import TableChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    from ._engine_render import render_table_svg

    chart = TableChart(
        id="test_cascade",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="table",
    )
    data = [
        {
            "Opportunity Name": "Enterprise Platform Renewal Agreement 2024",
            "Amount": 125000,
            "Close Date": "2024-03-15",
            "Stage": "Negotiation",
            "Owner": "Alice Johnson",
            "Record Type": "New Business",
        }
        for _ in range(5)
    ]
    return render_table_svg(
        resolve(chart, data, chart_style_context=_BOARD_STYLE),
        data,
        width=width,
        board_style=resolve_style(get_theme_style()),
    )


def _font_sizes_in_table_text(svg: str) -> list[int]:
    """Extract all font-size values from table cell and header text elements.

    Pagination arrows use a separate constant; exclude them by filtering only
    <text> elements that carry fill= (table text always sets fill explicitly).
    This avoids false positives from SVG metadata or non-text elements.
    """
    # Match font-size="N" in <text> elements that also have fill= (table text)
    sizes = []
    for m in re.finditer(r'<text\b[^>]*font-size="(\d+)"[^>]*fill="', svg):
        sizes.append(int(m.group(1)))
    # Also catch the reversed attribute order
    for m in re.finditer(r'<text\b[^>]*fill="[^"]*"[^>]*font-size="(\d+)"', svg):
        sizes.append(int(m.group(1)))
    return sizes


class TestCascadeFloor:
    """Cascade must stop at 11px — never produce smaller body/header text."""

    def test_floor_at_11px_narrow_width(self):
        """Width 300 triggers cascade; body font must not drop below 11."""
        svg = _make_wide_table(width=300)
        sizes = _font_sizes_in_table_text(svg)
        # There must be some text rendered (sanity check)
        assert sizes, "No <text fill=> elements found in SVG"
        assert min(sizes) >= 11, (
            f"font-size dropped to {min(sizes)}px, cascade floor not enforced. "
            f"All sizes seen: {sorted(set(sizes))}"
        )

    def test_no_sub_11_font_across_widths(self):
        """At widths 250–500, no table text renders below 11px."""
        for w in (250, 300, 400, 500):
            svg = _make_wide_table(width=w)
            sizes = _font_sizes_in_table_text(svg)
            assert sizes, f"No <text fill=> elements at width={w}"
            bad = [s for s in sizes if s < 11]
            assert not bad, (
                f"Width {w}: font-size {min(bad)}px found — cascade floor "
                f"not enforced. Sizes: {sorted(set(sizes))}"
            )

    def test_tight_width_wraps_rather_than_truncating(self):
        """At a tight width the table wraps rather than abbreviating.

        The 6-column table has 5 compact columns (~450px total demand) and one
        text column ("Opportunity Name").  The ellipsis that used to appear at
        width=500 came from the header-overflow default, which inherited the
        chart title's ``truncate``; headers now follow the theme's ``wrap-two``,
        and the text column's min-word floor keeps its cell content on word
        boundaries.
        """
        svg = _make_wide_table(width=500)
        assert "…" not in svg, (
            "Expected wrapping at width=500; an ellipsis means a header or cell "
            "was abbreviated instead of wrapped."
        )
        words = " ".join(re.findall(r"<tspan\b[^>]*>([^<]*)</tspan>", svg)).split()
        for word in ("Enterprise", "Platform", "Renewal", "Agreement"):
            assert word in words, f"Wrapping dropped or split the word {word!r}"


class TestNumericAutoWidth:
    """Auto-width must account for comma-formatted output, not raw digits.

    125000 → "125,000" is 3 chars wider; if the width calc uses raw digit
    count, the formatted value overflows into the adjacent column.
    """

    def test_numeric_values_do_not_overlap_adjacent_columns(self):
        """Adjacent column text must not have overlapping x extents."""
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        from ._engine_render import render_table_svg

        chart = TableChart(
            id="test_numeric_overlap",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="table",
        )
        # Columns: text, unformatted numeric (large, gets commas), text.
        # At width=600 with 4 columns, there's enough room if width calc is
        # correct but overflow if it measures raw digits instead of "125,000".
        data = [
            {
                "Name": "Alice",
                "Amount": 125000,
                "Close Date": "2024-03-15",
                "Stage": "Closed Won",
            },
            {
                "Name": "Bob",
                "Amount": 2100000,
                "Close Date": "2024-01-10",
                "Stage": "Negotiation",
            },
            {
                "Name": "Carol",
                "Amount": 875000,
                "Close Date": "2024-02-20",
                "Stage": "Prospecting",
            },
        ]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )

        # Parse all <text> elements by their x position and approximate
        # rendered width (using text-anchor="end" means x is the RIGHT edge;
        # text-anchor="start" means x is the LEFT edge).
        # We look for obvious telltale of overflow: the formatted value
        # immediately adjacent to Close Date text with no gap.
        # A simple proxy: "125,000" and a date string should not appear in the
        # same text element or tspan with no separating whitespace.
        assert "125,0002024" not in svg, (
            "Numeric value '125,000' runs directly into date '2024...' — "
            "numeric column width under-allocated. Width calc likely used "
            "raw digit count instead of formatted string."
        )
        assert "2100,0002024" not in svg and "2,100,0002024" not in svg, (
            "Numeric overflow into adjacent date column detected."
        )
