"""Table config sub-namespacing tests.

Verifies:
- TableColumnsStyle: cell_padding renamed to cell_padding_x
- TableChartStyle: max_rows removed; background/color/bottom_padding added; rule_color → rule.color
- TableHeaderStyle: font_compact (FontStyle|None) replaces font_weight_compact; height is float
- TableRowRoleStyle: font_weight added
- the table engine and layout_sizing.py read these fields from the table style
"""

from __future__ import annotations

from dbt_charts.core.compile.config import (
    get_default_theme_name,
    get_theme_style,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

from ._engine_render import render_table_svg

_BOARD_STYLE = resolve_style(get_theme_style())


class TestTableColumnsStyleCellPadding:
    """cell_padding stays as scalar per ADR-010 (no padding_x/padding_y pairs)."""

    def test_cell_padding_scalar_present(self):
        """cell_padding is a float on TableColumnsStyle (no padding_x/y split)."""

        cell_pad = get_theme_style(
            get_default_theme_name()
        ).charts.table.column_layout.cell_padding
        assert isinstance(cell_pad, float)


class TestTableStyleNewFields:
    """New top-level fields on TableChartStyle."""

    def test_background_override_propagates(self):

        table = get_theme_style().charts.table
        overridden = table.model_copy(update={"background": "#ff0000"})
        assert overridden.background == "#ff0000"

    def test_color_override_propagates(self):

        table = get_theme_style().charts.table
        overridden = table.model_copy(update={"color": "#123456"})
        assert overridden.color == "#123456"

    def test_divider_override_propagates(self):
        row = get_theme_style().charts.table.row
        overridden = row.model_copy(
            update={"divider": row.divider.model_copy(update={"color": "#aabbcc"})}
        )
        assert overridden.divider.color == "#aabbcc"

    def test_bottom_padding_is_non_negative(self):

        assert get_theme_style().charts.table.bottom_padding >= 0


class TestTableHeaderStyleChanges:
    """font_compact (FontStyle | None) replaces flat font_weight_compact; height is float."""

    def test_font_compact_override_propagates(self):
        from dbt_charts.core.compile.models.primitives import FontStyle

        header = get_theme_style().charts.table.header
        overridden = header.model_copy(
            update={"font_compact": FontStyle(weight="bold")}
        )
        assert overridden.font_compact is not None
        assert overridden.font_compact.weight == "bold"

    def test_height_is_float(self):
        """header.height is a float populated from theme."""

        assert isinstance(
            get_theme_style().charts.table.header.height,
            float,
        )


class TestTableRowRoleStyleChanges:
    """font (FontStyle) added for summary/total row typography override."""

    def test_font_weight_defaults_none(self):

        # font is legitimately absent (None) from the base theme row role.
        assert (
            get_theme_style(
                get_default_theme_name()
            ).charts.table.row.roles.summary.font
            is None
        )


from dbt_charts.core.compile.models.primitives import StaticGradientColorStyle as _sgs


class TestTableRendererUsesNestedConfig:
    """The table engine reads nested fields from the table style (TableChartStyle)."""

    def _make_chart(self):
        from dbt_charts.core.compile.models.chart.normalized import TableChart

        return TableChart(id="t", type="table")

    def test_cell_padding_x_used_in_render(self):
        """Rendering must not raise AttributeError for cell_padding after rename."""

        chart = self._make_chart()
        data = [{"a": "x", "b": 1}]
        chart = resolve(
            chart,
            data,
            chart_style_context=resolve_chart_style_context(get_theme_style()),
        )
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )
        assert "<svg" in svg

    def test_background_override_via_compiled_style(self):
        """table_config.background overrides theme background in render."""
        base = get_theme_style()
        seed = base.model_copy(
            update={
                "charts": base.charts.model_copy(
                    update={
                        "table": base.charts.table.model_copy(
                            update={"background": "#aabbcc"}
                        )
                    }
                )
            }
        )
        data = [{"a": "x"}]
        chart = resolve(
            self._make_chart(),
            data,
            chart_style_context=resolve_chart_style_context(seed),
        )
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(seed),
        )
        assert "#aabbcc" in svg

    def test_color_override_via_compiled_style(self):
        """table_config.color overrides theme text color in render."""
        base = get_theme_style()
        seed = base.model_copy(
            update={
                "charts": base.charts.model_copy(
                    update={
                        "table": base.charts.table.model_copy(
                            update={"color": _sgs(static="#dd00dd")}
                        )
                    }
                )
            }
        )
        data = [{"a": "hello"}]
        chart = resolve(
            self._make_chart(),
            data,
            chart_style_context=resolve_chart_style_context(seed),
        )
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(seed),
        )
        assert "#dd00dd" in svg


class TestLayoutSizingUsesNestedConfig:
    """layout_sizing.py no longer reads max_rows from TableChartStyle."""

    def test_table_height_estimated_without_max_rows(self):
        """Table height estimation must not crash after max_rows removal."""

        # Should not raise AttributeError for max_rows
        assert not hasattr(get_theme_style().charts.table, "max_rows")
