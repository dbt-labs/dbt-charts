"""Regression test: HeightProvider must thread resolved_style through nested boards.

Bug: _resolve_height dispatched to HeightProvider without resolved_style.
The provider closure always used render_ctx.resolved_style (outer board's style)
for every chart, even those inside nested boards with a different theme override.
Result: a nested stark-themed chart was rendered/sized with cream fonts and colors
during the sizing pass, and that wrong-style SVG was written to the render cache.

Fix: Add resolved_style to HeightProvider.__call__ and pass it from _resolve_height.
The provider then uses the correct nested board's resolved_style for each chart.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dbt_charts.core.compile.models.board.normalized import Layout, LayoutItem
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedChart as ResolvedChartV2,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.context import ChartStyleContext

_TEST_QUERY = SqlQuery(sql="SELECT 1", source="test")


def _bar_chart(chart_id: str) -> BarChart:
    return BarChart(
        id=chart_id,
        query=_TEST_QUERY,
        query_name="q",
        type="bar",
        x="x",
        y="y",
        style=None,
    )


def _resolved(chart: BarChart, ctx: ChartStyleContext) -> ResolvedChartV2:
    from dbt_charts.core.compile.resolve import resolve

    return resolve(chart, [], chart_style_context=ctx)


def test_height_provider_uses_nested_board_resolved_style() -> None:
    """Charts inside a nested board with a theme override must be sized using
    the nested board's own resolved_style, not the root board's resolved_style.

    Pre-fix: _resolve_height dropped resolved_style when dispatching to
    HeightProvider, so the provider closure always used render_ctx.resolved_style
    (outer cream style) for every chart — even those inside nested stark-themed
    boards.  The wrong-style SVG was written to the render cache and served to the
    final render pass.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
        resolve_style,
    )
    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render import layout_sizing
    from dbt_charts.core.render.layout_sizing import (
        SizingRenderCtx,
        _make_data_aware_height_provider,
    )
    from dbt_charts.core.render.sizing import calculate_layout_height

    outer_rs = resolve_style(get_theme_style("paper"))
    outer_ctx = resolve_chart_style_context(get_theme_style("paper"))
    nested_rs = resolve_style(get_theme_style("stark"))
    nested_ctx = resolve_chart_style_context(get_theme_style("stark"))

    # Both boards have a chart titled "Revenue" — same chart_id.
    chart = _bar_chart("revenue")
    chart_item = LayoutItem(type="chart", width=400.0, height=0.0, chart=chart)

    # Minimal mock of a nested Board with stark theme override.
    nested_board = MagicMock()
    nested_board.resolved_style = nested_rs
    nested_board.layout = Layout.model_validate({"type": "rows", "items": [chart_item]})
    nested_board.title = ""
    nested_board.visible_variables = {}
    nested_board.text = None

    board_item = LayoutItem.model_construct(
        type="board", width=400.0, height=0.0, board=nested_board, chart=None
    )

    outer_layout = Layout.model_validate({"type": "cols", "items": [board_item]})

    executor = MagicMock(spec=Executor)
    executor.execute_query.return_value = []
    render_ctx = SizingRenderCtx(
        executor=executor,
        resolved_style=outer_rs,
        chart_style_context=outer_ctx,
        pre_resolved={"revenue": _resolved(chart, outer_ctx)},
    )

    rendered_style_ids: list[int] = []

    def fake_render(
        _resolved_v2,
        _executor,
        _variables,
        width: float,
        **kwargs,
    ) -> tuple[str, float, float]:
        rs = kwargs.get("resolved_style")
        rendered_style_ids.append(id(rs))
        return f'<svg viewBox="0 0 {width} 300"></svg>', float(width), 300.0

    style_contexts = {id(outer_rs): outer_ctx, id(nested_rs): nested_ctx}
    with patch.object(layout_sizing, "_render_chart_to_svg", side_effect=fake_render):
        provider = _make_data_aware_height_provider(
            render_ctx,
            executor,
            variables={},
            card_padding=0.0,
            style_contexts=style_contexts,
        )
        calculate_layout_height(
            outer_layout,
            card_gap=0.0,
            gap=0.0,
            min_height=0.0,
            available_width=400.0,
            height_provider=provider,
            resolved_style=outer_rs,
        )

    assert len(rendered_style_ids) == 1, (
        f"Expected exactly one render call for the nested chart, got {len(rendered_style_ids)}"
    )
    assert rendered_style_ids[0] == id(nested_rs), (
        f"Chart inside nested stark board must be rendered with the nested stark "
        f"resolved_style (id={id(nested_rs)}), not the outer cream resolved_style "
        f"(id={id(outer_rs)}). Got: {rendered_style_ids[0]}.\n"
        "Pre-fix: _resolve_height dispatches to HeightProvider without resolved_style, "
        "so the provider always uses render_ctx.resolved_style (cream) regardless of "
        "which nested board the chart belongs to."
    )


def test_sibling_nested_boards_different_themes_get_correct_resolved_styles() -> None:
    """Two sibling cols items with different theme overrides must each be sized
    using their own resolved_style.

    This is the full repro of the live bug: parent board = cream, left col = cream
    nested board, right col = stark nested board, both containing a chart with the
    same id ("revenue").  Pre-fix: both charts were sized with cream style and
    wrote a cream SVG to render_cache.  Post-fix: each chart is sized with its
    own theme's style.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
        resolve_style,
    )
    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render import layout_sizing
    from dbt_charts.core.render.layout_sizing import (
        SizingRenderCtx,
        _make_data_aware_height_provider,
    )
    from dbt_charts.core.render.sizing import calculate_layout_height

    cream_rs = resolve_style(get_theme_style("paper"))
    cream_ctx = resolve_chart_style_context(get_theme_style("paper"))
    stark_rs = resolve_style(get_theme_style("stark"))
    stark_ctx = resolve_chart_style_context(get_theme_style("stark"))

    # Each nested board has its own chart id — prevents natural_heights dedup from
    # swallowing the stark render and making the assertion conditional.
    cream_chart = _bar_chart("revenue_cream")
    stark_chart = _bar_chart("revenue_stark")
    cream_chart_item = LayoutItem(
        type="chart", width=200.0, height=0.0, chart=cream_chart
    )
    stark_chart_item = LayoutItem(
        type="chart", width=200.0, height=0.0, chart=stark_chart
    )

    cream_nested_board = MagicMock()
    cream_nested_board.resolved_style = cream_rs
    cream_nested_board.layout = Layout.model_validate(
        {"type": "rows", "items": [cream_chart_item]}
    )
    cream_nested_board.title = ""
    cream_nested_board.visible_variables = {}
    cream_nested_board.text = None

    stark_nested_board = MagicMock()
    stark_nested_board.resolved_style = stark_rs
    stark_nested_board.layout = Layout.model_validate(
        {"type": "rows", "items": [stark_chart_item]}
    )
    stark_nested_board.title = ""
    stark_nested_board.visible_variables = {}
    stark_nested_board.text = None

    cream_board_item = LayoutItem.model_construct(
        type="board", width=200.0, height=0.0, board=cream_nested_board, chart=None
    )
    stark_board_item = LayoutItem.model_construct(
        type="board", width=200.0, height=0.0, board=stark_nested_board, chart=None
    )
    outer_layout = Layout.model_validate(
        {"type": "cols", "items": [cream_board_item, stark_board_item]}
    )

    executor = MagicMock(spec=Executor)
    executor.execute_query.return_value = []
    render_ctx = SizingRenderCtx(
        executor=executor,
        resolved_style=cream_rs,  # outer board is cream
        chart_style_context=cream_ctx,
        pre_resolved={
            "revenue_cream": _resolved(cream_chart, cream_ctx),
            "revenue_stark": _resolved(stark_chart, stark_ctx),
        },
    )

    # Capture resolved_style id per chart id for each render call.
    rendered_styles: dict[str, int] = {}

    def fake_render(
        resolved_v2,
        _executor,
        _variables,
        width: float,
        **kwargs,
    ) -> tuple[str, float, float]:
        rs = kwargs.get("resolved_style")
        rendered_styles[resolved_v2.id] = id(rs)
        return f'<svg viewBox="0 0 {width} 300"></svg>', float(width), 300.0

    style_contexts = {id(cream_rs): cream_ctx, id(stark_rs): stark_ctx}
    with patch.object(layout_sizing, "_render_chart_to_svg", side_effect=fake_render):
        provider = _make_data_aware_height_provider(
            render_ctx,
            executor,
            variables={},
            card_padding=0.0,
            style_contexts=style_contexts,
        )
        calculate_layout_height(
            outer_layout,
            card_gap=0.0,
            gap=0.0,
            min_height=0.0,
            available_width=400.0,
            height_provider=provider,
            resolved_style=cream_rs,
        )

    # Both charts have distinct ids — no natural_heights dedup — both render.
    assert rendered_styles.get("revenue_cream") == id(cream_rs), (
        f"Cream col chart must use cream resolved_style (id={id(cream_rs)}), "
        f"got {rendered_styles.get('revenue_cream')}."
    )
    assert rendered_styles.get("revenue_stark") == id(stark_rs), (
        f"Stark col chart must use stark resolved_style (id={id(stark_rs)}), "
        f"got {rendered_styles.get('revenue_stark')}.\n"
        "Pre-fix: _resolve_height drops resolved_style when dispatching to "
        "HeightProvider, so the stark chart is rendered with cream style instead."
    )


@pytest.mark.parametrize("theme_name", ["paper", "stark"])
def test_height_provider_resolved_style_param_propagated_to_render(
    theme_name: str,
) -> None:
    """The resolved_style passed into HeightProvider must reach _render_chart_to_svg.

    This test parametrizes over multiple themes to ensure the fix is general, not
    just a special-case for cream→stark.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
        resolve_style,
    )
    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render import layout_sizing
    from dbt_charts.core.render.layout_sizing import (
        SizingRenderCtx,
        _make_data_aware_height_provider,
    )
    from dbt_charts.core.render.sizing import calculate_layout_height

    outer_rs = resolve_style(get_theme_style("paper"))
    outer_ctx = resolve_chart_style_context(get_theme_style("paper"))
    nested_rs = resolve_style(get_theme_style(theme_name))
    nested_ctx = resolve_chart_style_context(get_theme_style(theme_name))

    chart = _bar_chart("metric")
    chart_item = LayoutItem(type="chart", width=400.0, height=0.0, chart=chart)

    nested_board = MagicMock()
    nested_board.resolved_style = nested_rs
    nested_board.chart_style_context = nested_ctx
    nested_board.layout = Layout.model_validate({"type": "rows", "items": [chart_item]})
    nested_board.title = ""
    nested_board.visible_variables = {}
    nested_board.text = None

    board_item = LayoutItem.model_construct(
        type="board", width=400.0, height=0.0, board=nested_board, chart=None
    )
    outer_layout = Layout.model_validate({"type": "cols", "items": [board_item]})

    executor = MagicMock(spec=Executor)
    executor.execute_query.return_value = []
    render_ctx = SizingRenderCtx(
        executor=executor,
        resolved_style=outer_rs,
        chart_style_context=outer_ctx,
        pre_resolved={"metric": _resolved(chart, outer_ctx)},
    )
    style_contexts: dict[int, ChartStyleContext] = {
        id(outer_rs): outer_ctx,
        id(nested_rs): nested_ctx,
    }

    captured_styles: list[int] = []

    def fake_render(_rv2, _ex, _vars, width: float, **kw) -> tuple[str, float, float]:
        captured_styles.append(id(kw.get("resolved_style")))
        return f'<svg viewBox="0 0 {width} 300"></svg>', float(width), 300.0

    with patch.object(layout_sizing, "_render_chart_to_svg", side_effect=fake_render):
        provider = _make_data_aware_height_provider(
            render_ctx,
            executor,
            variables={},
            card_padding=0.0,
            style_contexts=style_contexts,
        )
        calculate_layout_height(
            outer_layout,
            card_gap=0.0,
            gap=0.0,
            min_height=0.0,
            available_width=400.0,
            height_provider=provider,
            resolved_style=outer_rs,
        )

    assert len(captured_styles) == 1
    assert captured_styles[0] == id(nested_rs), (
        f"Chart in nested {theme_name!r} board must be sized with {theme_name!r} "
        f"resolved_style (id={id(nested_rs)}), got id={captured_styles[0]} "
        f"(outer cream id={id(outer_rs)})."
    )
