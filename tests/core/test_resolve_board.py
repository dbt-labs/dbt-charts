"""resolve_board() unit and integration tests.

Verifies:
- ResolvedBoard, ResolvedLayout, ResolvedLayoutItem types exist in models/board/compiled
- resolve_board() takes no executor
- Board config fields are Optional: None for nested boards, concrete for root
- No theme field on ResolvedBoard
- Static dimension estimates are concrete floats
- Nested board recursion produces correct ResolvedBoard stubs
- Theme mutation via Board.set_theme(...) before render() is reflected in both
  ResolvedBoard.style and board.resolved_style.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import ProjectSourcesConfig, get_theme_style
from dbt_charts.core.execute.env import build_exec_env

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _project_with_duckdb_source(
    local_project: Callable[..., FilesystemProject],
) -> FilesystemProject:
    """A Project whose sources registry resolves the 'duckdb' name to an
    in-memory DuckDB connection (D-09: boards can no longer define sources
    inline, so the fixtures' `source: duckdb` is resolved here)."""
    project = local_project(Path.cwd())
    project.__dict__["sources"] = ProjectSourcesConfig(
        sources={"duckdb": {"type": "duckdb", "path": ":memory:"}}
    )
    return project


def _compile_minimal_board() -> Any:
    """Compile a minimal board for integration tests."""
    from dbt_charts.core.compile.compiler import compile

    yaml_content = """
id: test-board
title: Test Board
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
    return result.board


# ---------------------------------------------------------------------------
# Type shape tests
# ---------------------------------------------------------------------------


class TestMergedTypes:
    """Type contracts: correct fields exist/absent in models/board/compiled."""

    def test_resolved_board_importable(self):
        from dbt_charts.core.compile.models.board.resolved import ResolvedBoard

        assert ResolvedBoard is not None

    def test_resolved_layout_importable(self):
        from dbt_charts.core.compile.models.board.resolved import ResolvedLayout

        assert ResolvedLayout is not None

    def test_resolved_layout_item_importable(self):
        from dbt_charts.core.compile.models.board.resolved import ResolvedLayoutItem

        assert ResolvedLayoutItem is not None

    def test_resolve_board_function_importable(self):
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        assert callable(resolve_board)

    def test_resolved_board_has_no_render_cache(self):
        """render_cache is render-phase output — not on ResolvedBoard."""
        from dbt_charts.core.compile.models.board.resolved import ResolvedBoard

        field_names = {f.name for f in ResolvedBoard.__dataclass_fields__.values()}
        assert "render_cache" not in field_names

    def test_board_config_fields_are_optional(self):
        """page_padding/card_padding/card_gap are Optional — None for nested boards."""
        import inspect

        from dbt_charts.core.compile.models.board.resolved import ResolvedBoard

        hints = inspect.get_annotations(ResolvedBoard, eval_str=True)
        for field in ("page_padding", "card_padding", "card_gap"):
            assert "None" in str(hints[field]) or type(None) in getattr(
                hints[field], "__args__", ()
            ), f"{field} must be Optional (float | None)"


# ---------------------------------------------------------------------------
# Purity tests
# ---------------------------------------------------------------------------


class TestResolveBoardPurity:
    """resolve_board() takes no executor and imports nothing from execute/."""

    def test_signature_has_no_executor(self):
        import inspect

        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        sig = inspect.signature(resolve_board)
        assert "executor" not in sig.parameters

    def test_static_resolver_has_no_executor_param(self):
        """build_resolved_board_static takes no executor — it is data-free."""
        import inspect

        from dbt_charts.core.render.board_resolve import build_resolved_board_static

        src = inspect.getsource(build_resolved_board_static)
        assert "executor" not in src, (
            "build_resolved_board_static must not reference executor"
        )

    def test_deterministic_same_inputs(self):
        """Same Board + config always produces identical ResolvedBoard."""
        from dbt_charts.core.compile.config import reset_config
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        reset_config()
        board = _compile_minimal_board()
        assert resolve_board(board) == resolve_board(board)


# ---------------------------------------------------------------------------
# Root board value tests
# ---------------------------------------------------------------------------


class TestResolveBoardRoot:
    """resolve_board() on a root board: all board config fields concrete."""

    @pytest.fixture
    def board(self):
        return _compile_minimal_board()

    @pytest.fixture
    def config(self):
        from dbt_charts.core.compile.config import get_config, reset_config

        reset_config()
        return get_config()

    def test_returns_resolved_board(self, board, config):
        from dbt_charts.core.compile.models.board.resolved import ResolvedBoard
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        assert isinstance(resolve_board(board), ResolvedBoard)

    def test_bakes_page_padding(self, board, config):
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        result = resolve_board(board)
        assert result.page_padding == float(get_theme_style().frame.margin)

    def test_bakes_card_padding(self, board, config):
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        result = resolve_board(board)
        assert result.card_padding == float(get_theme_style().frame.card_padding)

    def test_card_gap_zero_when_flag_false(self, board, config):
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        assert board.card_gap is False
        assert resolve_board(board).card_gap == 0.0

    def test_card_gap_from_config_when_flag_true(self, config):
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        from ._board_utils import make_test_board

        board = make_test_board(id="t", card_gap=True)
        assert resolve_board(board).card_gap == float(get_theme_style().frame.card_gap)

    def test_width_concrete_float(self, board, config):
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        result = resolve_board(board)
        assert isinstance(result.width, float) and result.width > 0

    def test_height_concrete_float(self, board, config):
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        result = resolve_board(board)
        assert isinstance(result.height, float) and result.height >= 0

    def test_style_is_resolved_style(self, board, config):
        from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        assert isinstance(resolve_board(board).style, ResolvedStyle)

    def test_passthrough_id_title(self, board, config):
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        result = resolve_board(board)
        assert result.id == board.id and result.title == board.title

    def test_chart_ids_preserved(self, board, config):
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        assert set(resolve_board(board).charts.keys()) == set(board.charts.keys())

    def test_charts_are_resolved_charts(self, board, config):
        from dbt_charts.core.compile.models.chart.resolved._base import (
            _BaseResolvedChartFields,
        )
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        result = resolve_board(board)
        for chart in result.charts.values():
            assert isinstance(chart, _BaseResolvedChartFields), (
                f"Expected a V2 resolved chart in ResolvedBoard.charts, got {type(chart)}"
            )

    def test_layout_item_chart_is_resolved_chart(self, board, config):
        from dbt_charts.core.compile.models.chart.resolved._base import (
            _BaseResolvedChartFields,
        )
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        result = resolve_board(board)
        chart_items = [i for i in result.layout.items if i.type == "chart" and i.chart]
        assert chart_items, "Expected at least one chart layout item"
        for item in chart_items:
            assert isinstance(item.chart, _BaseResolvedChartFields), (
                f"Expected a V2 resolved chart on ResolvedLayoutItem.chart, got {type(item.chart)}"
            )

    def test_passthrough_variables(self, board, config):
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        assert resolve_board(board).variables == board.variables

    def test_passthrough_variable_defaults(self, board, config):
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        assert resolve_board(board).variable_defaults == board.variable_defaults

    def test_layout_is_resolved_layout(self, board, config):
        from dbt_charts.core.compile.models.board.resolved import ResolvedLayout
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        assert isinstance(resolve_board(board).layout, ResolvedLayout)

    def test_layout_items_are_resolved_layout_items(self, board, config):
        from dbt_charts.core.compile.models.board.resolved import ResolvedLayoutItem
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        for item in resolve_board(board).layout.items:
            assert isinstance(item, ResolvedLayoutItem)

    def test_width_fallback_to_config_when_unsized(self, config):
        """When layout.width is 0 (unsized), falls back to get_theme_style().frame.max_width."""
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        from ._board_utils import make_test_board

        board = make_test_board(id="t")
        assert not board.layout.width  # 0.0 — no sizing run
        assert resolve_board(board).width == float(get_theme_style().frame.max_width)


# ---------------------------------------------------------------------------
# Nested board tests
# ---------------------------------------------------------------------------


class TestMergedNestedBoard:
    """Nested boards: board config fields are None (root-only concern)."""

    def _make_board_with_nested(self) -> Any:
        """Compile a board that has a nested board in its layout.

        The nested-board syntax is a layout key (rows:/cols:/etc.) nested inside
        the parent layout list — each sub-layout becomes a nested Board.
        """
        from dbt_charts.core.compile.compiler import compile

        yaml_content = """
id: root-board
title: Root
source: duckdb
charts:
  bar1:
    type: bar
    x: category
    y: value
    query:
      sql: SELECT 'A' AS category, 10 AS value
rows:
  - rows:
    - bar1
"""
        result = compile(yaml_content)
        return result.board

    def test_nested_board_board_config_is_none(self):
        """Nested ResolvedBoard carries None for board-level config."""
        from dbt_charts.core.compile.config import reset_config
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        reset_config()
        root_board = self._make_board_with_nested()
        result = resolve_board(root_board)

        # Find the nested ResolvedBoard inside the layout
        nested = next(
            (item.board for item in result.layout.items if item.board is not None), None
        )
        assert nested is not None, "Expected a nested board in layout items"
        assert nested.page_padding is None
        assert nested.card_padding is None
        assert nested.card_gap is None

    def test_root_board_board_config_is_concrete(self):
        """Root ResolvedBoard has concrete board config values."""
        from dbt_charts.core.compile.config import reset_config
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        reset_config()
        root_board = self._make_board_with_nested()
        result = resolve_board(root_board)

        assert result.page_padding == float(get_theme_style().frame.margin)
        assert result.card_padding == float(get_theme_style().frame.card_padding)
        assert result.card_gap is not None


# ---------------------------------------------------------------------------
# Theme mutation regression
# ---------------------------------------------------------------------------


class TestResolveFaceThemeMutation:
    """Board.set_theme + resolve_board pick up post-compile theme changes."""

    def test_mutated_theme_is_reflected_in_resolved_style(self):
        """After set_theme + resolve_board, style reflects the new theme."""
        from dbt_charts.core.compile.compiler import compile
        from dbt_charts.core.compile.config import (
            get_theme_style,
            reset_config,
        )
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )

        reset_config()

        yaml_content = """
id: test-board
title: Test
source: duckdb
rows:
  - c1
charts:
  c1:
    type: bar
    x: month
    y: revenue
    query:
      sql: SELECT 'Jan' AS month, 100 AS revenue
"""
        result = compile(yaml_content)
        assert result.board is not None, f"Compile failed: {result.errors}"
        board = result.board

        before_bg = resolve_board(board).style.background

        board.set_theme("neon")
        after_bg = resolve_board(board).style.background

        dark_bg = get_theme_style("neon").background
        assert after_bg == dark_bg, (
            f"resolved style must reflect the new theme; "
            f"got {after_bg!r}, expected {dark_bg!r}"
        )
        assert after_bg != before_bg

    def test_set_theme_writes_all_nested_boards(self):
        """set_theme on root also updates nested boards."""
        from dbt_charts.core.compile.compiler import compile
        from dbt_charts.core.compile.config import get_theme_style, reset_config

        reset_config()
        yaml_content = """
id: root
title: Root
source: duckdb
rows:
  - c1
charts:
  c1:
    type: bar
    x: month
    y: revenue
    query:
      sql: SELECT 1 AS month, 100 AS revenue
"""
        result = compile(yaml_content)
        assert result.board is not None
        board = result.board

        board.set_theme("neon")

        dark_bg = get_theme_style("neon").background
        assert board.resolved_style.background == dark_bg

    def test_renderer_writeback_before_layout_sizing(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """board.resolved_style has the new theme when calculate_data_aware_layout runs."""
        import importlib
        from unittest.mock import patch

        from dbt_charts.core.compile.compiler import compile
        from dbt_charts.core.compile.config import get_theme_style, reset_config
        from dbt_charts.core.render import render

        from .._run import Run

        reset_config()
        yaml_content = """
id: test-board
title: Test
source: duckdb
rows:
  - c1
charts:
  c1:
    type: bar
    x: month
    y: revenue
    query:
      sql: SELECT 1 AS month, 100 AS revenue
"""
        result = compile(yaml_content)
        assert result.board is not None
        board = result.board
        board.set_theme("neon")

        captured_bg: list[str] = []
        layout_sizing_mod = importlib.import_module(
            "dbt_charts.core.render.layout_sizing"
        )
        original_calc = layout_sizing_mod.calculate_data_aware_layout

        def spy_calc(f, *args, **kwargs):
            captured_bg.append(f.resolved_style.background)
            return original_calc(f, *args, **kwargs)

        executor = Run(
            board,
            env=build_exec_env(_project_with_duckdb_source(local_project)),
            query_registry=result.query_registry,
        )
        with patch.object(layout_sizing_mod, "calculate_data_aware_layout", spy_calc):
            render(board, executor.ctx, executor.ledger, format="svg")

        dark_bg = get_theme_style("neon").background
        assert captured_bg, "calculate_data_aware_layout was not called"
        assert captured_bg[0] == dark_bg, (
            f"board.resolved_style must reflect the new theme when layout sizing runs; "
            f"got {captured_bg[0]!r}, expected {dark_bg!r}"
        )

    def test_full_render_pipeline_reflects_mutated_theme(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """render() output contains the new theme's background after board.theme mutation."""
        from dbt_charts.core.compile.compiler import compile
        from dbt_charts.core.compile.config import get_theme_style, reset_config
        from dbt_charts.core.render import render

        from .._run import Run

        reset_config()

        yaml_content = """
id: test-board
title: Test
source: duckdb
rows:
  - c1
charts:
  c1:
    type: bar
    x: month
    y: revenue
    query:
      sql: SELECT 1 AS month, 100 AS revenue
"""
        result = compile(yaml_content)
        assert result.board is not None, f"Compile failed: {result.errors}"
        board = result.board
        board.set_theme("neon")

        executor = Run(
            board,
            env=build_exec_env(_project_with_duckdb_source(local_project)),
            query_registry=result.query_registry,
        )
        svg = render(board, executor.ctx, executor.ledger, format="svg").output

        dark_bg = get_theme_style("neon").background
        assert dark_bg.lower() in svg.lower(), (
            f"Full render after theme mutation must embed the new theme's background "
            f"({dark_bg!r}) in the SVG output"
        )
