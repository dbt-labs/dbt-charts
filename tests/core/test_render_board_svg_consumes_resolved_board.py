"""render_board_svg and render_nested_board consume ResolvedBoard.

Verifies:
- render_board_svg() accepts ResolvedBoard (not Board)
- render_nested_board() accepts ResolvedBoard
- renderer.render() calls resolve_board() before render_board_svg()
- render_board_svg() does not call get_theme_style().frame.* directly
- End-to-end SVG renders correctly
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import MagicMock

from ._svg_render import PINNED_RENDER_TIME

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compile_board(yaml: str | None = None) -> Any:
    from dbt_charts.core.compile.compiler import compile

    yaml = (
        yaml
        or """
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
    result = compile(yaml)
    return result.board


def _make_executor() -> Any:
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
# Signature / annotation tests
# ---------------------------------------------------------------------------


class TestSignatures:
    def test_render_board_svg_accepts_resolved_board(self):
        import inspect

        from dbt_charts.core.render.boards import render_board_svg

        sig = inspect.signature(render_board_svg)
        board_param = sig.parameters.get("board")
        assert board_param is not None
        annotation = str(board_param.annotation)
        assert "ResolvedBoard" in annotation, (
            f"render_board_svg.board must be ResolvedBoard, got: {annotation}"
        )

    def test_render_nested_board_accepts_resolved_board(self):
        import inspect

        from dbt_charts.core.render.boards import render_nested_board

        sig = inspect.signature(render_nested_board)
        board_param = sig.parameters.get("board")
        assert board_param is not None
        annotation = str(board_param.annotation)
        assert "ResolvedBoard" in annotation, (
            f"render_nested_board.board must be ResolvedBoard, got: {annotation}"
        )

    def test_renderer_calls_resolve_board_or_build_resolved_board(self):
        """renderer.py must call resolve_board (V1) or build_resolved_board (V2)."""
        import inspect

        from dbt_charts.core.render import renderer as renderer_mod

        src = inspect.getsource(renderer_mod)
        assert "resolve_board" in src or "build_resolved_board" in src

    def test_render_board_svg_no_board_config_reads(self):
        """render_board_svg must not call get_theme_style().frame.* directly."""
        import inspect

        from dbt_charts.core.render.boards import render_board_svg

        src = inspect.getsource(render_board_svg)
        assert "get_theme_style().frame.margin" not in src
        assert "get_theme_style().frame.card_padding" not in src
        assert "get_theme_style().frame.card_gap" not in src
        assert "get_theme_style().frame.max_width" not in src


# ---------------------------------------------------------------------------
# ResolvedBoard shape
# ---------------------------------------------------------------------------


class TestMergedBoardShape:
    def test_visible_variables_property_filters_hidden(self):
        """ResolvedBoard.visible_variables excludes hidden variables."""
        from dbt_charts.core.compile.config import reset_config
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        reset_config()
        board = _compile_board(
            """
id: var-board
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
variables:
  visible_var:
    input: text
    default: hello
  hidden_var:
    input: text
    default: secret
    visible: false
"""
        )
        resolved = resolve_board(board)
        visible = resolved.visible_variables
        assert isinstance(visible, dict)
        assert "visible_var" in visible, "visible_var must appear in visible_variables"
        assert "hidden_var" not in visible, (
            "hidden_var must be excluded by visible_variables"
        )


# ---------------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_render_produces_svg(self):
        from dbt_charts.core.render.renderer import render

        board = _compile_board()
        executor = _make_executor()
        result = render(board, executor, format="svg").output
        assert isinstance(result, str) and "<svg" in result

    def test_render_with_title_produces_svg(self):
        """Title rendering still works after ResolvedBoard migration."""
        from dbt_charts.core.render.renderer import render

        board = _compile_board(
            """
id: titled-board
title: My Dashboard Title
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
        executor = _make_executor()
        result = render(board, executor, format="svg").output
        assert isinstance(result, str) and "<svg" in result

    def test_board_margin_is_the_outer_page_padding(self):
        """style.frame.margin is the sole source of the board's outer padding.

        Every host renders it — there is no host-side override that trims it,
        so what an author writes is what Cloud, dct serve, and exports show.
        Content then starts at margin + card_padding.
        """
        from dbt_charts.core.render.renderer import render

        board = _compile_board(
            """
title: Margin Test
style:
  frame:
    width: 120
    margin: 10
    card_padding: 5
text: Hello
"""
        )
        executor = _make_executor()

        svg = render(board, executor, format="svg").output

        assert isinstance(svg, str)
        # The tagged group's own position plus its padded box's inset
        # translate sum to margin + card_padding — the same absolute start
        # as a single combined translate, just split across the box the
        # authoring tag now carries and the content it wraps.
        from ._svg_render import cumulative_ink_x

        assert cumulative_ink_x(svg, "text", until="style") == 15.0, (
            "content should start at margin 10 + card_padding 5"
        )

    def test_board_margin_grows_the_outer_height(self):
        """total_height carries both page margins on top of the content.

        Differential rather than absolute: an exact pin would have to encode
        theme footer/timestamp values, which dbt-charts/AGENTS.md bans. Both cases
        hold content width fixed at 100 (width - 2 * margin) so the text wraps
        identically and the only moving term is the padding — otherwise a bigger
        margin also narrows content and grows height by rewrapping, which would
        make the delta meaningless. Drop `+ 2 * page_padding` from total_height
        and the two heights come out equal.

        Width is the asymmetric arm: it is already padding-inclusive
        (layout.width is the container), so it tracks frame.width, not content.
        """
        from dbt_charts.core.render.renderer import render

        def _viewbox(width: int, margin: int) -> tuple[float, float]:
            board = _compile_board(
                f"""
title: Margin Test
style:
  frame:
    width: {width}
    margin: {margin}
    card_padding: 5
text: Hello
"""
            )
            svg = render(board, _make_executor(), format="svg").output
            assert isinstance(svg, str)
            box = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
            assert box is not None
            return float(box.group(1)), float(box.group(2))

        narrow_w, narrow_h = _viewbox(120, 10)
        wide_w, wide_h = _viewbox(160, 30)

        assert wide_h - narrow_h == 40.0, "each extra margin px adds 2px of height"
        assert (narrow_w, wide_w) == (120.0, 160.0), "total_width is frame.width"

    def test_nested_board_border_dash_array_emits_svg_dasharray(self):
        """A nested board's own style.border.dash_array reaches its stroked
        border rect (render_nested_board) when authored directly on that
        nested board, not on the root."""
        from dbt_charts.core.render.renderer import render

        board = _compile_board(
            """
id: root-board
title: Root
source: duckdb
charts:
  bar1:
    type: bar
    x: cat
    y: val
    query:
      sql: SELECT 'A' AS cat, 10 AS val
rows:
  - style:
      border:
        width: 2
        color: "#333333"
        radius: 0
        dash_array: [4, 4]
        line_cap: round
    rows:
      - bar1
"""
        )
        executor = _make_executor()
        svg = render(board, executor, format="svg").output
        assert isinstance(svg, str)
        assert 'stroke-dasharray="4,4"' in svg
        assert 'stroke-linecap="round"' in svg

    def test_render_nested_board(self):
        """Nested board layout renders without crash."""
        from dbt_charts.core.render.renderer import render

        board = _compile_board(
            """
id: root-board
title: Root
source: duckdb
charts:
  bar1:
    type: bar
    x: cat
    y: val
    query:
      sql: SELECT 'A' AS cat, 10 AS val
rows:
  - rows:
    - bar1
"""
        )
        executor = _make_executor()
        result = render(board, executor, format="svg").output
        assert isinstance(result, str) and "<svg" in result
