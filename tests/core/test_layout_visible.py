"""Tests for layout-item and chart-level visible field.

TDD: these tests are written BEFORE the implementation.

Layout items support:
  visible: <variable_name>           — bool/truthy variable
  visible: "expr_a and expr_b"       — Jinja boolean expression
  visible: {query: q, column: col}   — single-row boolean probe query
  visible: false                     — always hidden

This is distinct from variables.visible (control-bar only).
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.errors import JinjaError
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.layouts import render_rows_layout


def _rs():
    return resolve_style(get_theme_style())


def _executor(local_project: Callable[..., FilesystemProject], board, query_registry):
    return Executor(
        board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=query_registry,
    )


def _render_rows(
    local_project: Callable[..., FilesystemProject],
    board,
    query_registry,
    variables,
    *,
    width=800.0,
):
    from dbt_charts.core.compile.config import reset_config
    from dbt_charts.core.render.board_resolve import (
        build_resolved_board_static as resolve_board,
    )

    executor = _executor(local_project, board, query_registry)
    reset_config()
    resolved_board = resolve_board(board)
    return render_rows_layout(
        resolved_board.layout.items,
        executor,
        variables,
        width,
        600.0,
        0.0,
        8.0,
        resolved_style=_rs(),
        render_cache={},
        painted_canvas=None,
    )


class TestLayoutVisibleVariableName:
    """visible: <var_name> — boolean variable drives item presence."""

    def test_item_absent_when_variable_false(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Layout row with visible: show_panel absent when variable is false."""
        yaml_content = """
title: Test
queries:
  kpi_q:
    rows:
      - {value: 42}

variables:
  show_panel:
    input: checkbox
    default: false

rows:
  - visible: show_panel
    rows:
      - kpi_a
  - kpi_b

charts:
  kpi_a:
    query: kpi_q
    type: kpi
    value: value
  kpi_b:
    query: kpi_q
    type: kpi
    value: value
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        _, height_hidden = _render_rows(
            local_project, result.board, result.query_registry, {"show_panel": False}
        )
        _, height_shown = _render_rows(
            local_project, result.board, result.query_registry, {"show_panel": True}
        )

        assert height_hidden < height_shown, (
            f"Height when hidden ({height_hidden}) should be less than when shown ({height_shown})"
        )

    def test_item_present_when_variable_true(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Layout row with visible: show_panel rendered when variable is true."""
        yaml_content = """
title: Test
queries:
  kpi_q:
    rows:
      - {value: 99}

variables:
  show_panel:
    input: checkbox
    default: false

rows:
  - kpi_a
  - visible: show_panel
    rows:
      - kpi_b

charts:
  kpi_a:
    query: kpi_q
    type: kpi
    value: value
  kpi_b:
    query: kpi_q
    type: kpi
    value: value
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        _, height_hidden = _render_rows(
            local_project, result.board, result.query_registry, {"show_panel": False}
        )
        _, height_shown = _render_rows(
            local_project, result.board, result.query_registry, {"show_panel": True}
        )

        assert height_shown > height_hidden

    def test_absent_variable_raises(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """visible: show_panel raises when show_panel is not in current_values.

        resolve_jinja_template uses StrictUndefined — absent variables are an
        error, not silently falsy. Authors must give the variable a default.
        """
        yaml_content = """
title: Test
queries:
  kpi_q:
    rows:
      - {value: 42}

rows:
  - visible: show_panel
    rows:
      - kpi_a
  - kpi_b

charts:
  kpi_a:
    query: kpi_q
    type: kpi
    value: value
  kpi_b:
    query: kpi_q
    type: kpi
    value: value
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        with pytest.raises(JinjaError, match="show_panel"):
            _render_rows(local_project, result.board, result.query_registry, {})


class TestLayoutVisibleJinjaExpression:
    """visible: "expr" — Jinja boolean expression."""

    def test_jinja_expression_false(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """visible: 'a and b' hides item when either variable is false."""
        yaml_content = """
title: Test
queries:
  kpi_q:
    rows:
      - {value: 1}

variables:
  a:
    input: checkbox
    default: true
  b:
    input: checkbox
    default: false

rows:
  - kpi_always
  - visible: "a and b"
    rows:
      - kpi_conditional

charts:
  kpi_always:
    query: kpi_q
    type: kpi
    value: value
  kpi_conditional:
    query: kpi_q
    type: kpi
    value: value
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        _, height_hidden = _render_rows(
            local_project, result.board, result.query_registry, {"a": True, "b": False}
        )
        _, height_shown = _render_rows(
            local_project, result.board, result.query_registry, {"a": True, "b": True}
        )

        assert height_shown > height_hidden

    def test_jinja_expression_equality(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """visible: \"mood == 'happy'\" hides when mood is sad."""
        yaml_content = """
title: Test
queries:
  kpi_q:
    rows:
      - {value: 1}

variables:
  mood:
    input: select
    options:
      static: [happy, sad]
    default: happy

rows:
  - kpi_always
  - visible: "mood == 'happy'"
    rows:
      - kpi_happy

charts:
  kpi_always:
    query: kpi_q
    type: kpi
    value: value
  kpi_happy:
    query: kpi_q
    type: kpi
    value: value
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        _, height_sad = _render_rows(
            local_project, result.board, result.query_registry, {"mood": "sad"}
        )
        _, height_happy = _render_rows(
            local_project, result.board, result.query_registry, {"mood": "happy"}
        )

        assert height_happy > height_sad


class TestLayoutVisibleQueryColumn:
    """visible: {query: q, column: col} — single-row boolean probe."""

    def test_probe_query_false_hides_item(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """visible: {query: flags, column: show_warm} false → smaller height."""
        yaml_false = """
title: Test
queries:
  kpi_q:
    rows:
      - {value: 1}
  flags:
    rows:
      - {show_warm: false}

rows:
  - kpi_base
  - visible:
      query: flags
      column: show_warm
    rows:
      - kpi_conditional

charts:
  kpi_base:
    query: kpi_q
    type: kpi
    value: value
  kpi_conditional:
    query: kpi_q
    type: kpi
    value: value
"""
        yaml_true = """
title: Test
queries:
  kpi_q:
    rows:
      - {value: 1}
  flags:
    rows:
      - {show_warm: true}

rows:
  - kpi_base
  - visible:
      query: flags
      column: show_warm
    rows:
      - kpi_conditional

charts:
  kpi_base:
    query: kpi_q
    type: kpi
    value: value
  kpi_conditional:
    query: kpi_q
    type: kpi
    value: value
"""
        result_false = compile(yaml_false)
        assert result_false.success, f"Compile failed: {result_false.errors}"
        result_true = compile(yaml_true)
        assert result_true.success, f"Compile failed: {result_true.errors}"

        _, height_false = _render_rows(
            local_project, result_false.board, result_false.query_registry, {}
        )
        _, height_true = _render_rows(
            local_project, result_true.board, result_true.query_registry, {}
        )

        assert height_true > height_false

    def test_probe_query_wrong_row_count_raises(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """visible probe query returning multiple rows raises ValueError."""
        yaml_content = """
title: Test
queries:
  kpi_q:
    rows:
      - {value: 1}
  bad_flags:
    rows:
      - {show_warm: true}
      - {show_warm: false}

rows:
  - visible:
      query: bad_flags
      column: show_warm
    rows:
      - kpi_a

charts:
  kpi_a:
    query: kpi_q
    type: kpi
    value: value
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        with pytest.raises(ValueError, match="exactly 1"):
            _render_rows(local_project, result.board, result.query_registry, {})

    def test_probe_query_missing_column_raises(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """visible probe query with missing column name raises ValueError."""
        yaml_content = """
title: Test
queries:
  kpi_q:
    rows:
      - {value: 1}
  flags:
    rows:
      - {other_col: true}

rows:
  - visible:
      query: flags
      column: show_warm
    rows:
      - kpi_a

charts:
  kpi_a:
    query: kpi_q
    type: kpi
    value: value
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        with pytest.raises(ValueError, match="show_warm"):
            _render_rows(local_project, result.board, result.query_registry, {})


class TestLayoutVisibleBoolLiteral:
    """visible: false/true — static boolean."""

    def test_visible_false_always_hidden(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Layout item with visible: false is never rendered regardless of variables."""
        yaml_one = """
title: Test
queries:
  kpi_q:
    rows:
      - {value: 42}

rows:
  - kpi_shown
  - visible: false
    rows:
      - kpi_hidden

charts:
  kpi_shown:
    query: kpi_q
    type: kpi
    value: value
  kpi_hidden:
    query: kpi_q
    type: kpi
    value: value
"""
        yaml_two = """
title: Test
queries:
  kpi_q:
    rows:
      - {value: 42}

rows:
  - kpi_shown
  - kpi_hidden

charts:
  kpi_shown:
    query: kpi_q
    type: kpi
    value: value
  kpi_hidden:
    query: kpi_q
    type: kpi
    value: value
"""
        result_one = compile(yaml_one)
        assert result_one.success, f"Compile failed: {result_one.errors}"
        result_two = compile(yaml_two)
        assert result_two.success, f"Compile failed: {result_two.errors}"

        _, height_one = _render_rows(
            local_project, result_one.board, result_one.query_registry, {}
        )
        _, height_two = _render_rows(
            local_project, result_two.board, result_two.query_registry, {}
        )

        assert height_one < height_two

    def test_visible_true_always_shown(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Layout item with visible: true renders normally."""
        yaml_content = """
title: Test
queries:
  kpi_q:
    rows:
      - {value: 42}

rows:
  - visible: true
    rows:
      - kpi_shown

charts:
  kpi_shown:
    query: kpi_q
    type: kpi
    value: value
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        _, height = _render_rows(local_project, result.board, result.query_registry, {})
        assert height > 0

    def test_visible_false_on_inline_chart(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """visible: false on an inline chart item hides only that chart."""
        hidden_yaml = """
title: Test
queries:
  kpi_q:
    rows:
      - {value: 42}

rows:
  - query: kpi_q
    type: kpi
    value: value
    visible: false

  - kpi_b

charts:
  kpi_b:
    query: kpi_q
    type: kpi
    value: value
"""
        shown_yaml = """
title: Test
queries:
  kpi_q:
    rows:
      - {value: 42}

rows:
  - query: kpi_q
    type: kpi
    value: value

  - kpi_b

charts:
  kpi_b:
    query: kpi_q
    type: kpi
    value: value
"""
        r_hidden = compile(hidden_yaml)
        assert r_hidden.success, f"Compile failed: {r_hidden.errors}"
        r_shown = compile(shown_yaml)
        assert r_shown.success, f"Compile failed: {r_shown.errors}"

        _, height_hidden = _render_rows(
            local_project, r_hidden.board, r_hidden.query_registry, {}
        )
        _, height_shown = _render_rows(
            local_project, r_shown.board, r_shown.query_registry, {}
        )

        # hidden board has one fewer rendered item → shorter
        assert height_hidden < height_shown


class TestTerminalRendererVisible:
    """visible: false items must be omitted from terminal output too."""

    def test_terminal_renderer_respects_visible_false(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """render_layout_item_terminal returns '' for items with visible: false."""
        from dbt_charts.core.render.terminal import render_layout_item_terminal

        yaml_content = """
title: Test
queries:
  kpi_q:
    rows:
      - {value: 42}

rows:
  - visible: false
    rows:
      - kpi_hidden
  - kpi_shown

charts:
  kpi_hidden:
    query: kpi_q
    type: kpi
    value: value
  kpi_shown:
    query: kpi_q
    type: kpi
    value: value
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        executor = _executor(local_project, result.board, result.query_registry)
        hidden_item = result.board.layout.items[0]
        shown_item = result.board.layout.items[1]

        from dbt_charts.core.compile.resolve.style.board import resolve_style

        style = resolve_style(get_theme_style())
        hidden_output = render_layout_item_terminal(
            hidden_item, executor, {}, 80, 24, resolved_style=style
        )
        shown_output = render_layout_item_terminal(
            shown_item, executor, {}, 80, 24, resolved_style=style
        )

        assert hidden_output == "", (
            f"Expected empty output for hidden item, got: {hidden_output!r}"
        )
        assert shown_output != "", "Expected non-empty output for shown item"
