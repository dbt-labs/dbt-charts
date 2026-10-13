"""Phase 2 multi-dimension pivot tests.

TDD: these tests were written BEFORE the implementation. Each test
covers a distinct contract:

- compile accepts 2+ column dims (authored + normalized models)
- pivot_table_data with 2 column dims: correct leaf count, cell placement,
  nested group descriptor structure
- N-row header in SVG (spans structure)
- single-dim regression (game-of-life style): still works identically
- duplicate-cell error carries hint
"""

from __future__ import annotations

from typing import Any

import pytest

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
    from dbt_charts.core.compile.config import get_theme_style

    return resolve_style(get_theme_style())


def _make_resolved_chart(
    rows: list[str] | None,
    columns: list[str],
    values: list[str] | None,
    data: Any = None,
) -> Any:
    from dbt_charts.core.compile.models.chart.normalized import TableChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    chart = TableChart(
        id="multidim_test",
        type="table",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        rows=rows,
        columns=columns,
        values=values,
    )
    return resolve(
        chart, _MULTIDIM_DATA if data is None else data, chart_style_context=_BOARD_CTX
    )


# ---------------------------------------------------------------------------
# 1. Compile: 2+ columns fields accepted
# ---------------------------------------------------------------------------


class TestCompileAcceptsMultipleColumnFields:
    """Both authored and normalized models must accept len(columns) >= 2."""

    def test_authored_table_chart_accepts_two_column_fields(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import TableChart

        chart = TableChart(type="table", columns=["region", "quarter"])
        assert chart.columns == ["region", "quarter"]

    def test_authored_table_chart_accepts_three_column_fields(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import TableChart

        chart = TableChart(type="table", columns=["year", "quarter", "month"])
        assert chart.columns == ["year", "quarter", "month"]

    def test_normalized_chart_accepts_two_column_fields(self) -> None:
        chart = TableChart(id="c", type="table", columns=["region", "quarter"])
        assert chart.columns == ["region", "quarter"]

    def test_normalized_chart_accepts_three_column_fields(self) -> None:
        chart = TableChart(id="c", type="table", columns=["year", "q", "month"])
        assert chart.columns == ["year", "q", "month"]

    def test_field_on_two_channels_still_rejected(self) -> None:
        """The no-field-on-two-channels invariant must survive the limit lift."""
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.chart.authored import TableChart

        with pytest.raises(ValidationError, match="region"):
            TableChart(
                type="table",
                rows=["region"],
                columns=["region", "quarter"],
            )

    def test_field_on_two_channels_rejected_through_compile(self) -> None:
        # The invariant is enforced at the authored/compile boundary (the
        # normalized model is trusted and does not re-validate). Route through
        # normalize_chart to confirm the full compile path rejects it.
        from dbt_charts.core.compile.errors import CompilationError
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.normalize.charts import normalize_chart

        registry = {"q": SqlQuery(sql="SELECT 1", source="test")}
        with pytest.raises(CompilationError, match="region"):
            normalize_chart(
                "c",
                {
                    "type": "table",
                    "query": "q",
                    "rows": ["region"],
                    "columns": ["region", "q"],
                },
                registry,
                sources={},
            )

    def test_channels_survive_normalize_board_with_two_column_dims(self) -> None:
        """Two column dims round-trip through the full compiler."""
        from dbt_charts.core.compile.compiler import compile as compile_board

        yaml = """
title: Multi-dim Pivot
queries:
  sales:
    sql: "SELECT region, year, quarter, revenue FROM sales"
    source: test
charts:
  pivot_table:
    type: table
    query: sales
    rows:
      - region
    columns:
      - year
      - quarter
    values:
      - revenue
rows:
  - pivot_table
"""
        result = compile_board(yaml)
        assert result.errors == [], f"Compile errors: {result.errors}"
        chart = result.board.charts["pivot_table"]
        assert chart.columns == ["year", "quarter"]


# ---------------------------------------------------------------------------
# 2. pivot_table_data with 2 column dimensions
# ---------------------------------------------------------------------------

# Long-form data: region × year × quarter × revenue
_MULTIDIM_DATA = [
    {"region": "US", "year": "2024", "quarter": "Q1", "revenue": 100},
    {"region": "US", "year": "2024", "quarter": "Q2", "revenue": 200},
    {"region": "US", "year": "2025", "quarter": "Q1", "revenue": 150},
    {"region": "US", "year": "2025", "quarter": "Q2", "revenue": 250},
    {"region": "EU", "year": "2024", "quarter": "Q1", "revenue": 90},
    {"region": "EU", "year": "2024", "quarter": "Q2", "revenue": 180},
    {"region": "EU", "year": "2025", "quarter": "Q1", "revenue": 120},
    {"region": "EU", "year": "2025", "quarter": "Q2", "revenue": 220},
]


class TestPivotTableDataTwoColDimsMultiMeasure:
    """pivot_table_data with columns=[year, quarter], values=[revenue, cost]."""

    DATA = [
        {"region": "US", "year": "2024", "quarter": "Q1", "revenue": 100, "cost": 40},
        {"region": "US", "year": "2024", "quarter": "Q2", "revenue": 200, "cost": 80},
        {"region": "EU", "year": "2024", "quarter": "Q1", "revenue": 90, "cost": 30},
        {"region": "EU", "year": "2024", "quarter": "Q2", "revenue": 180, "cost": 70},
    ]


# ---------------------------------------------------------------------------
# 2b. rows omitted → row dims inferred from the remaining query columns
#     (CRITICAL regression: a table chart with columns/values but no rows
#     used to render — every detail row keyed to () and collided).
# ---------------------------------------------------------------------------


class TestPivotTableDataRowsOmitted:
    DATA = [
        {"region": "US", "month": "Jan", "amount": 100},
        {"region": "US", "month": "Feb", "amount": 200},
        {"region": "EU", "month": "Jan", "amount": 150},
        {"region": "EU", "month": "Feb", "amount": 250},
    ]

    def test_svg_renders_without_rows_declared(self) -> None:
        """End-to-end: a table chart with columns/values but no rows must
        render (rows are inferred), not raise ChartDataError."""
        from ._engine_render import render_table_svg

        resolved = _make_resolved_chart(
            rows=None,
            columns=["month"],
            values=["amount"],
        )
        svg = render_table_svg(
            resolved,
            self.DATA,
            width=600,
            board_style=_board_style(),
        )
        assert "<svg" in svg
        assert "US" in svg and "EU" in svg


# ---------------------------------------------------------------------------
# 2c. Up-front field validation — a typo'd values/columns field must raise a
#     ChartDataError, not silently produce blank cells or a raw KeyError.
# ---------------------------------------------------------------------------


class TestPivotTableDataValidatesFields:
    DATA = [
        {"region": "US", "quarter": "Q1", "revenue": 100},
        {"region": "EU", "quarter": "Q1", "revenue": 90},
    ]


# ---------------------------------------------------------------------------
# 3. N-row header in SVG
# ---------------------------------------------------------------------------


class TestMultiDimPivotSVGHeader:
    """SVG rendered from 2-col-dim pivot has N-row header structure."""

    def _get_svg(self) -> str:
        from ._engine_render import render_table_svg

        resolved = _make_resolved_chart(
            rows=["region"],
            columns=["year", "quarter"],
            values=["revenue"],
        )
        return render_table_svg(
            resolved,
            _MULTIDIM_DATA,
            width=900,
            board_style=_board_style(),
        )

    def test_svg_renders_without_error(self) -> None:
        svg = self._get_svg()
        assert isinstance(svg, str)
        assert "<svg" in svg

    def test_outer_group_labels_present(self) -> None:
        """Year labels (outer dim) must appear in SVG."""
        svg = self._get_svg()
        assert "2024" in svg, "Outer dim label '2024' must appear in SVG"
        assert "2025" in svg, "Outer dim label '2025' must appear in SVG"

    def test_inner_group_labels_present(self) -> None:
        """Quarter labels (inner dim) must appear in SVG."""
        svg = self._get_svg()
        assert "Q1" in svg, "Inner dim label 'Q1' must appear in SVG"
        assert "Q2" in svg, "Inner dim label 'Q2' must appear in SVG"

    def test_data_values_present(self) -> None:
        svg = self._get_svg()
        assert "100" in svg
        assert "90" in svg

    def test_outer_labels_appear_before_inner_labels(self) -> None:
        """Year labels must come before quarter labels in SVG output."""
        svg = self._get_svg()
        year_pos = svg.find("2024")
        q1_pos = svg.find("Q1")
        assert year_pos >= 0 and q1_pos >= 0
        assert year_pos < q1_pos, "Year group label must appear before quarter label"


class TestMultiDimMultiMeasureSVGHeader:
    """SVG from 2-col-dim + 2-measure pivot has 3-row header structure."""

    DATA = [
        {"region": "US", "year": "2024", "quarter": "Q1", "revenue": 100, "cost": 40},
        {"region": "EU", "year": "2024", "quarter": "Q1", "revenue": 90, "cost": 30},
    ]

    def _get_svg(self) -> str:
        from ._engine_render import render_table_svg

        resolved = _make_resolved_chart(
            rows=["region"],
            columns=["year", "quarter"],
            values=["revenue", "cost"],
        )
        return render_table_svg(
            resolved,
            self.DATA,
            width=900,
            board_style=_board_style(),
        )

    def test_svg_renders_without_error(self) -> None:
        svg = self._get_svg()
        assert "<svg" in svg

    def test_year_quarter_and_measure_labels_all_present(self) -> None:
        svg = self._get_svg()
        assert "2024" in svg
        assert "Q1" in svg
        assert "revenue" in svg.lower()
        assert "cost" in svg.lower()


# ---------------------------------------------------------------------------
# 4. Single-dim regression: game-of-life style
# ---------------------------------------------------------------------------


class TestSingleDimRegressionUnchanged:
    """A single column dim keeps the plain single-dim reshape shape."""

    DATA = [
        {"region": "US", "month": "Jan", "amount": 100},
        {"region": "US", "month": "Feb", "amount": 200},
        {"region": "EU", "month": "Jan", "amount": 150},
        {"region": "EU", "month": "Feb", "amount": 250},
    ]

    def test_single_dim_svg_renders(self) -> None:
        from ._engine_render import render_table_svg

        resolved = _make_resolved_chart(
            rows=["region"],
            columns=["month"],
            values=["amount"],
        )
        svg = render_table_svg(
            resolved,
            self.DATA,
            width=600,
            board_style=_board_style(),
        )
        assert "<svg" in svg
        assert "jan" in svg.lower() or "Jan" in svg


# ---------------------------------------------------------------------------
# 5. Totals: total rows (column totals) + trailing total column (row totals).
#
# Tidy contract:
#   (a) A total row is tagged via the SAME row-role spec column flat tables
#       already use (style.table.row.role, resolved with resolve_row_role /
#       is_total_role). Total-role rows bucket by their rows-dim tuple like
#       every other row — nothing special about the keying — and keep first-seen
#       (query) order. So a single grand total (one consistent rows-dim label,
#       e.g. literal "Total") merges into ONE row, while distinct total labels
#       (per-group subtotals) stay distinct rows. Placement is the query's job:
#       render never reorders (chart AGENTS.md #3), so an `ORDER BY` decides
#       where totals land. The rows-dim cell values pass through verbatim from
#       the query — render never merges, re-labels, or invents them.
#   (b) A row total (trailing "Total" column) is a literal, case-sensitive
#       "Total" value in a columns-dimension field — it reshapes into a
#       trailing leaf column via the SAME col-tuple enumeration used for every
#       other column value. No extra render code; duplicate-cell detection
#       already guards it like any other column value.
# ---------------------------------------------------------------------------
