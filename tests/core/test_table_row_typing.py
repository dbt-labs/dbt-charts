"""Tests for P3 table row typing (value/summary/total roles)."""

import dataclasses
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

_BOARD_STYLE = resolve_style(get_theme_style())


class TestResolveRowRole:
    """Unit tests for the resolve_row_role helper."""

    def test_none_spec_returns_value(self):
        from dbt_charts.core.render.chart.table_support import resolve_row_role

        assert resolve_row_role(None, {"a": 1}) == "value"

    def test_column_ref_returns_role(self):
        from dbt_charts.core.render.chart.table_support import resolve_row_role

        assert resolve_row_role("row_role", {"row_role": "total"}) == "total"
        assert resolve_row_role("row_role", {"row_role": "summary"}) == "summary"
        assert resolve_row_role("row_role", {"row_role": "value"}) == "value"

    def test_case_insensitive(self):
        from dbt_charts.core.render.chart.table_support import resolve_row_role

        assert resolve_row_role("row_role", {"row_role": "TOTAL"}) == "total"
        assert resolve_row_role("row_role", {"row_role": "Summary"}) == "summary"

    def test_empty_value_returns_value(self):
        from dbt_charts.core.render.chart.table_support import resolve_row_role

        assert resolve_row_role("row_role", {"row_role": ""}) == "value"

    def test_null_value_returns_value(self):
        from dbt_charts.core.render.chart.table_support import resolve_row_role

        assert resolve_row_role("row_role", {"row_role": None}) == "value"

    def test_unknown_value_falls_back_to_value(self):
        from dbt_charts.core.render.chart.table_support import resolve_row_role

        assert resolve_row_role("row_role", {"row_role": "magic"}) == "value"

    def test_missing_column_falls_back_to_literal(self):
        from dbt_charts.core.render.chart.table_support import resolve_row_role

        # "nonexistent" is not a column, so spec is treated as literal.
        # The literal "nonexistent" is not a valid role, so returns "value".
        assert resolve_row_role("nonexistent", {"a": 1}) == "value"

    def test_literal_role_value(self):
        from dbt_charts.core.render.chart.table_support import resolve_row_role

        # When spec is literal "total" and no column named "total" exists,
        # every row is interpreted as having role "total".
        assert resolve_row_role("total", {"a": 1}) == "total"

    def test_is_summary_role(self):
        from dbt_charts.core.render.chart.table_support import is_summary_role

        assert is_summary_role("summary") is True
        assert is_summary_role("total") is True
        assert is_summary_role("value") is False
        assert is_summary_role("") is False

    def test_is_total_role(self):
        from dbt_charts.core.render.chart.table_support import is_total_role

        assert is_total_role("total") is True
        assert is_total_role("summary") is False
        assert is_total_role("value") is False


class TestRowTypingRendering:
    """Integration tests for summary/total row styling in render_table_svg."""

    def _data_with_total(self):
        return [
            {"company": "Apex", "revenue": 1000, "row_role": "value"},
            {"company": "Bright", "revenue": 2000, "row_role": "value"},
            {"company": "Total", "revenue": 3000, "row_role": "total"},
        ]

    def _data_with_summary(self):
        return [
            {"company": "Apex", "revenue": 1000, "row_role": "value"},
            {"company": "Bright", "revenue": 2000, "row_role": "value"},
            {"company": "Average", "revenue": 1500, "row_role": "summary"},
        ]

    def _style_with_row_rule(self, row_role="row_role"):

        es = resolve_chart_style_context(get_theme_style())
        tc = es.table
        new_row = tc.row.model_copy(
            update={
                "rule": tc.row.rule.model_copy(update={"width": 0.5}),
                "role": row_role,
                "roles": tc.row.roles.model_copy(
                    update={
                        "summary": tc.row.roles.summary.model_copy(
                            update={"rule_width": 0.5}
                        )
                    }
                ),
            }
        )
        new_header = tc.header.model_copy(
            update={"rule": tc.header.rule.model_copy(update={"width": 0})}
        )
        return dataclasses.replace(
            es, table=tc.model_copy(update={"row": new_row, "header": new_header})
        )

    def test_total_row_renders_double_rule(self, make_chart):
        """role=total produces TWO rule rects above the total row.

        Row rules below the last data row and below the total row are
        suppressed: the double-rule above the total is the terminal
        visual separator and drawing extras would stack redundantly.
        """
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        _custom_ctx = self._style_with_row_rule()
        chart = resolve(chart, [], chart_style_context=_custom_ctx)
        svg = render_table_svg(
            chart,
            self._data_with_total(),
            width=600,
            board_style=_BOARD_STYLE,
        )
        rules = re.findall(r'<rect [^>]*shape-rendering="crispEdges"', svg)
        # 1 row rule (after row 0 only; row 1's rule is suppressed because
        # the next row is a total) + 2 double-rule rects above total = 3
        assert len(rules) == 3, (
            f"Expected 3 rules (1 row + 2 double above total), got {len(rules)}"
        )

    def test_summary_row_renders_single_rule(self, make_chart):
        """role=summary produces ONE rule rect above the summary row.

        Row rule below the last data row is suppressed (the summary rule
        above the summary row IS the separator between body and summary).
        """
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        _custom_ctx = self._style_with_row_rule()
        chart = resolve(chart, [], chart_style_context=_custom_ctx)
        svg = render_table_svg(
            chart,
            self._data_with_summary(),
            width=600,
            board_style=_BOARD_STYLE,
        )
        rules = re.findall(r'<rect [^>]*shape-rendering="crispEdges"', svg)
        # 1 row rule (after row 0; row 1's rule is suppressed because the
        # next row is a summary) + 1 summary rule above = 2
        assert len(rules) == 2, (
            f"Expected 2 rules (1 row + 1 above summary), got {len(rules)}"
        )

    def test_summary_row_medium(self, make_chart):
        """Summary rows render with font-weight 500 (medium) by default."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        _custom_ctx = self._style_with_row_rule()
        chart = resolve(chart, [], chart_style_context=_custom_ctx)
        svg = render_table_svg(
            chart,
            self._data_with_total(),
            width=600,
            board_style=_BOARD_STYLE,
        )
        assert 'font-weight="500"' in svg

    def test_no_row_role_config_no_medium(self, make_chart):
        """When row_role is unset, no data rows get medium styling."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        es = resolve_chart_style_context(get_theme_style())
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            self._data_with_total(),
            width=600,
            board_style=_BOARD_STYLE,
        )
        # Header uses 600 by default; data rows should NOT carry font-weight.
        total_texts = re.findall(r'<text[^>]*font-weight="500"[^>]*>[^<]*Total', svg)
        assert len(total_texts) == 0, (
            f"Expected no medium Total cell, got {len(total_texts)}"
        )

    def test_no_row_rule_no_summary_rule(self, make_chart):
        """When row_rule_width=0, no summary rule drawn even for summary rows."""

        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        es = resolve_chart_style_context(get_theme_style())
        tc = es.table
        new_row = tc.row.model_copy(
            update={
                "role": "row_role",
                "roles": tc.row.roles.model_copy(
                    update={
                        "summary": tc.row.roles.summary.model_copy(
                            update={"rule_width": 0}
                        )
                    }
                ),
            }
        )
        new_header = tc.header.model_copy(
            update={"rule": tc.header.rule.model_copy(update={"width": 0})}
        )
        es = dataclasses.replace(
            es, table=tc.model_copy(update={"row": new_row, "header": new_header})
        )
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            self._data_with_total(),
            width=600,
            board_style=_BOARD_STYLE,
        )
        rules = re.findall(r'<rect [^>]*shape-rendering="crispEdges"', svg)
        assert len(rules) == 0, f"Expected 0 rules, got {len(rules)}"
        # Medium still applies (style, not rule-dependent)
        assert 'font-weight="500"' in svg


class TestAnchorsMode:
    """Tests for symbol_mode=anchors behavior."""

    def test_anchors_with_summary_row_shows_symbols(self, make_chart):
        """anchors mode: summary rows also show prefix/suffix symbols."""
        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
        from dbt_charts.core.compile.models.primitives import FormatConfig
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        chart.style = TableChartStylePatch(
            columns={
                "company": TableColumnConfig(),
                "revenue": TableColumnConfig(format=FormatConfig(spec="$,.2s")),
            }
        )

        es = resolve_chart_style_context(get_theme_style())
        tc = es.table
        es = dataclasses.replace(
            es,
            table=tc.model_copy(
                update={
                    "symbol_mode": "anchors",
                    "row": tc.row.model_copy(update={"role": "row_role"}),
                }
            ),
        )
        data = [
            {"company": "Apex", "revenue": 24730000, "row_role": "value"},
            {"company": "Bright", "revenue": 8460000, "row_role": "value"},
            {"company": "Mid", "revenue": 5000000, "row_role": "value"},
            {"company": "Total", "revenue": 37190000, "row_role": "total"},
        ]
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=_BOARD_STYLE,
        )
        # Count $ tspans — should be 2 (first row + total row)
        dollar_count = svg.count(">$<")
        assert dollar_count == 2, f"Expected 2 $ tspans, got {dollar_count}"

    @staticmethod
    def _anchors_svg(make_chart, fmt, revenues, symbol_mode="anchors"):
        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )
        from dbt_charts.core.render.chart.table import render_table_svg

        chart = make_chart("table", x=None, y=None)
        chart.style = TableChartStylePatch(
            columns={
                "company": TableColumnConfig(),
                "revenue": TableColumnConfig(format=fmt),
            }
        )
        es = resolve_chart_style_context(get_theme_style())
        es = dataclasses.replace(
            es, table=es.table.model_copy(update={"symbol_mode": symbol_mode})
        )
        data = [
            {"company": name, "revenue": value}
            for name, value in zip("ABC", revenues, strict=True)
        ]
        chart = resolve(chart, [], chart_style_context=es)
        return render_table_svg(chart, data, width=600, board_style=_BOARD_STYLE)

    @pytest.mark.parametrize("unit", ["EUR", "GBP", "USD", "MXN", "PLN"])
    def test_anchors_shows_an_authored_currency_suffix_once(self, make_chart, unit):
        from dbt_charts.core.compile.models.primitives import FormatConfig

        svg = self._anchors_svg(
            make_chart,
            FormatConfig(spec=",.0f", suffix=f" {unit}"),
            [1500, 2500, 3500],
        )
        assert svg.count(f">{unit}<") == 1

    @pytest.mark.parametrize(
        ("symbol_mode", "repeat", "count"),
        [("anchors", "every", 3), ("all", "anchor", 1)],
    )
    def test_format_repeat_overrides_symbol_mode(
        self, make_chart, symbol_mode, repeat, count
    ):
        from dbt_charts.core.compile.models.primitives import FormatConfig

        svg = self._anchors_svg(
            make_chart,
            FormatConfig(spec=",.0f", prefix="EUR ", repeat=repeat),
            [1500, -2500, 3500],
            symbol_mode,
        )
        assert svg.count(">EUR<") == count
        assert ">−2,500<" in svg

    def test_anchors_keeps_a_magnitude_suffix_on_every_row(self, make_chart):
        from dbt_charts.core.compile.models.primitives import FormatConfig

        svg = self._anchors_svg(
            make_chart, FormatConfig(spec=".3~s"), [1_500_000, 2_500_000, 3_500_000]
        )
        assert svg.count(">M<") == 3


class TestAllSymbolsMode:
    """Tests for symbol_mode=all — prefix and suffix repeat on every row.

    This is not the default (``stark.yml`` sets ``anchors``),
    but is an explicit opt-in for tables where every row should carry
    full formatting (units spelled out on each cell). Guards against the
    mode silently regressing as ``anchors`` is iterated.
    """

    def test_all_mode_shows_prefix_on_every_data_row(self, make_chart):
        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
        from dbt_charts.core.compile.models.primitives import FormatConfig
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        chart.style = TableChartStylePatch(
            columns={
                "company": TableColumnConfig(),
                "revenue": TableColumnConfig(format=FormatConfig(spec="$,.2s")),
            }
        )

        es = resolve_chart_style_context(get_theme_style())
        tc = es.table
        es = dataclasses.replace(es, table=tc.model_copy(update={"symbol_mode": "all"}))
        data = [
            {"company": "Apex", "revenue": 24_730_000},
            {"company": "Bright", "revenue": 8_460_000},
            {"company": "Mid", "revenue": 5_000_000},
            {"company": "Lowly", "revenue": 1_200_000},
        ]
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=_BOARD_STYLE,
        )
        # Every one of the 4 data rows should carry a "$" prefix tspan.
        dollar_count = svg.count(">$<")
        assert dollar_count == 4, (
            f"symbol_mode=all must emit $ on every row, got {dollar_count}"
        )
        # And the "M" suffix (magnitude letter) also every row.
        m_count = svg.count(">M</tspan>")
        assert m_count == 4, (
            f"symbol_mode=all must emit suffix on every row, got {m_count}"
        )

    def test_all_mode_with_summary_row_shows_all_symbols(self, make_chart):
        """With a total/summary row, the 'all' mode keeps symbols on
        every data row AND the total."""
        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
        from dbt_charts.core.compile.models.primitives import FormatConfig
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        chart.style = TableChartStylePatch(
            columns={
                "company": TableColumnConfig(),
                "revenue": TableColumnConfig(format=FormatConfig(spec="$,.2s")),
            }
        )

        es = resolve_chart_style_context(get_theme_style())
        tc = es.table
        es = dataclasses.replace(
            es,
            table=tc.model_copy(
                update={
                    "symbol_mode": "all",
                    "row": tc.row.model_copy(update={"role": "row_role"}),
                }
            ),
        )
        data = [
            {"company": "Apex", "revenue": 24_730_000, "row_role": "value"},
            {"company": "Bright", "revenue": 8_460_000, "row_role": "value"},
            {"company": "Total", "revenue": 33_190_000, "row_role": "total"},
        ]
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=_BOARD_STYLE,
        )
        # 2 data rows + 1 total row = 3 $ prefixes
        dollar_count = svg.count(">$<")
        assert dollar_count == 3, (
            f"symbol_mode=all with total must emit $ on every row "
            f"including total, got {dollar_count}"
        )

    def test_anchors_default_strips_middle_rows(self, make_chart):
        """Control test: verify the default (anchors) strips symbols from
        middle rows — shows only on first data row and summary/total."""
        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
        from dbt_charts.core.compile.models.primitives import FormatConfig
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        chart.style = TableChartStylePatch(
            columns={
                "company": TableColumnConfig(),
                "revenue": TableColumnConfig(format=FormatConfig(spec="$,.2s")),
            }
        )
        # Use the default — do not set symbol_mode explicitly
        es = resolve_chart_style_context(get_theme_style())
        data = [
            {"company": "Apex", "revenue": 24_730_000},
            {"company": "Bright", "revenue": 8_460_000},
            {"company": "Mid", "revenue": 5_000_000},
            {"company": "Lowly", "revenue": 1_200_000},
        ]
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=_BOARD_STYLE,
        )
        # Default "anchors" mode: only first row has $
        dollar_count = svg.count(">$<")
        assert dollar_count == 1, (
            f"Default (anchors) must emit $ only on first row, got {dollar_count}"
        )


class TestScaleDomainExcludesTotalRows:
    """Total/summary rows must not influence the scale gradient domain."""

    def test_total_row_excluded_from_scale_domain(self, make_chart):
        """Detail rows span the full palette; total row's large value does not crush the gradient."""

        from dbt_charts.core.compile.models.chart.authored import (
            ColumnScaleConfig,
            ScaleTargetConfig,
            TableColumnConfig,
        )
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        chart.style = TableChartStylePatch(
            columns={
                "product": TableColumnConfig(),
                "revenue": TableColumnConfig(
                    scale=ColumnScaleConfig(
                        background=ScaleTargetConfig(palette=["#ffffff", "#000000"])
                    ),
                ),
            }
        )
        # Detail values span [10, 40]; total=100 must NOT expand the domain.
        data = [
            {"product": "A", "revenue": 10, "_df_row_role": "value"},
            {"product": "B", "revenue": 20, "_df_row_role": "value"},
            {"product": "C", "revenue": 30, "_df_row_role": "value"},
            {"product": "D", "revenue": 40, "_df_row_role": "value"},
            {"product": "Total", "revenue": 100, "_df_row_role": "total"},
        ]
        es = resolve_chart_style_context(get_theme_style())
        tc = es.table
        es = dataclasses.replace(
            es,
            table=tc.model_copy(
                update={"row": tc.row.model_copy(update={"role": "_df_row_role"})}
            ),
        )

        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=_BOARD_STYLE,
        )

        # With total excluded from domain: domain=[10,40], revenue=40 maps to #000000.
        # Without the fix: domain=[10,100], revenue=40 → ~33% gray, never reaching #000000.
        assert 'fill="#000000"' in svg, (
            "Max detail row must map to palette maximum — "
            "total row must not expand the scale domain"
        )
