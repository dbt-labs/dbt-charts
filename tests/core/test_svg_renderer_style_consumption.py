"""Tests for the table renderer consuming effective chart style: it reads colors
from chart.style.table (ResolvedTableStyle)."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.primitives import FontStyle
from dbt_charts.core.compile.models.style.resolved import ResolvedTableStyle
from dbt_charts.core.compile.models.style.theme import TableChartStyle
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

from ._engine_render import render_table_svg


def _rs():
    return resolve_style(get_theme_style())


def _ctx():
    return resolve_chart_style_context(get_theme_style())


def _table_chart(
    table_style: TableChartStyle | None = None, data: list[dict] | None = None
):
    """Build a minimal V2 ResolvedTableChart, optionally with a style override."""
    from dbt_charts.core.compile.models.chart.normalized import TableChart
    from dbt_charts.core.compile.resolve import resolve as _resolve

    chart = TableChart.model_validate({"id": "test", "type": "table"})
    resolved = _resolve(chart, data or [], chart_style_context=_ctx())
    if table_style is not None:
        return resolved.model_copy(
            update={
                "style": ResolvedTableStyle(
                    table=table_style,
                    title=resolved.style.title,
                    formats=resolved.style.formats,
                    title_font=resolved.style.title_font,
                    pagination=resolved.style.pagination,
                )
            }
        )
    return resolved


# ---------------------------------------------------------------------------
# Table renderer
# ---------------------------------------------------------------------------


class TestTableRendererStyleConsumption:
    """render_table_svg reads colors from chart.style.table; board_style is a kwarg."""

    def test_table_uses_style_header_background(self):
        table_style = _ctx().table.model_copy(
            update={
                "header": _ctx().table.header.model_copy(
                    update={"background": "#aa0000"}
                )
            }
        )
        data = [{"col_a": "hello", "col_b": 42}]
        svg = render_table_svg(
            _table_chart(table_style, data), data, width=400, board_style=_rs()
        )
        assert "#aa0000" in svg

    def test_table_uses_style_divider_color(self):
        row = _ctx().table.row
        table_style = _ctx().table.model_copy(
            update={
                "row": row.model_copy(
                    update={
                        "divider": row.divider.model_copy(
                            update={"visible": True, "width": 1.0, "color": "#00bb00"}
                        )
                    }
                ),
            }
        )
        data = [{"col_a": "hello", "col_b": 42}, {"col_a": "world", "col_b": 7}]
        svg = render_table_svg(
            _table_chart(table_style, data), data, width=400, board_style=_rs()
        )
        assert "#00bb00" in svg

    def test_table_uses_style_stripe_color(self):
        from dbt_charts.core.compile.models.style.theme import TableRowStripeStyle

        table_style = _ctx().table.model_copy(
            update={
                "row": _ctx().table.row.model_copy(
                    update={"stripe": TableRowStripeStyle(color="#0000cc")}
                )
            }
        )
        data = [{"a": "x", "b": 1}, {"a": "y", "b": 2}]
        svg = render_table_svg(
            _table_chart(table_style, data), data, width=400, board_style=_rs()
        )
        assert "#0000cc" in svg

    def test_table_uses_style_text_color(self):
        from dbt_charts.core.compile.models.primitives import ColumnFontStyle
        from dbt_charts.core.compile.models.style.authored.table import (
            TableColumnDefaultsConfig,
        )

        table_style = _ctx().table.model_copy(
            update={
                "column_defaults": TableColumnDefaultsConfig(
                    font=ColumnFontStyle(color="#dd00dd")
                )
            }
        )
        data = [{"a": "hello"}]
        svg = render_table_svg(
            _table_chart(table_style, data), data, width=400, board_style=_rs()
        )
        assert "#dd00dd" in svg

    def test_table_uses_style_header_color(self):
        base = _ctx()
        base_font = base.table.header.font
        new_font = FontStyle(
            family=base_font.family,
            color="#ee00ee",
            size=base_font.size,
            weight=base_font.weight,
            case=base_font.case,
        )
        table_style = base.table.model_copy(
            update={"header": base.table.header.model_copy(update={"font": new_font})}
        )
        data = [{"a": "hello"}]
        svg = render_table_svg(
            _table_chart(table_style, data), data, width=400, board_style=_rs()
        )
        assert "#ee00ee" in svg

    def test_table_without_style_override_uses_theme_colors(self):
        """Table with default resolved_style renders without error."""
        data = [{"a": "hello"}]
        svg = render_table_svg(
            _table_chart(None, data), data, width=400, board_style=_rs()
        )
        assert "<svg" in svg

    def test_table_rejects_invalid_color(self):
        """Invalid authored color values raise ERR-CSS-COLOR-INVALID-AT-RENDER, not silently sanitize."""
        from dbt_charts.core.diagnostics.chart_data import ChartDataError

        table_style = _ctx().table.model_copy(
            update={
                "header": _ctx().table.header.model_copy(
                    update={"background": "not-a-color"}
                )
            }
        )
        data = [{"a": "hello"}]
        with pytest.raises(ChartDataError, match="not a valid CSS color"):
            render_table_svg(
                _table_chart(table_style, data),
                data,
                width=400,
                board_style=_rs(),
            )

    def test_table_requires_board_style(self):
        """render_table_svg raises TypeError when board_style is not passed."""
        with pytest.raises(TypeError, match="board_style"):
            render_table_svg(_table_chart(), [{"a": "hello"}], width=400)  # type: ignore[call-arg]
