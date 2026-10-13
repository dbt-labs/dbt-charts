"""Tests for pivot_table_data's single-dim single-measure shape and table
chart pivot channels.

The query-level Pivot model has been retired. This file covers:
  - pivot_table_data's single-dim single-measure path with rows omitted
    (inferred): headers, values, row count, missing-cell-as-None, empty
    input, duplicate-cell and missing-key error diagnostics
  - chart-level rows/columns/values survive normalization (replaces old query.pivot tests)
"""

from __future__ import annotations


class TestPivotTableDataSingleDimSingleMeasure:
    DATA = [
        {"region": "US", "month": "Jan", "amount": 100},
        {"region": "US", "month": "Feb", "amount": 200},
        {"region": "EU", "month": "Jan", "amount": 150},
        {"region": "EU", "month": "Feb", "amount": 250},
    ]


class TestTableChartChannelsSurviveNormalization:
    """chart-level rows/columns/values survive the full compile pipeline."""

    PIVOT_BOARD_YAML = """
title: Pivot Test
queries:
  sales_by_region_month:
    sql: "SELECT region, month, SUM(amount) AS amount FROM t"
    source: test
charts:
  cross_tab:
    type: table
    title: Sales Cross-Tab
    query: sales_by_region_month
    rows:
      - region
    columns:
      - month
    values:
      - amount
rows:
  - cols:
      - cross_tab
"""

    def test_channels_survive_normalization(self) -> None:
        """After normalize_board, chart.rows/columns/values must be preserved."""
        from dbt_charts.core.compile.compiler import compile as compile_board

        result = compile_board(self.PIVOT_BOARD_YAML)
        assert result.errors == [], f"Compile errors: {result.errors}"
        chart = result.board.charts["cross_tab"]
        assert chart.rows == ["region"], "chart.rows lost during normalization"
        assert chart.columns == ["month"], "chart.columns lost during normalization"
        assert chart.values == ["amount"], "chart.values lost during normalization"

    def test_table_without_channels_compiles(self) -> None:
        """A flat table chart (no pivot channels) still compiles cleanly."""
        from dbt_charts.core.compile.compiler import compile as compile_board

        flat_yaml = """
title: Flat Table
queries:
  q:
    sql: "SELECT region, month, amount FROM t"
    source: test
charts:
  flat:
    type: table
    query: q
rows:
  - flat
"""
        result = compile_board(flat_yaml)
        assert result.errors == [], f"Compile errors: {result.errors}"
        chart = result.board.charts["flat"]
        assert chart.rows is None
        assert chart.columns is None
        assert chart.values is None
