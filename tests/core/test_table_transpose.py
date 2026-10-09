"""Tests for style.table.transpose — (label, value) row pivot for N-measure tables.

When transpose=True, render_table_svg pivots a single wide data row into N
(label, value) rows — one per column. Raises ChartDataError when data has
more than one row.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())


class TestTableTransposeStyle:
    """TableChartStylePatch accepts transpose field."""

    def test_transpose_defaults_false(self):
        """transpose defaults to False on TableChartStyle."""
        ctx = resolve_chart_style_context(get_theme_style())
        assert ctx.table.transpose is False

    def test_transpose_accepted_by_patch(self):
        """TableChartStylePatch accepts transpose: True via model_validate."""
        from dbt_charts.core.compile.models.style.authored import ChartStylePatch

        patch = ChartStylePatch.model_validate({"table": {"transpose": True}})
        assert patch.table is not None
        assert patch.table.transpose is True


class TestTableTransposeRender:
    """render_table_svg with transpose=True pivots a single row to N (label, value) rows."""

    def _make_transposed_chart(self, make_chart):
        """Create a table chart with transpose=True in resolved style."""
        chart = make_chart("table", x=None, y=None)
        resolved = resolve(chart, [], chart_style_context=_BOARD_STYLE)
        transposed_tc = resolved.style.table.model_copy(update={"transpose": True})
        return resolved.model_copy(
            update={"style": resolved.style.model_copy(update={"table": transposed_tc})}
        )

    def test_transpose_renders_n_data_rows(self, make_chart):
        """3-column single-row query renders as 3 (label, value) rows.

        The pivoted table always has "Metric" and "Value" column headers —
        these are only present when transpose fires, not in a normal 1-row render.
        """
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = self._make_transposed_chart(make_chart)
        data = [{"col_a": 10, "col_b": 20, "col_c": 30}]
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )
        # Transposed output: header row shows "Metric" / "Value" (not the original
        # column keys). A normal 1-row render would show col_a/col_b/col_c headers.
        assert "Metric" in svg, (
            f"Transposed SVG must have 'Metric' header; got: {svg[:400]}"
        )
        assert "Value" in svg, (
            f"Transposed SVG must have 'Value' header; got: {svg[:400]}"
        )
        # slug_to_text("col_a") → "col a". In the transposed output these appear as
        # __metric__ data-cell values; in a normal render they would be column headers.
        # The "Metric"/"Value" header combo uniquely identifies the transposed shape.
        assert "col a" in svg and "col b" in svg and "col c" in svg, (
            f"Expected slug labels as metric-column data cells, got: {svg[:400]}"
        )

    def test_transpose_renders_label_column_from_column_config(self, make_chart):
        """Columns with style.columns.<col>.label use the label, not the column key."""
        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = self._make_transposed_chart(make_chart)
        # Patch in column configs with labels
        column_configs = {
            "dollars_used": TableColumnConfig(label="Dollars Used"),
            "dollars_balance": TableColumnConfig(label="Dollars Balance"),
        }
        chart = chart.model_copy(update={"columns": column_configs})
        data = [{"dollars_used": 1500, "dollars_balance": 3000}]
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )
        assert "Dollars Used" in svg, "Expected label 'Dollars Used' in SVG"
        assert "Dollars Balance" in svg, "Expected label 'Dollars Balance' in SVG"

    def test_transpose_with_column_defaults_bakes_labels_and_output_style(self):
        """column_defaults.label bakes into each source column's resolved
        config (unaffected by the fixed "Metric"/"Value" header labels since
        those are already explicitly set), so it flows into the transposed
        metric labels. column_defaults' presentation fields (background here)
        reach the two synthetic __metric__/__value__ output columns too —
        render applies column_defaults to them directly, since they are its
        own render-native key space (see `_transpose_data_for_render` /
        `fill_table_column_defaults`)."""
        from dbt_charts.core.compile.models.chart.normalized import TableChart
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        data = [{"a": 1, "b": 2}]
        chart = TableChart(
            id="t",
            type="table",
            style={
                "transpose": True,
                "column_defaults": {"label": "Custom Label", "background": "#ff00ff"},
            },
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            resolved, data, width=400, board_style=resolve_style(get_theme_style())
        )
        assert svg.count("Custom Label") == 2
        assert "#ff00ff" in svg
        # The fixed "Metric"/"Value" headers are already-set source values —
        # column_defaults.label must not override them.
        assert "Metric" in svg and "Value" in svg

    def test_transpose_multirow_raises_chart_data_error(self, make_chart):
        """transpose=True with >1 data rows raises ChartDataError."""
        from dbt_charts.core.diagnostics.chart_data import ChartDataError
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = self._make_transposed_chart(make_chart)
        data = [
            {"col_a": 10, "col_b": 20},
            {"col_a": 11, "col_b": 21},
        ]
        with pytest.raises(ChartDataError, match="transpose"):
            render_table_svg(
                chart,
                data,
                width=400,
                board_style=resolve_style(get_theme_style()),
            )

    def test_transpose_column_count_matches_row_count(self, make_chart):
        """10-column single-row query renders exactly 10 (label, value) rows.

        The pivoted SVG must emit 10 metric-label cells in the __metric__ column.
        We count occurrences of "Dollars Used", "Dollars Balance", etc. as a proxy
        for row count — each slug label appears exactly once in the pivoted output.
        The "Metric"/"Value" headers are also present because transpose fired.
        """
        import re

        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = self._make_transposed_chart(make_chart)
        data = [
            {
                "dollars_used": 1500,
                "dollars_balance": 3000,
                "credits_used": 50,
                "credits_balance": 200,
                "storage_used": 100,
                "storage_balance": 400,
                "compute_hours": 12,
                "active_users": 25,
                "api_calls": 900,
                "subscription_months": 6,
            }
        ]
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )
        # Transposed output: "Metric" / "Value" header labels distinguish it from a
        # normal 1-row render that would show the 10 original column keys as headers.
        assert "Metric" in svg, (
            f"Transposed SVG must have 'Metric' header; got: {svg[:300]}"
        )
        assert "Value" in svg, (
            f"Transposed SVG must have 'Value' header; got: {svg[:300]}"
        )

        # Each original column becomes one metric row. Count slug-label occurrences;
        # each should appear exactly once in the __metric__ column cells.
        # slug_to_text produces lowercase tokens: "dollars used", not "Dollars Used".
        expected_labels = [
            "dollars used",
            "dollars balance",
            "credits used",
            "credits balance",
            "storage used",
            "storage balance",
            "compute hours",
            "active users",
            "API calls",
            "subscription months",
        ]
        for lbl in expected_labels:
            count = len(re.findall(re.escape(lbl), svg))
            assert count == 1, (
                f"Expected label {lbl!r} exactly once in transposed SVG, got {count}"
            )

        # Values use the theme SI default: 1500 -> "1.5\u00a0K", 3000 -> "3\u00a0K".
        assert "1.5\u00a0K" in svg, f"Expected '1.5\u00a0K' in SVG but got: {svg[:300]}"
        assert "3\u00a0K" in svg, f"Expected '3\u00a0K' in SVG but got: {svg[:300]}"
