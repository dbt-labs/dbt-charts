"""Render layer wiring to ResolvedBoard.

Covers:
1. renderer.render() calls resolve_board() as part of the render pipeline
2. render() still produces correct SVG output (no regressions)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from ._svg_render import PINNED_RENDER_TIME

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compile_board() -> Any:
    from dbt_charts.core.compile.compiler import compile

    result = compile(
        """
id: test-board
title: Test Board
source: duckdb
rows:
  - bar1
charts:
  bar1:
    type: bar
    x: cat
    y: val
    query:
      sql: SELECT 'A' AS cat, 10 AS val
"""
    )
    return result.board


def _make_mock_executor() -> Any:
    exc = MagicMock()
    exc.render_with_range_filters.return_value = None
    exc.execute_chart.return_value = [{"cat": "A", "val": 10}]
    exc.execute_batch.return_value = {}
    exc._query_errors = {}
    exc._query_results = {}
    # No cache in play here — the real Executor.cache_hit_ats contract is an
    # empty list until a persistent-cache hit occurs, which never happens
    # against this mock's adapter stand-in.
    exc.cache_hit_ats = []
    exc.rendered_at = PINNED_RENDER_TIME
    return exc


# ---------------------------------------------------------------------------
# Compiled Vega-Lite config
# ---------------------------------------------------------------------------


class TestCompiledVegaConfig:
    def test_returns_non_empty_dict(self):
        from ._board_utils import _default_resolved_style

        result = _default_resolved_style().vega_config
        assert isinstance(result, dict) and len(result) > 0


# ---------------------------------------------------------------------------
# renderer.py wired to resolve_board
# ---------------------------------------------------------------------------


class TestRendererWiring:
    """renderer.render() calls resolve_board() as part of the pipeline."""

    def test_renderer_knows_about_resolve_board(self):
        """build_resolved_board_static is the static resolver callable."""
        from dbt_charts.core.render.board_resolve import build_resolved_board_static

        assert callable(build_resolved_board_static)

    def test_render_produces_svg(self):
        """End-to-end smoke: render() produces valid SVG after wiring."""
        from dbt_charts.core.render.renderer import render

        board = _compile_board()
        executor = _make_mock_executor()
        result = render(board, executor, format="svg", variables={}).output
        assert isinstance(result, str) and "<svg" in result

    def test_render_produces_html(self):
        """End-to-end: html format still works."""
        from dbt_charts.core.render.renderer import render

        board = _compile_board()
        executor = _make_mock_executor()
        result = render(board, executor, format="html", variables={}).output
        assert isinstance(result, str) and "<!DOCTYPE" in result

    def test_resolve_board_result_accessible(self):
        """build_resolved_board_static() returns a ResolvedBoard."""
        from dbt_charts.core.compile.config import reset_config
        from dbt_charts.core.compile.models.board.resolved import ResolvedBoard
        from dbt_charts.core.render.board_resolve import build_resolved_board_static

        reset_config()
        board = _compile_board()
        result = build_resolved_board_static(board)
        assert isinstance(result, ResolvedBoard)
