"""Tests for style.table.row_numbers — leading index column.

Covers:
- Schema: TableRowNumbersStyle (visible, header, align) on TableChartStyle.
- Default rendering: no row_numbers config -> no extra column appears.
- visible=True renders a leading column with 1, 2, 3, ... for N data rows.
- header customizes the column header text (default "#").
- align customizes numeric alignment (default "right").
- Pagination continuity: page 2 shows N+1.. (NEVER resets to 1).
- Column width auto-scales against TOTAL row count (not per-page count).
- Summary/total rows render blank in the row-number cell.
- CF rules indexed by column name never target the row-number column
  (no column: "#" match inside the when_rules dict).
"""

from __future__ import annotations

import re
from collections.abc import Generator
from typing import Any

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.controls import interactive_controls

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())

# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestRowNumbersSchema:
    """TableRowNumbersStyle parses from dict and rejects unknown fields."""

    def test_default_off(self) -> None:
        from dbt_charts.core.compile.models.style.theme import TableRowNumbersStyle

        rn = TableRowNumbersStyle()
        assert rn.visible is False
        assert rn.header == "#"
        assert rn.align == "right"

    def test_explicit_values(self) -> None:
        from dbt_charts.core.compile.models.style.theme import TableRowNumbersStyle

        rn = TableRowNumbersStyle(visible=True, header="Row", align="left")
        assert rn.visible is True
        assert rn.header == "Row"
        assert rn.align == "left"

    def test_align_rejects_invalid_value(self) -> None:
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.theme import TableRowNumbersStyle

        with pytest.raises(ValidationError):
            TableRowNumbersStyle(align="center")  # type: ignore[arg-type]

    def test_unknown_field_rejected(self) -> None:
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.theme import TableRowNumbersStyle

        with pytest.raises(ValidationError):
            TableRowNumbersStyle.model_validate({"bogus": "nope"})

    def test_table_style_nests_row_numbers(self) -> None:

        ts = get_theme_style().charts.table
        assert ts.row_numbers.visible is False
        assert ts.row_numbers.header == "#"
        assert ts.row_numbers.align == "right"

    def test_table_style_patch_accepts_row_numbers(self) -> None:
        """TableStylePatch.row_numbers accepts overrides."""
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch as TableStylePatch,
        )

        ts = TableStylePatch.model_validate(
            {"row_numbers": {"visible": True, "header": "Row"}}
        )
        assert ts.row_numbers is not None
        assert ts.row_numbers.visible is True
        assert ts.row_numbers.header == "Row"


# ---------------------------------------------------------------------------
# Renderer integration helpers
# ---------------------------------------------------------------------------


def _resolve_table(make_chart_fn: Any, data: Any) -> Any:
    """Return a base ResolvedTableChart with default style."""
    chart = make_chart_fn("table", x=None, y=None)
    return resolve(chart, data, chart_style_context=_BOARD_STYLE)


def _modify_table(resolved: Any, **table_updates: Any) -> Any:
    """Return a copy of resolved table chart with style.table fields updated."""
    new_table = resolved.style.table.model_copy(update=table_updates)
    return resolved.model_copy(
        update={"style": resolved.style.model_copy(update={"table": new_table})}
    )


def _with_row_numbers(
    resolved: Any, pagination: Any = None, **row_numbers_kwargs: Any
) -> Any:
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.compile.models.style.authored import PaginationConfig
    from dbt_charts.core.compile.models.style.theme import TableRowNumbersStyle

    if pagination is None:
        pagination = PaginationConfig(enabled=False)

    ink = resolve_style(get_theme_style()).secondary_color
    return _modify_table(
        resolved,
        row_numbers=TableRowNumbersStyle(
            font=FontStyle(color=ink), **row_numbers_kwargs
        ),
        pagination=pagination,
    )


def _row_number_text_values(svg: str) -> list[str]:
    """Return the rendered text of leaf ``<text>..</text>`` nodes.

    Row-number cells render as direct ``<text>N</text>``; numeric data cells
    render their digits inside ``<tspan>`` children. Scanning only ``<text>``
    leaf content captures the row-number column (and text columns) while
    excluding compact-formatted numeric cells, whose SI tokens (e.g. ``2000``→
    ``"2"``) would otherwise collide with row indices.

    Strips out the paginator <g> first — its page-number text elements
    overlap with row-number digits and would poison the scan.
    """
    body = re.sub(r'<g class="dbt-paginator"[^>]*>.*?</g>', "", svg, flags=re.DOTALL)
    texts = re.findall(r"<text[^>]*>([^<]*)</text>", body)
    return [t for t in texts if t]


# ---------------------------------------------------------------------------
# Default off
# ---------------------------------------------------------------------------


class TestDefaultOff:
    def test_default_render_has_no_row_number_column(self, make_chart: Any) -> None:
        """Without row_numbers config, no leading '#' header appears."""
        from .._engine_render import render_table_svg

        data = [
            {"company": "Apex", "revenue": 500},
            {"company": "Bright", "revenue": 600},
            {"company": "Cedar", "revenue": 700},
        ]
        chart = _resolve_table(make_chart, data)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )
        # No '#' header should appear next to the "Company" column.
        assert ">#<" not in svg
        # No standalone cell text of "1", "2", "3" (the synthetic index values).
        rendered = _row_number_text_values(svg)
        assert "1" not in rendered
        assert "2" not in rendered
        assert "3" not in rendered


# ---------------------------------------------------------------------------
# visible=True renders the leading index column
# ---------------------------------------------------------------------------


class TestShowTrue:
    def test_show_renders_indices_one_two_three(self, make_chart: Any) -> None:
        from .._engine_render import render_table_svg

        data = [
            {"company": "Apex", "revenue": 1000},
            {"company": "Bright", "revenue": 2000},
            {"company": "Cedar", "revenue": 3000},
        ]
        chart = _with_row_numbers(_resolve_table(make_chart, data), visible=True)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )
        rendered = _row_number_text_values(svg)
        # Each index appears as a standalone cell text.
        assert "1" in rendered
        assert "2" in rendered
        assert "3" in rendered

    def test_default_header_is_hash(self, make_chart: Any) -> None:
        from .._engine_render import render_table_svg

        data = [{"company": "Apex", "revenue": 1000}]
        chart = _with_row_numbers(_resolve_table(make_chart, data), visible=True)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )
        rendered = _row_number_text_values(svg)
        assert "#" in rendered

    def test_custom_header_text(self, make_chart: Any) -> None:
        from .._engine_render import render_table_svg

        data = [{"company": "Apex", "revenue": 1000}]
        chart = _with_row_numbers(
            _resolve_table(make_chart, data), visible=True, header="Row"
        )
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )
        rendered = _row_number_text_values(svg)
        assert "Row" in rendered
        assert "#" not in rendered

    def test_default_align_is_right(self, make_chart: Any) -> None:
        """Default align=right -> the index cell uses text-anchor 'end'."""
        from .._engine_render import render_table_svg

        data = [{"company": "Apex", "revenue": 1000}]
        chart = _with_row_numbers(_resolve_table(make_chart, data), visible=True)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )
        # Look for the index "1" emitted with text-anchor="end".
        # Pattern: <text ... text-anchor="end" ...>1</text>
        assert re.search(r'<text[^>]*text-anchor="end"[^>]*>1</text>', svg) is not None

    def test_align_left_uses_start_anchor(self, make_chart: Any) -> None:
        """align=left -> text-anchor 'start'."""
        from .._engine_render import render_table_svg

        data = [{"company": "Apex", "revenue": 1000}]
        chart = _with_row_numbers(
            _resolve_table(make_chart, data), visible=True, align="left"
        )
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )
        assert (
            re.search(r'<text[^>]*text-anchor="start"[^>]*>1</text>', svg) is not None
        )


# ---------------------------------------------------------------------------
# Pagination continuity
# ---------------------------------------------------------------------------


class TestPaginationContinuity:
    """Which page's row numbers are visible — the interactive-host contract
    (a single page renders per requested ``variables``). A static export
    instead pre-renders every page into a toggled group; that contract is
    covered by ``TestStaticMultiPagePagination`` in
    test_table_pagination_controls.py.
    """

    @pytest.fixture(autouse=True)
    def _interactive_host(self) -> Generator[None]:
        with interactive_controls(True):
            yield

    def _fifty_row_data(self) -> list[dict[str, Any]]:
        return [{"company": f"C{i:02d}", "revenue": i * 100} for i in range(1, 51)]

    def _page_variables(self, chart_id: str, page: int) -> dict[str, Any]:
        return {f"{chart_id}_page": page}

    def test_page_two_starts_at_eleven(self, make_chart: Any) -> None:
        """50 rows, page_rows=10: page 2 shows 11..20, never 1..10 again."""
        from dbt_charts.core.compile.models.style.authored import PaginationConfig

        from .._engine_render import render_table_svg

        data = self._fifty_row_data()
        chart = _with_row_numbers(
            _resolve_table(make_chart, data),
            pagination=PaginationConfig(enabled=True, page_rows=10),
            visible=True,
        )
        svg = render_table_svg(
            chart,
            data,
            width=600,
            variables=self._page_variables(chart.id, page=2),
            board_style=resolve_style(get_theme_style()),
        )
        rendered = _row_number_text_values(svg)
        # Page 2 indices: 11..20
        for idx in range(11, 21):
            assert str(idx) in rendered, (
                f"expected index {idx} on page 2; got {rendered}"
            )
        # And NOT 1..10 as standalone cells.
        for idx in range(1, 11):
            assert str(idx) not in rendered, (
                f"page 2 wrongly shows index {idx}; got {rendered}"
            )

    def test_page_five_last_page(self, make_chart: Any) -> None:
        """Page 5 of 50/10 shows 41..50."""
        from dbt_charts.core.compile.models.style.authored import PaginationConfig

        from .._engine_render import render_table_svg

        data = self._fifty_row_data()
        chart = _with_row_numbers(
            _resolve_table(make_chart, data),
            pagination=PaginationConfig(enabled=True, page_rows=10),
            visible=True,
        )
        svg = render_table_svg(
            chart,
            data,
            width=600,
            variables=self._page_variables(chart.id, page=5),
            board_style=resolve_style(get_theme_style()),
        )
        rendered = _row_number_text_values(svg)
        for idx in range(41, 51):
            assert str(idx) in rendered, (
                f"expected index {idx} on page 5; got {rendered}"
            )
        for idx in range(1, 41):
            assert str(idx) not in rendered, (
                f"page 5 wrongly shows index {idx}; got {rendered}"
            )

    def test_width_sized_to_total_row_count_not_per_page(self, make_chart: Any) -> None:
        """A 200-row table with page_rows=10 sizes the row-number column
        against 200 (3 digits) not 10 (2 digits). On any page the column
        should be wide enough for "200" not just "10".
        """
        from dbt_charts.core.compile.models.style.authored import PaginationConfig

        from .._engine_render import render_table_svg

        data_big = [{"company": f"C{i:03d}", "revenue": i} for i in range(1, 201)]
        data_small = [{"company": f"C{i:03d}", "revenue": i} for i in range(1, 11)]
        chart = _with_row_numbers(
            _resolve_table(make_chart, data_big),
            pagination=PaginationConfig(enabled=True, page_rows=10),
            visible=True,
        )
        svg_big = render_table_svg(
            chart,
            data_big,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )
        svg_small = render_table_svg(
            chart,
            data_small,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )

        # The x-offset of the SECOND column (company) should be greater in
        # the 200-row table because the row-number column is wider.
        def _company_header_x(svg: str) -> float:
            m = re.search(
                r'<text[^>]*x="([0-9.]+)"[^>]*>(?:<title>[^<]*</title>)?Company</text>',
                svg,
            )
            assert m is not None, f"no Company header found in {svg[:500]}"
            return float(m.group(1))

        x_big = _company_header_x(svg_big)
        x_small = _company_header_x(svg_small)
        assert x_big > x_small, (
            f"Company column x should grow with row count ({x_big=} {x_small=})"
        )


# ---------------------------------------------------------------------------
# Summary / total rows render blank in row-number cell
# ---------------------------------------------------------------------------


class TestSummaryRowsBlank:
    def test_summary_row_shows_blank_row_number(self, make_chart: Any) -> None:
        from .._engine_render import render_table_svg

        data = [
            {"company": "Apex", "revenue": 1000, "kind": "value"},
            {"company": "Bright", "revenue": 2000, "kind": "value"},
            {"company": "Subtotal", "revenue": 3000, "kind": "summary"},
            {"company": "Total", "revenue": 3000, "kind": "total"},
        ]
        from dbt_charts.core.compile.models.primitives import FontStyle
        from dbt_charts.core.compile.models.style.authored import PaginationConfig
        from dbt_charts.core.compile.models.style.theme import TableRowNumbersStyle

        resolved_base = _resolve_table(make_chart, data)
        ink = resolve_style(get_theme_style()).secondary_color
        chart = _modify_table(
            resolved_base,
            row_numbers=TableRowNumbersStyle(visible=True, font=FontStyle(color=ink)),
            row=resolved_base.style.table.row.model_copy(update={"role": "kind"}),
            pagination=PaginationConfig(enabled=False),
        )
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )
        rendered = _row_number_text_values(svg)
        # The two value rows should be numbered 1 and 2.
        assert "1" in rendered
        assert "2" in rendered
        # Summary/total must NOT extend the sequence to 3 or 4.
        assert "3" not in rendered
        assert "4" not in rendered


# ---------------------------------------------------------------------------
# CF rules never target the row-number column
# ---------------------------------------------------------------------------


def _svg_width(svg: str) -> float:
    m = re.search(r'<svg[^>]*\bwidth="([0-9.]+)"', svg)
    assert m is not None, f"no <svg width=...> found in {svg[:200]}"
    return float(m.group(1))


class TestWidthBudgeting:
    """Row-number width must be subtracted from available_width BEFORE
    calculate_column_layout so data columns absorb the cost — not the
    table SVG growing wider and overflowing the dashboard grid cell."""

    def test_svg_width_unchanged_when_row_numbers_toggled_on(
        self, make_chart: Any
    ) -> None:
        """Auto-sized columns at a tight width: adding row_numbers must NOT
        expand the SVG — the row-number width must come out of the
        data-column budget so elastic columns compress to absorb it."""
        from .._engine_render import render_table_svg

        # 5 auto-sized columns in 300px. Without the budget fix the SVG grows
        # when row_numbers.visible=True because the synthetic column is tacked on
        # AFTER the layout pass instead of being deducted from the budget first.
        data = [{"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0, "e": 5.0}] * 5
        FIXED_WIDTH = 300

        chart_off = _resolve_table(make_chart, data)
        svg_off = render_table_svg(
            chart_off,
            data,
            width=FIXED_WIDTH,
            board_style=resolve_style(get_theme_style()),
        )

        chart_on = _with_row_numbers(_resolve_table(make_chart, data), visible=True)
        svg_on = render_table_svg(
            chart_on,
            data,
            width=FIXED_WIDTH,
            board_style=resolve_style(get_theme_style()),
        )

        w_off = _svg_width(svg_off)
        w_on = _svg_width(svg_on)
        assert w_on == w_off, (
            f"Toggling row_numbers.visible grew the SVG width from {w_off} to {w_on}. "
            "Row-number width must be deducted from the data-column budget, "
            "not added on top."
        )


class TestConditionalFormattingIgnoresRowNumbers:
    def test_cf_rule_keyed_by_hash_does_not_target_index_cell(
        self, make_chart: Any
    ) -> None:
        """A CF rule keyed by column name '#' should not match the row-number
        column — the index column is synthetic and has no data source.
        """
        from dbt_charts.core.compile.models.chart.authored import ConditionalRule
        from dbt_charts.core.compile.resolve.style.board import resolve_style

        from .._engine_render import render_table_svg

        data = [{"company": "Apex", "revenue": 1000}]
        chart = _with_row_numbers(_resolve_table(make_chart, data), visible=True)

        # Fabricate a CF block keyed by '#' with a rule that would fill red.
        # The renderer MUST NOT paint the row-number cell red.
        chart = chart.model_copy(
            update={
                "conditional_formatting": {
                    "#": type(
                        "CFEntry",
                        (),
                        {
                            "when": [
                                ConditionalRule(is_null=False, background="#FF0000")
                            ]
                        },
                    )()
                }
            }
        )
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )
        # The red background must not leak into the row-number cell. A simple
        # check: the fabricated CF color should not appear anywhere.
        assert "#FF0000" not in svg
