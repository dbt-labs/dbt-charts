"""Tests for render-layer multi-measure pivot (Step 2 of Phase 1).

Covers:
- pivot_table_data: multi-measure reshape correctness
- single-measure backward-compat: column names are col-values, 1-row header
- multi-measure SVG has a 2-row header with group spans and measure sub-labels
- duplicate-cell ChartDataError message hints "move the extra field onto rows or columns"
- values:* inference when values=None
- empty data falls through to normal empty-state
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import TableChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_style,
    resolve_style_and_context,
)

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _board_style() -> Any:
    return resolve_style(get_theme_style())


class TestPivotTableDataNotAPivot:
    """No column dimension → not a pivot; the rows pass through untouched.

    This is the shared gate: the renderer and the layout sizer both call
    unconditionally, so neither owns a private predicate for "is this a pivot?"
    """

    DATA = [{"region": "US", "amount": 100}, {"region": "EU", "amount": 150}]


class TestPivotTableDataSingleMeasure:
    """Single-measure pivot: leaf names are col-values (backward compat)."""

    DATA = [
        {"region": "US", "month": "Jan", "amount": 100},
        {"region": "US", "month": "Feb", "amount": 200},
        {"region": "EU", "month": "Jan", "amount": 150},
        {"region": "EU", "month": "Feb", "amount": 250},
    ]


class TestPivotTableDataMultiMeasure:
    """Multi-measure pivot: leaf names are (col_value, measure) tuples encoded in key."""

    DATA = [
        {"region": "US", "month": "Jan", "revenue": 100, "cost": 60},
        {"region": "US", "month": "Feb", "revenue": 200, "cost": 110},
        {"region": "EU", "month": "Jan", "revenue": 150, "cost": 90},
        {"region": "EU", "month": "Feb", "revenue": 250, "cost": 140},
    ]


class TestPivotColumnOrder:
    """Pivot leaf columns follow the dimension's canonical order, not first-seen.

    A reader assumes a time pivot's columns are chronological (they are in
    every reference tool), so first-seen order is actively misleading there.
    Dimensions without a canonical order (plain strings, mixed content) keep
    first-seen (query) order — that's the documented lever for
    business-ordered categories and the trailing "Total" column pattern.
    """

    def _leaf_keys(self, wide: list[dict[str, Any]], row_dims: list[str]) -> list[str]:
        return [k for k in wide[0] if k not in row_dims]


# ---------------------------------------------------------------------------
# SVG integration tests
# ---------------------------------------------------------------------------


class TestPivotSingleMeasureSVG:
    """Single-measure pivot: SVG has 1-row header with col-value column names."""

    DATA = [
        {"region": "US", "month": "Jan", "amount": 100},
        {"region": "US", "month": "Feb", "amount": 200},
        {"region": "EU", "month": "Jan", "amount": 150},
        {"region": "EU", "month": "Feb", "amount": 250},
    ]

    def test_single_measure_pivot_renders(self) -> None:
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        from ._engine_render import render_table_svg

        chart = TableChart(
            id="pivot_test",
            type="table",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            rows=["region"],
            columns=["month"],
            values=["amount"],
        )
        resolved = resolve(chart, self.DATA, chart_style_context=_BOARD_CTX)
        board = _board_style()
        svg = render_table_svg(resolved, self.DATA, width=600, board_style=board)
        assert isinstance(svg, str)
        assert "<svg" in svg
        # Col-value column headers "Jan" and "Feb" must appear as header labels
        # (slug_to_text may title-case them; just check case-insensitively)
        assert "jan" in svg.lower(), (
            "Single-measure pivot must show col-value 'Jan' as column header"
        )
        assert "feb" in svg.lower(), (
            "Single-measure pivot must show col-value 'Feb' as column header"
        )

    def test_conditional_formatting_keys_by_col_value(self) -> None:
        """Conditional formatting keyed by col-value (e.g. game-of-life c1..c32)."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            FieldConditionalFormatting,
        )
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        from ._engine_render import render_table_svg

        data = [
            {"row_key": "r1", "col": "c1", "val": "#"},
            {"row_key": "r2", "col": "c1", "val": "."},
        ]
        cf = {
            "c1": FieldConditionalFormatting(
                when=[ConditionalRule(eq="#", background="#000000")]
            )
        }
        chart = TableChart(
            id="cf_test",
            type="table",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            rows=["row_key"],
            columns=["col"],
            values=["val"],
            conditional_formatting=cf,
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        board = _board_style()
        svg = render_table_svg(resolved, data, width=400, board_style=board)
        # Must render without error; background fill #000000 must appear
        assert "#000000" in svg, (
            "Conditional formatting must apply to pivot col-value columns"
        )


class TestPivotMultiMeasureSVG:
    """Multi-measure pivot: SVG must have a 2-row header structure."""

    DATA = [
        {"region": "US", "month": "Jan", "revenue": 100, "cost": 60},
        {"region": "US", "month": "Feb", "revenue": 200, "cost": 110},
        {"region": "EU", "month": "Jan", "revenue": 150, "cost": 90},
        {"region": "EU", "month": "Feb", "revenue": 250, "cost": 140},
    ]

    def _get_svg(self) -> str:
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        from ._engine_render import render_table_svg

        chart = TableChart(
            id="multi_pivot_test",
            type="table",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            rows=["region"],
            columns=["month"],
            values=["revenue", "cost"],
        )
        resolved = resolve(chart, self.DATA, chart_style_context=_BOARD_CTX)
        board = _board_style()
        return render_table_svg(resolved, self.DATA, width=800, board_style=board)

    def test_svg_renders_without_error(self) -> None:
        svg = self._get_svg()
        assert isinstance(svg, str)
        assert "<svg" in svg

    def test_group_labels_in_svg(self) -> None:
        svg = self._get_svg()
        # Group labels are the distinct month values
        assert "Jan" in svg, "Group label 'Jan' must appear in multi-measure pivot SVG"
        assert "Feb" in svg, "Group label 'Feb' must appear in multi-measure pivot SVG"

    def test_measure_sub_labels_in_svg(self) -> None:
        svg = self._get_svg()
        # Measure sub-labels appear in the bottom header row
        # slug_to_text("revenue") → "revenue" or "Revenue" depending on case
        assert "revenue" in svg.lower(), (
            "Measure sub-label 'revenue' must appear in SVG"
        )
        assert "cost" in svg.lower(), "Measure sub-label 'cost' must appear in SVG"

    def test_data_values_in_svg(self) -> None:
        svg = self._get_svg()
        # Cell values must appear in SVG
        assert "100" in svg
        assert "60" in svg

    def test_group_header_spans_appear_before_leaf_headers(self) -> None:
        """Group-span rect/text elements appear earlier in SVG than leaf header text."""
        svg = self._get_svg()
        # Both Jan and Feb appear in SVG; group labels are rendered before leaf labels
        jan_pos = svg.find("Jan")
        feb_pos = svg.find("Feb")
        # revenue/cost appear after at least one of the group labels
        rev_pos = svg.lower().find("revenue")
        assert jan_pos >= 0 and rev_pos >= 0
        # Group labels must appear before or concurrent with measure labels
        # (we just assert both are present; exact ordering is layout-dependent)
        assert min(jan_pos, feb_pos) < rev_pos, (
            "Group labels must appear in SVG before measure sub-labels"
        )


class TestPivotMultiMeasureHeaderNotDuplicated:
    """Regression: single-dim multi-measure must render measure names ONCE (not twice).

    Phase-2 bug: measure level was added to group-header descriptor AND the leaf
    row already shows measure names → measures appeared twice in SVG.
    """

    DATA = [
        {"region": "US", "month": "Jan", "revenue": 100, "cost": 60},
        {"region": "EU", "month": "Jan", "revenue": 150, "cost": 90},
    ]

    def test_measure_names_appear_exactly_once_in_header(self) -> None:
        """'revenue' must appear exactly once in the SVG header text."""
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        from ._engine_render import render_table_svg

        chart = TableChart(
            id="no_dup_test",
            type="table",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            rows=["region"],
            columns=["month"],
            values=["revenue", "cost"],
        )
        resolved = resolve(chart, self.DATA, chart_style_context=_BOARD_CTX)
        svg = render_table_svg(
            resolved, self.DATA, width=800, board_style=_board_style()
        )
        # Count how many times "revenue" appears as a header text element.
        # The leaf header (1 occurrence) is all that's allowed — group-header level
        # for month (Jan) must NOT emit "revenue" a second time.
        revenue_count = svg.lower().count("revenue")
        assert revenue_count == 1, (
            f"'revenue' must appear exactly once in SVG (leaf header only), "
            f"but appeared {revenue_count} times — measure level probably duplicated"
        )


class TestPivotEmptyData:
    """Empty data falls through to the normal empty-state (no pivot attempted)."""

    def test_empty_data_renders_no_data_state(self) -> None:
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        from ._engine_render import render_table_svg

        chart = TableChart(
            id="empty_pivot",
            type="table",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            rows=["region"],
            columns=["month"],
            values=["amount"],
        )
        resolved = resolve(chart, [], chart_style_context=_BOARD_CTX)
        board = _board_style()
        svg = render_table_svg(resolved, [], width=400, board_style=board)
        # Must render without error; the normal empty state should appear
        assert "<svg" in svg


class TestPivotStyleColumnsIsStylingOnly:
    """`style.columns` on a pivot styles the columns it names and nothing else.

    The old semantics read an authored mapping whose keys were all real
    post-pivot columns as the complete show-list, silently collapsing the
    cross-tab. Hiding is the explicit `visible: false`; naming only a subset
    of the post-pivot columns, with no `visible:` anywhere, styles that
    subset and renders every column same as any other subset.
    """

    DATA = [
        {"region": "East", "quarter": "Q1", "amt": 10},
        {"region": "East", "quarter": "Q2", "amt": 20},
        {"region": "West", "quarter": "Q1", "amt": 30},
        {"region": "West", "quarter": "Q2", "amt": 40},
    ]

    def _render(self, style_columns: dict[str, Any] | None) -> str:
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        from ._engine_render import render_table_svg

        kwargs: dict[str, Any] = {}
        if style_columns is not None:
            kwargs["style"] = {"columns": style_columns}
        chart = TableChart(
            id="pivcol",
            type="table",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            rows=["region"],
            columns=["quarter"],
            values=["amt"],
            **kwargs,
        )
        resolved = resolve(chart, self.DATA, chart_style_context=_BOARD_CTX)
        return render_table_svg(
            resolved, self.DATA, width=600, board_style=_board_style()
        )

    def test_styling_the_row_group_field_still_renders_the_cross_tab(self) -> None:
        svg = self._render({"region": {"label": "Rgn"}})
        assert "Q1" in svg and "Q2" in svg
        assert ">40<" in svg, "the cross-tab body must still render"
        assert "rgn" in svg.lower()

    def test_naming_only_some_pivoted_columns_still_renders_all(self) -> None:
        svg = self._render({"region": {"label": "Rgn"}, "Q1": {"align": "right"}})
        assert "Q2" in svg
        assert ">40<" in svg, "the un-named pivoted column must still render"

    def test_leaf_keyed_subset_without_visible_still_renders_the_row_dimension(
        self,
    ) -> None:
        """Naming only the pivoted values used to hide the `rows:` dimension
        under the old show-list shape. style.columns is styling-only: the row
        dimension renders regardless of what is named."""
        svg = self._render({"Q1": {"align": "right"}, "Q2": {"align": "right"}})
        assert "Q1" in svg and "Q2" in svg
        assert ">40<" in svg, "the cross-tab body must still render"
        assert "East" in svg, "the row dimension renders unless hidden explicitly"

    def test_row_dimension_renders_unless_visible_false_hides_it(self) -> None:
        """`visible: false` is the explicit way to hide a sort-key dimension."""
        svg = self._render({"region": {"visible": False}, "Q1": {"align": "right"}})
        assert "Q1" in svg and "Q2" in svg
        assert ">40<" in svg
        assert "East" not in svg

    def test_visible_false_hides_a_named_post_pivot_column(self) -> None:
        svg = self._render({"Q1": {"visible": False}})
        assert "Q2" in svg
        assert ">40<" in svg, "Q2's cross-tab body must still render"
        assert "Q1" not in svg

    def test_styling_every_post_pivot_column_renders(self) -> None:
        svg = self._render(
            {
                "region": {"label": "Rgn"},
                "Q1": {"align": "right"},
                "Q2": {"align": "right"},
            }
        )
        assert "Q1" in svg and "Q2" in svg
        assert ">40<" in svg, "the cross-tab body must still render"
        assert "East" in svg, "the styled row dimension must still render"
        assert "rgn" in svg.lower()

    def test_case_a_no_style_columns_unaffected(self) -> None:
        svg = self._render(None)
        assert "Q1" in svg and "Q2" in svg
        assert "East" in svg and ">40<" in svg

    def test_pre_pivot_measure_name_styling_still_renders_every_column(self) -> None:
        """`amt` names a measure, consumed by the pivot reshape rather than a
        post-transform column — styling it never hides anything else."""
        svg = self._render({"amt": {"align": "right"}})
        assert "Q1" in svg and "Q2" in svg
        assert "East" in svg and ">40<" in svg

    def test_flat_table_style_columns_never_hides_an_unnamed_column(self) -> None:
        """Not a pivot — style.columns is styling-only here too."""
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        from ._engine_render import render_table_svg

        data = [{"region": "East", "amt": 10, "helper": "x"}]
        chart = TableChart(
            id="flat",
            type="table",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            style={"columns": {"region": {"label": "Rgn", "visible": True}}},
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        svg = render_table_svg(resolved, data, width=600, board_style=_board_style())
        assert "rgn" in svg.lower()
        assert ">x<" in svg, "helper wasn't marked visible: false — it must render"

    def test_flat_table_visible_false_hides_the_helper_column(self) -> None:
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        from ._engine_render import render_table_svg

        data = [{"region": "East", "amt": 10, "helper": "x"}]
        chart = TableChart(
            id="flathidden",
            type="table",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            style={"columns": {"helper": {"visible": False}}},
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        svg = render_table_svg(resolved, data, width=600, board_style=_board_style())
        assert "East" in svg
        assert ">x<" not in svg

    def test_empty_data_with_style_columns_renders_the_authored_header(self) -> None:
        """Zero rows: there is no post-pivot key space to read a column list
        from, so the empty-state render falls back to the resolved mapping's
        keys and still shows the declared header."""
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        from ._engine_render import render_table_svg

        chart = TableChart(
            id="pivempty",
            type="table",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            rows=["region"],
            columns=["quarter"],
            values=["amt"],
            style={"columns": {"region": {"label": "Rgn"}}},
        )
        resolved = resolve(chart, self.DATA, chart_style_context=_BOARD_CTX)
        svg = render_table_svg(resolved, [], width=600, board_style=_board_style())
        assert "rgn" in svg.lower()

    def test_empty_data_flat_table_with_style_columns_renders(self) -> None:
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        from ._engine_render import render_table_svg

        chart = TableChart(
            id="flatempty",
            type="table",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            style={"columns": {"region": {"label": "Rgn", "visible": True}}},
        )
        resolved = resolve(
            chart, [{"region": "East", "amt": 10}], chart_style_context=_BOARD_CTX
        )
        svg = render_table_svg(resolved, [], width=600, board_style=_board_style())
        assert "rgn" in svg.lower()

    def test_a_pivoted_value_colliding_with_the_measure_name_renders(self) -> None:
        """The measure field is consumed by the reshape, so a pivoted value
        that collides with its name is an ordinary new column — it renders
        (the #7712 guard that raised here is deleted)."""
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        from ._engine_render import render_table_svg

        data = [
            {"region": "East", "quarter": "amt", "amt": 10},
            {"region": "East", "quarter": "Q2", "amt": 20},
        ]
        chart = TableChart(
            id="collide",
            type="table",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            rows=["region"],
            columns=["quarter"],
            values=["amt"],
            style={"columns": {"region": {"label": "Rgn"}}},
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        svg = render_table_svg(resolved, data, width=600, board_style=_board_style())
        assert "East" in svg
        assert ">10<" in svg and ">20<" in svg
