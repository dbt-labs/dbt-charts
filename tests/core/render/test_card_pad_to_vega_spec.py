"""Tests: card_pad forwarded as Vega internal padding for layout charts.

Vega-family charts in a layout must receive card_pad as internal Vega padding
(not as an outer SVG translate), so board titles align with chart content.

TDD: these tests fail before the fix and pass after.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import Project

# ---------------------------------------------------------------------------
# 1. render_layout_item: Vega charts get no SVG translate, full width
# ---------------------------------------------------------------------------


class TestVegaChartLayoutBehavior:
    """Vega charts in a layout must NOT get an outer SVG translate.

    Instead, card_pad is forwarded as Vega internal padding so the Vega
    chart handles its own inset.  SVG-family charts (table/kpi/spark_bar)
    keep the current translate.
    """

    def _make_bar_item(self):
        """Return a ResolvedLayoutItem wrapping a V2-resolved bar chart."""
        from dbt_charts.core.compile import compile
        from dbt_charts.core.compile.models.board.resolved import ResolvedLayoutItem
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )

        result = compile(
            """
queries:
  q:
    type: values
    rows:
      - {x: a, y: 1}
charts:
  bar1:
    query: q
    type: bar
    x: x
    y: y
rows:
  - bar1
"""
        )
        assert result.success and result.board is not None
        board = result.board
        _, ctx = resolve_style_and_context(get_theme_style())
        rc_v2 = resolve(board.charts["bar1"], [], chart_style_context=ctx)
        return ResolvedLayoutItem(
            type="chart", chart=rc_v2, board=None, x=0.0, y=0.0, width=800, height=300
        )

    def _make_kpi_item(self):
        """Return a ResolvedLayoutItem wrapping a V2-resolved KPI chart (SVG-family)."""
        from dbt_charts.core.compile import compile
        from dbt_charts.core.compile.models.board.resolved import ResolvedLayoutItem
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )

        result = compile(
            """
queries:
  q:
    type: values
    rows:
      - {value: 42}
charts:
  kpi1:
    query: q
    type: kpi
    value: value
rows:
  - kpi1
"""
        )
        assert result.success and result.board is not None
        board = result.board
        _, ctx = resolve_style_and_context(get_theme_style())
        rc_v2 = resolve(board.charts["kpi1"], [], chart_style_context=ctx)
        return ResolvedLayoutItem(
            type="chart", chart=rc_v2, board=None, x=0.0, y=0.0, width=800, height=200
        )

    def test_vega_chart_no_svg_translate(self):
        """render_layout_item for a Vega chart must NOT wrap in SVG translate."""
        import importlib

        from dbt_charts.core.render.chart.rendering import render_layout_item

        rendering_module = importlib.import_module(
            "dbt_charts.core.render.chart.rendering"
        )

        card_padding = float(get_theme_style().frame.card_padding)
        item = self._make_bar_item()
        chart_svg = '<g class="dbt-chart"><svg width="800" height="300" viewBox="0 0 800 300"></svg></g>'

        def mock_render_chart(*args, **kwargs):
            return chart_svg, 300.0

        from dbt_charts.core.compile.resolve.style.board import resolve_style

        style = resolve_style(get_theme_style())
        with patch.object(
            rendering_module, "render_chart_item", side_effect=mock_render_chart
        ):
            svg, _ = render_layout_item(
                item,
                MagicMock(),
                {},
                0.0,
                800.0,
                300.0,
                resolved_style=style,
                render_cache={},
                painted_canvas=None,
            )

        assert f"translate({card_padding}, {card_padding})" not in svg, (
            "Vega chart must NOT be wrapped in SVG translate — card_pad "
            "is now forwarded as Vega internal padding"
        )

    def test_vega_chart_rendered_at_full_width(self):
        """render_chart_item for a Vega chart is called with the full item width."""
        import importlib

        vega_lite_module = importlib.import_module(
            "dbt_charts.core.render.chart.vega_lite"
        )

        from dbt_charts.core.render.chart.rendering import render_layout_item

        card_padding = float(get_theme_style().frame.card_padding)
        item_width = 800.0
        item = self._make_bar_item()  # already sets width=800

        # A minimal artifact the render path returns — just needs to be
        # consumable by render_chart_artifact (which calls render_vega_spec).
        render_calls: list[dict[str, Any]] = []

        def mock_render_v2(resolved, data, resolved_style, *, width, **kwargs):
            render_calls.append({"width": width, "kwargs": kwargs})
            # Return a minimal VegaLiteArtifact-compatible object by forwarding
            # the original call would be complex; just raise ChartDataError so
            # render_chart_item returns an error tile (render_calls is what we check).
            from dbt_charts.core.diagnostics.chart_data import ChartDataError

            raise ChartDataError("mock")

        from dbt_charts.core.compile.resolve.style.board import resolve_style

        style = resolve_style(get_theme_style())
        with patch.object(
            vega_lite_module, "render_resolved_chart", side_effect=mock_render_v2
        ):
            render_layout_item(
                item,
                MagicMock(),
                {},
                0.0,
                item_width,
                300.0,
                resolved_style=style,
                render_cache={},
                painted_canvas=None,
            )

        assert render_calls, "render_resolved_chart must be called"
        called_width = render_calls[0]["width"]
        assert called_width == pytest.approx(item_width, abs=1.0), (
            f"Vega chart must be rendered at full item width {item_width}, "
            f"got {called_width} (should not subtract 2*card_pad={2 * card_padding})"
        )

    def test_vega_chart_padding_forwarded_to_spec(self):
        """card_pad is forwarded as padding dict to render_resolved_chart."""
        import importlib

        vega_lite_module = importlib.import_module(
            "dbt_charts.core.render.chart.vega_lite"
        )

        from dbt_charts.core.render.chart.rendering import render_layout_item

        card_padding = float(get_theme_style().frame.card_padding)
        item = self._make_bar_item()

        render_calls: list[dict[str, Any]] = []

        def mock_render_v2(resolved, data, resolved_style, *, width, **kwargs):
            render_calls.append({"width": width, "kwargs": kwargs})
            from dbt_charts.core.diagnostics.chart_data import ChartDataError

            raise ChartDataError("mock")

        with patch.object(
            vega_lite_module, "render_resolved_chart", side_effect=mock_render_v2
        ):
            from dbt_charts.core.compile.resolve.style.board import resolve_style

            render_layout_item(
                item,
                MagicMock(),
                {},
                0.0,
                800.0,
                300.0,
                resolved_style=resolve_style(get_theme_style()),
                render_cache={},
                painted_canvas=None,
            )

        assert render_calls, "render_resolved_chart must be called"
        passed_padding = render_calls[0]["kwargs"].get("padding")
        expected_padding = {
            "left": card_padding,
            "right": card_padding,
            "top": card_padding,
            "bottom": card_padding,
        }
        assert passed_padding == expected_padding, (
            f"card_pad must be forwarded as Vega padding, got {passed_padding!r}"
        )

    def test_vega_chart_height_not_inflated(self):
        """render_layout_item for a Vega chart returns chart_height directly."""
        import importlib

        rendering_module = importlib.import_module(
            "dbt_charts.core.render.chart.rendering"
        )

        from dbt_charts.core.render.chart.rendering import render_layout_item

        chart_height = 300.0
        item = self._make_bar_item()
        chart_svg = f'<g class="dbt-chart"><svg width="800" height="{chart_height}" viewBox="0 0 800 {chart_height}"></svg></g>'

        def mock_render_chart(*args, **kwargs):
            return chart_svg, chart_height

        with patch.object(
            rendering_module, "render_chart_item", side_effect=mock_render_chart
        ):
            from dbt_charts.core.compile.resolve.style.board import resolve_style

            _, actual_height = render_layout_item(
                item,
                MagicMock(),
                {},
                0.0,
                800.0,
                300.0,
                resolved_style=resolve_style(get_theme_style()),
                render_cache={},
                painted_canvas=None,
            )

        # Vega chart height must NOT have 2*card_pad added (Vega handles padding internally)
        assert actual_height == pytest.approx(chart_height, abs=1.0), (
            f"Vega chart actual_height should be {chart_height} (no 2*card_pad inflation), "
            f"got {actual_height}"
        )

    def test_svg_family_chart_receives_additive_padding_dict(self):
        """SVG-family charts now receive the same 4-sided padding dict as Vega.

        Post-unification, the SVG-family branch is gone — every chart family
        reads per-family ``PaddingStyle`` and forwards the additive dict.
        """
        import importlib

        rendering_module = importlib.import_module(
            "dbt_charts.core.render.chart.rendering"
        )

        from dbt_charts.core.render.chart.rendering import render_layout_item

        card_padding = float(get_theme_style().frame.card_padding)
        item = self._make_kpi_item()
        chart_height = 200.0
        chart_svg = f'<g class="dbt-chart"><svg width="800" height="{chart_height}" viewBox="0 0 800 {chart_height}"></svg></g>'
        render_calls: list[dict[str, Any]] = []

        def mock_render_chart(*args, **kwargs):
            render_calls.append(kwargs)
            return chart_svg, chart_height

        with patch.object(
            rendering_module, "render_chart_item", side_effect=mock_render_chart
        ):
            from dbt_charts.core.compile.resolve.style.board import resolve_style

            _svg, _actual_height = render_layout_item(
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

        assert render_calls, "render_chart_item must be called for SVG-family charts"
        padding_arg = render_calls[0].get("padding")
        assert padding_arg is not None, (
            "SVG-family charts must receive the 4-sided padding dict, "
            "same as Vega-family"
        )
        # Default theme padding is {0,0,0,0}, so additive_padding emits card_pad on each side.
        for side in ("top", "right", "bottom", "left"):
            assert padding_arg[side] == pytest.approx(card_padding, abs=0.1), (
                f"padding.{side} should be card_padding on a chart with no local override"
            )


# ---------------------------------------------------------------------------
# 2. Layout sizing: Vega item height = actual SVG height (no + 2*card_pad)
# ---------------------------------------------------------------------------


class TestVegaSizingNoPadInflation:
    """Layout sizing provider must return actual SVG height for Vega charts.

    The SVG height already contains Vega's internal padding (= card_pad).
    Adding 2*card_pad on top inflates the item height incorrectly.
    """

    def test_render_first_sizing_returns_actual_height(
        self, local_project: Callable[..., Project]
    ):
        """Data-aware sizing returns actual SVG height without adding 2*card_pad."""
        import importlib
        from unittest.mock import patch

        _converters_chart = importlib.import_module(
            "dbt_charts.core.render.converters.chart"
        )
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render.board_resolve import build_resolved_board

        yaml_content = """
queries:
  q:
    type: values
    rows:
      - {month: "2024-01", revenue: 1000}
      - {month: "2024-02", revenue: 1500}
      - {month: "2024-03", revenue: 1200}
charts:
  c1:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - c1
"""
        MOCK_HEIGHT = 2000
        MOCK_SVG = f'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="{MOCK_HEIGHT}"><g/></svg>'

        result = compile(yaml_content)
        assert result.success and result.board is not None
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        with patch.object(_converters_chart, "render_vega_spec", return_value=MOCK_SVG):
            resolved_board, _ = build_resolved_board(result.board, executor, {})

        bar_item = resolved_board.layout.items[0]
        # After fix: item height equals actual SVG height (card_pad is Vega-internal)
        assert abs(bar_item.height - MOCK_HEIGHT) < 1.0, (
            f"Item height {bar_item.height} should equal SVG height {MOCK_HEIGHT}, "
            f"not {MOCK_HEIGHT} + 2*card_pad"
        )
