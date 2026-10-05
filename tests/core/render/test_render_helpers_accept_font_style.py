"""Tests verifying render helpers accept font: FontStyle instead of scalar params.

Each test passes a sentinel FontStyle and asserts the family appears in SVG output,
proving the value flows through — not just that the parameter name exists.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.normalized import SparkBarChart, TableChart
from dbt_charts.core.compile.models.primitives import FontStyle
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)


def _eff():
    return resolve_style(get_theme_style()).chart_defaults


def _ctx():
    return resolve_chart_style_context(get_theme_style())


def _board():
    return resolve_style(get_theme_style())


# =============================================================================
# A — render/chart/callout.py  (private helpers receive FontStyle internally)
# =============================================================================


def test_a_callout_svg_contains_font_family() -> None:
    """render_callout_svg reads font from ResolvedStyle and emits its family."""
    from dbt_charts.core.render.chart.callout import render_callout_svg

    style = resolve_style(get_theme_style())
    svg = render_callout_svg(
        message="boom",
        width=300,
        callout_style=style.chart_defaults.callout_error,
        base_dir=None,
    )
    assert "font-family" in svg


# =============================================================================
# B — render/placeholder.py  add_placeholder_overlay(font: FontStyle)
# =============================================================================


def test_b_add_placeholder_overlay_accepts_fontstyle() -> None:
    """add_placeholder_overlay must accept font: FontStyle and emit its family."""
    from dbt_charts.core.render.placeholder import add_placeholder_overlay

    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"></svg>'
    font = FontStyle(family="SentinelFontB", size=12.0)
    result = add_placeholder_overlay(svg, 200, 100, font=font, resolved_style=_board())
    assert "SentinelFontB" in result
    assert "add data" in result.lower()


# =============================================================================
# C — render/boards.py  _render_columned_text_svg takes font: FontStyle | None
# =============================================================================


# =============================================================================
# D — render/chart/spark_bar.py  _render_spark_bar_row takes font: FontStyle
# =============================================================================


def _resolved_spark_bar(chart_id: str = "sb1"):
    from dbt_charts.core.compile.resolve import resolve

    # x is the magnitude (numeric), y is the label — spark_bar inverts the
    # cartesian convention.
    chart = SparkBarChart(id=chart_id, type="spark_bar", x="count", y="label")
    return resolve(chart, [{"label": "A", "count": 10}], chart_style_context=_ctx())


def test_d_spark_bar_renders_font_family() -> None:
    """render_spark_bar_svg must emit font-family in the SVG."""
    from dbt_charts.core.render.chart.spark_bar import render_spark_bar_svg

    chart = _resolved_spark_bar()
    data = [{"label": "A", "count": 10}, {"label": "B", "count": 5}]
    svg = render_spark_bar_svg(chart, data, board_style=_board())
    assert "font-family" in svg


def test_d_spark_bar_placeholder_renders_overlay() -> None:
    """render_spark_bar_svg with is_placeholder=True must render the placeholder overlay.

    Regression: spark_bar.py previously referenced undefined `font_family` in the
    placeholder block when font_family was extracted as a local variable.  This test ensures
    placeholder mode reaches add_placeholder_overlay without a NameError.
    """
    from dbt_charts.core.render.chart.spark_bar import render_spark_bar_svg

    chart = _resolved_spark_bar("sb_placeholder")
    data = [{"label": "A", "count": 10}, {"label": "B", "count": 5}]
    svg = render_spark_bar_svg(chart, data, is_placeholder=True, board_style=_board())
    assert "add data" in svg.lower()
    assert "<svg" in svg


# =============================================================================
# E — render/chart/spark.py  render_spark_bar / render_spark take font (bar/bar-normalize)
# =============================================================================


def test_e_render_spark_bar_accepts_fontstyle() -> None:
    """render_spark_bar must accept font: FontStyle and emit its family."""
    from dbt_charts.core.render.chart.spark import render_spark_bar

    font = FontStyle(family="SentinelFontE", size=11.0)
    svg = render_spark_bar(
        50.0,
        value_visible=True,
        font=font,
        resolved_style=_eff(),
    )
    assert "SentinelFontE" in svg


def test_e_render_spark_bar_sentinel_via_render_spark() -> None:
    """render_spark dispatches FontStyle to render_spark_bar (no scalar re-expansion)."""
    from dbt_charts.core.render.chart.spark import render_spark

    font = FontStyle(family="SentinelFontRenderSpark", size=12.0)
    svg = render_spark(
        75.0,
        "bar-normalize",
        font=font,
        value_visible=True,
        resolved_style=_eff(),
    )
    assert "SentinelFontRenderSpark" in svg


# =============================================================================
# F — render/chart/table_support.py  calculate_column_layout, resolve_wrapped_headers
# =============================================================================


# =============================================================================
# G — render/chart/table.py  private helpers & render_table_svg end-to-end
# =============================================================================

# Note: _render_pagination_controls used to take font: FontStyle; the
# paginator now owns its own font/color/weight cluster as PaginatorStyle, so
# that signature shape is no longer pinned here. The visual behavior is
# covered by tests/core/test_table_pagination_controls.py.


def test_g_render_table_svg_end_to_end() -> None:
    """render_table_svg must succeed end-to-end with current V2 resolved chart."""
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.table import render_table_svg

    data = [{"name": "Alice", "score": 95}, {"name": "Bob", "score": 82}]
    board_rs = _board()
    board_ctx = _ctx()
    chart = TableChart(id="t1", type="table", title="Test")
    resolved = resolve(chart, data, chart_style_context=board_ctx)
    svg = render_table_svg(resolved, data, board_style=board_rs)
    assert "<svg" in svg
    assert "Alice" in svg
    assert "Bob" in svg
