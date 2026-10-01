"""TDD tests for Vega height propagation through the render stack.

These tests verify Option B from the task spec:
1. Vega-Lite charts are rendered with height=None (auto-size).
2. Actual rendered heights bubble up through render_chart_item,
   render_layout_item, and layout renderers.
3. render_rows_layout positions sibling items using actual heights.

All tests here FAIL before the fix and PASS after.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.board.normalized import NO_TITLE_SHIFT
from dbt_charts.core.compile.models.board.resolved import ResolvedLayoutItem
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.style.theme import PaddingStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_style,
    resolve_style_and_context,
)
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.project import Project

_ZERO_PADDING = PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _translate_y_values(svg: str) -> list[float]:
    """Return all Y values from translate(0, Y) transforms in the SVG."""
    return [float(m) for m in re.findall(r"translate\(0,\s*([\d.]+)\)", svg)]


# ---------------------------------------------------------------------------
# Test 1 — render_chart_item returns tuple[str, float]
# ---------------------------------------------------------------------------


class TestRenderChartItemReturnsActualHeight:
    """render_chart_item must return (svg_str, actual_height_float) not a plain str.

    Before the fix this fails because render_chart_item returns str.
    """

    def test_returns_tuple(self, make_chart):
        """render_chart_item must return (str, float)."""
        from dbt_charts.core.render.chart.rendering import render_chart_item

        chart = make_chart("bar", x="category", y="value")
        executor = MagicMock(spec=Executor)
        executor.execute_chart.return_value = [
            {"category": "A", "value": 10},
            {"category": "B", "value": 20},
        ]

        rs, ctx = resolve_style_and_context(get_theme_style())
        result = render_chart_item(
            resolve(chart, [], chart_style_context=ctx),
            executor,
            {},
            600,
            300,
            resolved_style=rs,
            render_cache={},
        )

        assert isinstance(result, tuple), (
            f"render_chart_item must return tuple[str, float], got {type(result)}"
        )
        svg, actual_height = result
        assert isinstance(svg, str)
        assert isinstance(actual_height, float)
        assert "<svg" in svg or "<g" in svg

    def test_actual_height_matches_svg_viewbox(self, make_chart):
        """Returned actual_height must equal the height in the rendered SVG."""
        from dbt_charts.core.render.chart.rendering import render_chart_item

        chart = make_chart("bar", x="category", y="value")
        executor = MagicMock(spec=Executor)
        executor.execute_chart.return_value = [
            {"category": "A", "value": 10},
            {"category": "B", "value": 20},
        ]

        rs, ctx = resolve_style_and_context(get_theme_style())
        svg, actual_height = render_chart_item(
            resolve(chart, [], chart_style_context=ctx),
            executor,
            {},
            600,
            300,
            resolved_style=rs,
            render_cache={},
        )

        # The data-chart-height attribute must agree with the returned float
        m = re.search(r'data-chart-height="([\d.]+)"', svg)
        assert m, "data-chart-height attribute must be present"
        assert float(m.group(1)) == pytest.approx(actual_height, abs=1.0)

    def test_svg_family_chart_height_matches_passed_height(self, make_chart):
        """SVG-family charts (table/kpi/spark_bar) keep the pre-computed height
        when it fits the chart's structural minimum.

        KPI's quantitative-text-object layout reserves fixed slots for value /
        title / support rows so a row of KPIs stays vertically aligned. When
        the requested height is below that structural minimum the renderer
        expands; when it is above it, the requested height wins.
        """
        from dbt_charts.core.render.chart.rendering import render_chart_item

        chart = make_chart("kpi", x=None, y=None, value="value")
        executor = MagicMock(spec=Executor)
        executor.execute_chart.return_value = [{"value": 42}]

        # Use a height comfortably above the KPI fixed-slot minimum so the
        # renderer respects what was passed.
        available_height = 220.0
        rs, ctx = resolve_style_and_context(get_theme_style())
        result = render_chart_item(
            resolve(chart, [], chart_style_context=ctx),
            executor,
            {},
            600,
            available_height,
            resolved_style=rs,
            render_cache={},
        )

        assert isinstance(result, tuple)
        _svg, actual_height = result
        assert actual_height == pytest.approx(available_height, abs=10.0)


# ---------------------------------------------------------------------------
# Test 2 — rows layout uses actual item heights for Y positioning
# ---------------------------------------------------------------------------


class TestRowsLayoutUsesActualHeights:
    """render_rows_layout must position the second item at first_actual_height + gap.

    Before the fix this fails because render_rows_layout returns str.
    """

    def test_returns_tuple(self, local_project: Callable[..., Project]):
        """render_rows_layout must return (svg_str, total_height_float)."""
        from dbt_charts.core.compile import compile
        from dbt_charts.core.render.layouts import render_rows_layout

        result = compile(
            """\
queries:
  q:
    type: values
    rows:
      - {cat: A, val: 1}
      - {cat: B, val: 2}
charts:
  c1:
    query: q
    type: bar
    x: cat
    y: val
  c2:
    query: q
    type: bar
    x: cat
    y: val
rows:
  - c1
  - c2
"""
        )
        assert result.success and result.board is not None
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        from dbt_charts.core.render.board_resolve import build_resolved_board

        resolved, _ = build_resolved_board(result.board, executor, {})
        items = resolved.layout.items

        layout_result = render_rows_layout(
            items,
            executor,
            {},
            800.0,
            600.0,
            0.0,
            20.0,
            resolved_style=resolve_style(get_theme_style()),
            render_cache={},
            painted_canvas=None,
        )

        assert isinstance(layout_result, tuple), (
            f"render_rows_layout must return tuple[str, float], got {type(layout_result)}"
        )
        svg, total_height = layout_result
        assert isinstance(svg, str)
        assert isinstance(total_height, float)
        assert total_height > 0

    def test_second_item_y_equals_first_actual_height_plus_gap(self, tmp_path: Path):
        """Second item's translate Y must equal first item's actual rendered height + gap."""
        import importlib
        from unittest.mock import patch

        from dbt_charts.core.render.layouts import render_rows_layout

        layouts_module = importlib.import_module("dbt_charts.core.render.layouts")

        # Simulate two items where the first renders 400px tall, second 300px.
        first_svg = '<svg width="800" height="400" viewBox="0 0 800 400"><rect/></svg>'
        second_svg = '<svg width="800" height="300" viewBox="0 0 800 300"><rect/></svg>'

        item1 = ResolvedLayoutItem(
            type="chart", chart=None, board=None, x=0, y=0, width=800, height=200
        )  # pre-computed 200, actual 400
        item2 = ResolvedLayoutItem(
            type="chart", chart=None, board=None, x=0, y=0, width=800, height=200
        )  # pre-computed 200, actual 300

        call_count = 0

        def mock_render(item, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return first_svg, 400.0
            return second_svg, 300.0

        gap = 20.0
        with patch.object(
            layouts_module, "render_layout_item", side_effect=mock_render
        ):
            svg, total_height = render_rows_layout(
                (item1, item2),
                MagicMock(),
                {},
                800.0,
                600.0,
                0.0,
                gap,
                resolved_style=resolve_style(get_theme_style()),
                render_cache={},
                painted_canvas=None,
            )

        # First item lands at Y=0 — a no-op translate, so it's embedded with no
        # wrapper <g> at all (an inert wrapper wastes PDF's nesting budget; see
        # translate_group in svg_utils.py). Second item must be at
        # Y = first_actual_height + gap = 400 + 20 = 420, which IS a real
        # translate and does get wrapped.
        assert first_svg in svg, "first item (Y=0) should be embedded unwrapped"
        ys = _translate_y_values(svg)
        assert ys == [pytest.approx(400.0 + gap, abs=1.0)], (
            f"Second item Y should be first_actual_height({400})+gap({gap})=420, got {ys}"
        )

        # Total height should be first + gap + second = 400 + 20 + 300 = 720
        assert total_height == pytest.approx(420.0 + 300.0, abs=1.0)


# ---------------------------------------------------------------------------
# Test 5 — render_layout_item includes card_padding in returned actual_height
# ---------------------------------------------------------------------------


class TestCardPaddingHeightPropagation:
    """Card padding behavior differs by renderer family.

    - SVG-family (kpi/table/spark_bar): wrapped in SVG translate; parent receives
      chart_height + 2*card_padding.
    - Vega-family (bar/line/etc.): card_pad forwarded as Vega internal padding;
      Vega's SVG height already includes the inset, so parent receives chart_height
      directly (no inflation).
    """

    def _make_item(self, chart_type: str) -> tuple[Any, str, float]:
        """Return (item, mock_chart_svg, chart_height) for the given chart type."""
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )

        chart_height = 400.0
        _, ctx = resolve_style_and_context(get_theme_style())

        # Build the chart model directly for each type.
        if chart_type == "kpi":
            from dbt_charts.core.compile.models.chart.normalized import KpiChart

            chart = KpiChart(
                id="test",
                type="kpi",
                value="value",
                label="",
                query=SqlQuery(sql="SELECT 1", source="test"),
                query_name="q",
            )
        else:
            from dbt_charts.core.compile.models.chart.normalized import BarChart

            chart = BarChart(
                id="test",
                type="bar",
                x="x",
                y="y",
                title="",
                subtitle="",
                query=SqlQuery(sql="SELECT 1", source="test"),
                query_name="q",
            )
        rc = resolve(chart, [], chart_style_context=ctx)
        item = ResolvedLayoutItem(
            type="chart", chart=rc, board=None, x=0.0, y=0.0, width=800, height=200
        )
        mock_svg = f'<g class="dbt-chart"><svg width="800" height="{chart_height}" viewBox="0 0 800 {chart_height}"><rect/></svg></g>'
        return item, mock_svg, chart_height

    def test_vega_chart_height_not_inflated(self):
        """Vega-family: actual_height == chart_height (Vega handles padding internally)."""
        import importlib
        from unittest.mock import MagicMock, patch

        from dbt_charts.core.render.chart.rendering import render_layout_item

        rendering_module = importlib.import_module(
            "dbt_charts.core.render.chart.rendering"
        )

        card_padding = float(get_theme_style().frame.card_padding)
        item, mock_svg, chart_height = self._make_item("bar")

        with patch.object(
            rendering_module, "render_chart_item", return_value=(mock_svg, chart_height)
        ):
            _svg, actual_height = render_layout_item(
                item,
                MagicMock(),
                {},
                0.0,
                800.0,
                200.0,
                resolved_style=resolve_style(get_theme_style()),
                render_cache={},
                painted_canvas=None,
            )

        # Vega: height returned as-is (internal padding already baked in by Vega)
        assert actual_height == pytest.approx(chart_height, abs=1.0), (
            f"Vega chart actual_height={actual_height} should equal chart_height={chart_height} "
            f"(NOT inflated by 2*card_padding={2 * card_padding})"
        )

    def test_svg_family_height_comes_from_render_chart_item(self):
        """SVG-family layout trusts the renderer's returned actual height."""
        import importlib
        from unittest.mock import MagicMock, patch

        from dbt_charts.core.render.chart.rendering import render_layout_item

        rendering_module = importlib.import_module(
            "dbt_charts.core.render.chart.rendering"
        )

        item, mock_svg, chart_height = self._make_item("kpi")

        with patch.object(
            rendering_module, "render_chart_item", return_value=(mock_svg, chart_height)
        ):
            _svg, actual_height = render_layout_item(
                item,
                MagicMock(),
                {},
                0.0,
                800.0,
                200.0,
                resolved_style=resolve_style(get_theme_style()),
                render_cache={},
                painted_canvas=None,
            )

        assert actual_height == pytest.approx(chart_height, abs=1.0), (
            f"SVG-family actual_height={actual_height} should match "
            f"render_chart_item output {chart_height}"
        )


# ---------------------------------------------------------------------------
# Test 6 — layout background rects use actual height, not pre-computed height
# ---------------------------------------------------------------------------


class TestLayoutBgRectUsesActualHeight:
    """Background rects must be sized to actual rendered height, not available_height.

    If a Vega chart renders taller than the pre-computed slot, the background
    must extend to cover the actual content height — not stop at the pre-computed
    value, which would leave content overflowing the background.
    """

    def test_rows_layout_bg_rect_uses_actual_height(self, tmp_path: Path):
        """rows layout bg rect height == actual_total_height, not available_height."""
        import importlib
        import re
        from unittest.mock import patch

        from dbt_charts.core.render.layouts import render_rows_layout

        layouts_module = importlib.import_module("dbt_charts.core.render.layouts")

        # Pre-computed height is 200 but Vega renders to 400 (auto-sized taller)
        item_svg = '<svg width="800" height="400" viewBox="0 0 800 400"><rect/></svg>'
        item = ResolvedLayoutItem(
            type="chart", chart=None, board=None, x=0, y=0, width=800, height=200
        )

        def mock_render(_item, *args, **kwargs):
            return item_svg, 400.0

        with patch.object(
            layouts_module, "render_layout_item", side_effect=mock_render
        ):
            svg, total_height = render_rows_layout(
                (item,),
                MagicMock(),
                {},
                800.0,
                200.0,
                0.0,
                0.0,
                background="#ffffff",
                resolved_style=resolve_style(get_theme_style()),
                render_cache={},
                painted_canvas=None,
            )

        assert total_height == pytest.approx(400.0, abs=1.0)

        # Background rect height must match actual height (400), not available_height (200)
        rect_heights = [
            float(m) for m in re.findall(r'<rect[^>]+height="([\d.]+)"', svg)
        ]
        assert rect_heights, "No <rect> found in SVG"
        bg_height = rect_heights[0]  # bg_rect is first element
        assert bg_height == pytest.approx(400.0, abs=1.0), (
            f"Background rect height={bg_height} should match actual_total_height=400, "
            f"not available_height=200"
        )


# ---------------------------------------------------------------------------
# Test 5 — _align_cols_heights passes correct args to render_chart_to_svg
# ---------------------------------------------------------------------------


class TestAlignColsHeights:
    """_align_cols_heights must re-render shorter Vega items with full item width,
    the target height, and uniform card padding."""

    def test_rerenders_with_correct_args(self):
        """Shorter Vega item is re-rendered with item.width, target_height, uniform padding."""
        from unittest.mock import MagicMock, patch

        from dbt_charts.core.compile.models.board.normalized import LayoutItem
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.render.chart.spec_builders import additive_padding
        from dbt_charts.core.render.layout_sizing import (
            SizingRenderCtx,
            _align_cols_heights,
        )

        card_pad = float(get_theme_style().frame.card_padding)
        target_height = 300.0
        _q = SqlQuery(sql="SELECT 1", source="test")

        chart_tall = BarChart(id="chart_tall", type="bar", query=_q, x="x", y="y")
        chart_short = BarChart(id="chart_short", type="bar", query=_q, x="x", y="y")

        item_tall = LayoutItem(
            type="chart", width=400.0, height=300.0, chart=chart_tall
        )
        item_short = LayoutItem(
            type="chart", width=600.0, height=200.0, chart=chart_short
        )

        render_cache = {
            ("chart_tall", 400.0, 300.0): ('<svg width="400" height="300"/>', 300.0),
            ("chart_short", 600.0, 200.0): ('<svg width="600" height="200"/>', 200.0),
        }
        executor = MagicMock()
        from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )

        # Pre-resolved V2 mocks: _align_cols_heights looks up chart_id in pre_resolved
        # and passes the result to _render_chart_to_svg.
        mock_tall = MagicMock(spec=ResolvedChart)
        mock_tall.id = "chart_tall"
        mock_tall.layout_padding = _ZERO_PADDING
        mock_short = MagicMock(spec=ResolvedChart)
        mock_short.id = "chart_short"
        mock_short.layout_padding = _ZERO_PADDING

        _rs, _ctx = resolve_style_and_context(get_theme_style())
        render_ctx = SizingRenderCtx(
            render_cache=render_cache,
            natural_heights={
                ("chart_tall", 400.0): 300.0,
                ("chart_short", 600.0): 200.0,
            },
            pre_resolved={"chart_tall": mock_tall, "chart_short": mock_short},
            executor=executor,
            variables={"v": 1},
            resolved_style=_rs,
            chart_style_context=_ctx,
        )

        new_svg = '<svg width="600" height="300"/>'
        import importlib

        layout_sizing_mod = importlib.import_module(
            "dbt_charts.core.render.layout_sizing"
        )
        with patch.object(
            layout_sizing_mod,
            "_render_chart_to_svg",
            return_value=(new_svg, item_short.width, 300.0, None),
        ) as mock_render:
            _align_cols_heights([item_tall, item_short], target_height, render_ctx)

        mock_render.assert_called_once()
        _, pos_args, kw_args = mock_render.mock_calls[0]
        # Positional: resolved_v2_chart, executor, variables, width
        assert pos_args[0] is mock_short
        assert pos_args[3] == pytest.approx(item_short.width)
        # Keyword: height and padding
        assert kw_args["height"] == pytest.approx(target_height)
        chart_padding = render_ctx.chart_style_context.padding
        assert kw_args["padding"] == additive_padding(card_pad, chart_padding)
        # Cache updated with new svg under (chart_id, width, target_height) key
        assert render_ctx.render_cache[
            ("chart_short", 600.0, 300.0, NO_TITLE_SHIFT)
        ] == (
            new_svg,
            300.0,
        )


class TestRenderChartItemAcceptsResolvedChart:
    """render_chart_item must accept ResolvedChart, not Chart."""

    def test_render_chart_item_with_resolved_chart(self):
        """Passing a ResolvedChart directly to render_chart_item must work."""
        from unittest.mock import MagicMock

        from dbt_charts.core.compile.compiler import compile
        from dbt_charts.core.compile.config import reset_config
        from dbt_charts.core.compile.resolve.style.board import resolve_style
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )
        from dbt_charts.core.render.chart.rendering import render_chart_item

        reset_config()

        yaml_content = """
id: test-board
title: Test
source: duckdb
rows:
  - bar1
charts:
  bar1:
    type: bar
    x: category
    y: value
    query:
      sql: SELECT 'A' AS category, 10 AS value
"""
        result = compile(yaml_content)
        assert result.board is not None
        resolved_board = resolve_board(result.board)

        chart_items = [
            i for i in resolved_board.layout.items if i.type == "chart" and i.chart
        ]
        assert chart_items, "Need at least one chart item"
        resolved_chart = chart_items[0].chart
        assert resolved_chart is not None

        executor = MagicMock(spec=Executor)
        executor.execute_chart.return_value = [{"category": "A", "value": 10}]

        svg, height = render_chart_item(
            resolved_chart,
            executor,
            {},
            600,
            300,
            resolved_style=resolve_style(get_theme_style()),
            render_cache={},
        )
        assert svg
        assert height > 0


# ---------------------------------------------------------------------------
# Test — resolved chart height is directly assignable (no object.__setattr__)
# ---------------------------------------------------------------------------


class TestResolvedChartHeightAssignable:
    """Resolved chart models are fully frozen — no field mutation is permitted."""

    def test_other_fields_remain_frozen(self):
        """Unrelated fields must still be immutable (frozen semantics preserved)."""
        import pytest

        from dbt_charts.core.compile.models.chart.normalized import BarChart
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )

        chart = BarChart(
            id="test_bar",
            type="bar",
            x="cat",
            y="val",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
        )
        _, ctx = resolve_style_and_context(get_theme_style())
        resolved = resolve(chart, [], chart_style_context=ctx)

        with pytest.raises((TypeError, AttributeError, ValueError)):
            resolved.id = "mutated"  # type: ignore[misc]
