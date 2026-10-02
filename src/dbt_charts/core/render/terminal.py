"""Terminal rendering module.

Stage: RENDER
Purpose: Render compiled boards to terminal output.

This module provides terminal rendering functionality using:
- Plotext for charts
- Rich for tables and text formatting
- Custom layout rendering for terminal constraints

Entry Points:
    - render_board_terminal(board, executor, variables, **options) -> str
    - render_chart_terminal(chart, data, display_title, **options) -> str
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.models.board.normalized import (
    Board,
    Layout,
    LayoutItem,
    VariableValues,
)
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.primitives import FormatAliases
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.conditions import evaluate_visible

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle


def _dispatch_layout(
    layout: Layout,
    executor: Executor,
    variables: dict[str, Any],
    width: int,
    height: int,
    gap: int,
    background: str | None,
    grid_columns: int,
    resolved_style: ResolvedStyle,
) -> str:
    """Dispatch to the correct terminal layout renderer based on layout type."""
    from dbt_charts.core.render.terminal_layouts import (
        render_cols_layout_terminal,
        render_grid_layout_terminal,
        render_rows_layout_terminal,
        render_tabs_layout_terminal,
    )

    if layout.type == "cols":
        return render_cols_layout_terminal(
            layout.items,
            executor,
            variables,
            width,
            height,
            gap=gap,
            background=background,
            resolved_style=resolved_style,
        )
    if layout.type == "grid":
        return render_grid_layout_terminal(
            layout.items,
            executor,
            variables,
            width,
            height,
            columns=layout.columns or grid_columns,
            gap=gap,
            background=background,
            resolved_style=resolved_style,
        )
    if layout.type == "tabs":
        return render_tabs_layout_terminal(
            layout.items,
            executor,
            variables,
            width,
            height,
            layout.tab_titles,
            layout.default_tab or 0,
            layout.tab_position or "top",
            background,
            resolved_style=resolved_style,
        )
    # "rows" or any unknown type — fall back to rows
    return render_rows_layout_terminal(
        layout.items,
        executor,
        variables,
        width,
        height,
        gap=gap,
        background=background,
        resolved_style=resolved_style,
    )


def render_board_terminal(
    board: Board,
    executor: Executor,
    variables: VariableValues | None = None,
    width: int | None = None,
    height: int | None = None,
    colors: bool = True,
    *,
    resolved_style: ResolvedStyle,
    **options: Any,
) -> str:
    """Render a compiled board to terminal output.

    Args:
        board: Compiled board to render
        executor: Executor for query execution
        variables: Variable values for queries
        width: Terminal width in characters (auto-detect if None)
        height: Terminal height in characters (auto-detect if None)
        colors: Whether to use ANSI colors
        resolved_style: Board-scoped ResolvedStyle; required for style-aware rendering.
        **options: Additional rendering options

    Returns:
        Terminal-formatted board string
    """
    # Auto-detect terminal size if not provided
    if width is None or height is None:
        try:
            term_size = shutil.get_terminal_size()
            width = width or term_size.columns
            height = height or term_size.lines
        except (OSError, ValueError):
            # shutil.get_terminal_size() can fail in non-TTY environments (CI, pipes, etc.)
            # Fallback to standard terminal size
            width = width or 80
            height = height or 24

    # Trust the normalizer - use pre-computed variable_defaults
    variable_registry = board.variable_registry or {}

    # Merge variables: start with None for all vars, then defaults, then user values
    all_variables: dict[str, Any] = dict.fromkeys(variable_registry)
    all_variables.update(board.variable_defaults)  # Pre-computed by normalizer
    if variables:
        all_variables.update(variables)

    grid_columns = resolved_style.layout.grid.columns

    board_width = width - 4  # Account for padding
    board_height = height - 4

    # Render title if present
    title_lines: list[str] = []
    title_height = 0
    if board.title:
        from dbt_charts.core.compile.template.jinja import resolve_jinja_template

        resolved_title = resolve_jinja_template(board.title, all_variables)

        # Format title with border
        title_lines.append("=" * board_width)
        title_lines.append(f" {resolved_title}")
        title_lines.append("=" * board_width)
        title_lines.append("")
        title_height = len(title_lines)

    # Calculate layout area
    layout_width = board_width
    layout_height = board_height - title_height

    bg = board.authored_style.background if board.authored_style else None
    layout_output = _dispatch_layout(
        board.layout,
        executor,
        all_variables,
        layout_width,
        layout_height,
        gap=1,
        background=bg,
        grid_columns=grid_columns,
        resolved_style=resolved_style,
    )

    # Combine title and layout
    output_lines = title_lines + [layout_output]

    return "\n".join(output_lines)


def render_layout_item_terminal(
    item: LayoutItem,
    executor: Executor,
    variables: VariableValues,
    available_width: int,
    available_height: int,
    *,
    resolved_style: ResolvedStyle,
) -> str:
    """Render a single layout item (chart or nested board) to terminal.

    Args:
        item: Layout item to render
        executor: Executor for query execution
        variables: Variable values for queries
        available_width: Available terminal width in characters
        available_height: Available terminal height in characters
        resolved_style: Board-scoped style for grid column config.

    Returns:
        Terminal-formatted string for the item
    """
    if not evaluate_visible(item.visible, variables, executor):
        return ""
    if item.type == "chart" and item.chart:
        return render_chart_item_terminal(
            item.chart,
            executor,
            variables,
            available_width,
            available_height,
            formats=resolved_style.chart_defaults.formats,
        )
    elif item.type == "board" and item.board:
        return render_nested_board_terminal(
            item.board,
            executor,
            variables,
            available_width,
            available_height,
            resolved_style=resolved_style,
        )
    return ""


def render_chart_item_terminal(
    chart: Chart,
    executor: Executor,
    variables: VariableValues,
    available_width: int,
    available_height: int,
    *,
    formats: FormatAliases | None,
) -> str:
    """Render a chart item to terminal.

    Args:
        chart: Chart to render
        executor: Executor for query execution
        variables: Variable values for queries
        available_width: Available terminal width in characters
        available_height: Available terminal height in characters
        formats: Board-level style.formats alias map (ResolvedStyle.formats).

    Returns:
        Terminal-formatted chart string
    """
    try:
        # Execute query to get data
        data = executor.execute_chart(chart, variables)

        from dbt_charts.core.compile.resolve import resolve_chart_display_title

        display_title = resolve_chart_display_title(chart, variables)

        # Render chart
        from dbt_charts.core.render.terminal_charts import render_chart_terminal

        return render_chart_terminal(
            chart,
            data,
            display_title,
            width=available_width,
            height=available_height,
            colors=True,
            formats=formats,
        )

    except (ValueError, KeyError, TypeError, AttributeError, DbtChartsError) as e:
        # DbtChartsError joins the four bug-class types here rather than
        # replacing them. The SVG path deliberately does the opposite — it lets
        # bug-class exceptions surface loudly — but terminal is a debug/preview
        # surface where an inline "[Error rendering …]" line beside the rest of
        # the board beats a crash that takes the whole board with it. Do not
        # narrow this back down to match render_chart_item.
        error_msg = str(e)[:200]  # Show more of the error for debugging
        return f"[Error rendering {chart.id}: {error_msg}]"


def render_nested_board_terminal(
    board: Board,
    executor: Executor,
    variables: VariableValues,
    available_width: int,
    available_height: int,
    *,
    resolved_style: ResolvedStyle,
) -> str:
    """Render a nested board to terminal.

    Args:
        board: Board to render
        executor: Executor for query execution
        variables: Variable values for queries
        available_width: Available terminal width in characters
        available_height: Available terminal height in characters
        resolved_style: Board-scoped style for grid column config.

    Returns:
        Terminal-formatted board string
    """
    title_lines: list[str] = []
    if board.title:
        from dbt_charts.core.compile.template.jinja import resolve_jinja_template

        resolved_title = resolve_jinja_template(board.title, variables)
        title_lines.append(f"─ {resolved_title} ─")
        title_lines.append("")

    title_height = len(title_lines)
    layout_height = available_height - title_height

    bg = board.authored_style.background if board.authored_style else None
    layout_output = _dispatch_layout(
        board.layout,
        executor,
        variables,
        available_width,
        layout_height,
        gap=1,
        background=bg,
        grid_columns=resolved_style.layout.grid.columns,
        resolved_style=resolved_style,
    )

    return "\n".join(title_lines + [layout_output])
