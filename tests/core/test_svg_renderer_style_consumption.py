"""Tests for table and KPI renderers consuming effective chart style.

Proves:
1. Table renderer reads colors from chart.style.table (ResolvedTableStyle).
2. KPI renderer reads styling from chart.style.kpi (ResolvedKpiStyle).
"""

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
from dbt_charts.core.render.chart.kpi import render_kpi_svg
from dbt_charts.core.render.chart.table import render_table_svg


def _rs():
    return resolve_style(get_theme_style())


def _ctx():
    return resolve_chart_style_context(get_theme_style())


def _table_chart(table_style: TableChartStyle | None = None):
    """Build a minimal V2 ResolvedTableChart, optionally with a style override."""
    from dbt_charts.core.compile.models.chart.normalized import TableChart
    from dbt_charts.core.compile.resolve import resolve as _resolve

    chart = TableChart.model_validate({"id": "test", "type": "table"})
    resolved = _resolve(chart, [], chart_style_context=_ctx())
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


def _kpi_chart(kpi_style=None):
    """Build a minimal V2 ResolvedKpiChart, optionally with a kpi-style override."""
    from dbt_charts.core.compile.models.chart.normalized import KpiChart
    from dbt_charts.core.compile.resolve import resolve as _resolve

    chart = KpiChart.model_validate(
        {
            "id": "test_kpi",
            "type": "kpi",
            "query": {"query_type": "sql", "sql": "SELECT 1", "source": "test"},
            "query_name": "q",
            "value": "revenue",
            "label": "Revenue",
        }
    )
    resolved = _resolve(chart, [{"revenue": 42000}], chart_style_context=_ctx())
    if kpi_style is not None:
        return resolved.model_copy(
            update={"style": resolved.style.model_copy(update={"kpi": kpi_style})}
        )
    return resolved


def _make_kpi_style(value_font_size=None, value_font_weight=None):
    """Build a KpiChartStyle override."""
    base = _ctx().kpi
    v = base.value
    value_updates: dict = {}
    if value_font_size is not None:
        value_updates["font"] = v.font.model_copy(
            update={"size": float(value_font_size)}
        )
    if value_font_weight is not None:
        font = value_updates.get("font", v.font)
        value_updates["font"] = font.model_copy(update={"weight": value_font_weight})
    if value_updates:
        return base.model_copy(update={"value": v.model_copy(update=value_updates)})
    return base


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
            _table_chart(table_style), data, width=400, board_style=_rs()
        )
        assert "#aa0000" in svg

    def test_table_uses_style_border_color(self):
        from dbt_charts.core.compile.models.primitives import RuleStyle
        from dbt_charts.core.compile.models.style.theme import TableRuleStyle

        table_style = _ctx().table.model_copy(
            update={
                "rule": TableRuleStyle(color="#00bb00"),
                "row": _ctx().table.row.model_copy(
                    update={
                        "rule": RuleStyle(width=1.0, continuous=False, color="#00bb00")
                    }
                ),
            }
        )
        data = [{"col_a": "hello", "col_b": 42}]
        svg = render_table_svg(
            _table_chart(table_style), data, width=400, board_style=_rs()
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
            _table_chart(table_style), data, width=400, board_style=_rs()
        )
        assert "#0000cc" in svg

    def test_table_uses_style_text_color(self):
        from dbt_charts.core.compile.models.primitives import StaticGradientColorStyle

        table_style = _ctx().table.model_copy(
            update={"color": StaticGradientColorStyle(static="#dd00dd")}
        )
        data = [{"a": "hello"}]
        svg = render_table_svg(
            _table_chart(table_style), data, width=400, board_style=_rs()
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
        )
        table_style = base.table.model_copy(
            update={"header": base.table.header.model_copy(update={"font": new_font})}
        )
        data = [{"a": "hello"}]
        svg = render_table_svg(
            _table_chart(table_style), data, width=400, board_style=_rs()
        )
        assert "#ee00ee" in svg

    def test_table_without_style_override_uses_theme_colors(self):
        """Table with default resolved_style renders without error."""
        data = [{"a": "hello"}]
        svg = render_table_svg(_table_chart(), data, width=400, board_style=_rs())
        assert "<svg" in svg

    def test_table_rejects_invalid_color(self):
        """Invalid authored color values raise InvalidColorError, not silently sanitize."""
        from dbt_charts.core.colors import InvalidColorError

        table_style = _ctx().table.model_copy(
            update={
                "header": _ctx().table.header.model_copy(
                    update={"background": "not-a-color"}
                )
            }
        )
        data = [{"a": "hello"}]
        with pytest.raises(InvalidColorError, match="Invalid color value"):
            render_table_svg(
                _table_chart(table_style),
                data,
                width=400,
                board_style=_rs(),
            )

    def test_table_requires_board_style(self):
        """render_table_svg raises TypeError when board_style is not passed."""
        with pytest.raises(TypeError, match="board_style"):
            render_table_svg(_table_chart(), [{"a": "hello"}], width=400)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# KPI renderer
# ---------------------------------------------------------------------------


class TestKpiRendererStyleConsumption:
    """render_kpi_svg reads styling from chart.style.kpi; board_style is a kwarg."""

    def test_kpi_uses_style_value_font_size(self):
        kpi_style = _make_kpi_style(value_font_size=72.0)
        svg = render_kpi_svg(
            _kpi_chart(kpi_style),
            [{"revenue": 42000}],
            width=300,
            height=200,
            board_style=_rs(),
        )
        assert 'font-size="72.0"' in svg

    def test_kpi_uses_style_value_font_weight(self):
        kpi_style = _make_kpi_style(value_font_weight="600")
        svg = render_kpi_svg(
            _kpi_chart(kpi_style),
            [{"revenue": 42000}],
            width=300,
            height=200,
            board_style=_rs(),
        )
        assert 'font-weight="600"' in svg

    def test_kpi_label_font_size_follows_theme_when_set(self):
        """When charts.kpi.label.font.size is set on the resolved style,
        the rendered label uses that size regardless of card width."""
        kpi_style = _ctx().kpi
        expected = int(kpi_style.label.font.size)
        svg = render_kpi_svg(
            _kpi_chart(),
            [{"revenue": 42000}],
            width=300,
            height=200,
            board_style=_rs(),
        )
        assert f'font-size="{expected}"' in svg or f'font-size="{expected}.0"' in svg, (
            f"Expected KPI label at theme size {expected}, got {svg[:300]}"
        )

    def test_kpi_rejects_invalid_font_weight(self):
        """An invalid weight string falls back to the resolved theme default."""
        from dbt_charts.core.compile.models.style.theme import font_weight_as_css

        kpi_style = _make_kpi_style(value_font_weight="evil-injection")
        svg = render_kpi_svg(
            _kpi_chart(kpi_style),
            [{"revenue": 42000}],
            width=300,
            height=200,
            board_style=_rs(),
        )
        assert "evil-injection" not in svg
        default_weight = font_weight_as_css(
            resolve_chart_style_context(get_theme_style()).kpi.value.font.weight
        )
        assert f'font-weight="{default_weight}"' in svg

    def test_kpi_without_style_override_uses_theme_defaults(self):
        """KPI with default resolved_style emits the resolved theme weight."""
        from dbt_charts.core.compile.models.style.theme import font_weight_as_css

        svg = render_kpi_svg(
            _kpi_chart(),
            [{"revenue": 42000}],
            width=300,
            height=200,
            board_style=_rs(),
        )
        assert "<svg" in svg
        default_weight = font_weight_as_css(
            resolve_chart_style_context(get_theme_style()).kpi.value.font.weight
        )
        assert f'font-weight="{default_weight}"' in svg

    def test_kpi_requires_board_style(self):
        """render_kpi_svg raises TypeError when board_style is not passed."""
        with pytest.raises(TypeError, match="board_style"):
            render_kpi_svg(_kpi_chart(), [{"revenue": 42000}])  # type: ignore[call-arg]
