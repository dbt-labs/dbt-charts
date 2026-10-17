"""Terminal layout rendering functions.

Stage: RENDER
Purpose: Render different layout types (rows, cols, grid, tabs) to terminal output.

This module provides functions to render layouts in terminal-friendly format:
- Rows: Vertical stacking
- Cols: Horizontal distribution (if terminal width allows)
- Grid: Character-based grid layout
- Tabs: Text-based tabs
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dbt_charts.core.compile.models.board.normalized import (
    LayoutItem,
    VariableValues,
)
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.terminal_text import (
    pad_visible,
    truncate_visible,
    visible_len,
)
from dbt_charts.core.text.format_d3 import board_word

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle


def _layout_item_is_table(item: LayoutItem) -> bool:
    if item.type != "chart":
        return False
    assert item.chart is not None  # normalizer: chart layout items always carry chart
    return item.chart.type == "table"


def _cols_should_stack_vertically(items: list[LayoutItem], col_width: int) -> bool:
    if col_width < 50:
        return True
    return any(_layout_item_is_table(item) for item in items)


def render_rows_layout_terminal(
    items: list[LayoutItem],
    executor: Executor,
    variables: VariableValues,
    available_width: int,
    available_height: int,
    gap: int = 1,
    background: str | None = None,
    *,
    resolved_style: ResolvedStyle,
) -> str:
    """Render items in vertical stack for terminal.

    Args:
        items: Layout items to render
        executor: Executor for query execution
        variables: Variable values for queries
        available_width: Available terminal width in characters
        available_height: Available terminal height in characters
        gap: Gap between items (in lines)
        background: Optional background color (not used in terminal)
        resolved_style: Board-scoped style for grid column config.

    Returns:
        Terminal-formatted string for rows layout
    """
    from dbt_charts.core.render.terminal import render_layout_item_terminal

    if not items:
        return ""

    rendered_items: list[str] = []
    for item in items:
        item_output = render_layout_item_terminal(
            item,
            executor,
            variables,
            available_width,
            available_height,
            resolved_style=resolved_style,
        )
        if item_output:
            rendered_items.append(item_output)
            # Add gap between items
            if gap > 0:
                rendered_items.append("")

    return "\n".join(rendered_items)


def render_cols_layout_terminal(
    items: list[LayoutItem],
    executor: Executor,
    variables: VariableValues,
    available_width: int,
    available_height: int,
    gap: int = 2,
    background: str | None = None,
    *,
    resolved_style: ResolvedStyle,
) -> str:
    """Render items side-by-side for terminal.

    Note: Terminal columns are limited by width. If items don't fit,
    we fall back to vertical stacking.

    Args:
        items: Layout items to render
        executor: Executor for query execution
        variables: Variable values for queries
        available_width: Available terminal width in characters
        available_height: Available terminal height in characters
        gap: Gap between items (in characters)
        background: Optional background color (not used in terminal)
        resolved_style: Board-scoped style for grid column config.

    Returns:
        Terminal-formatted string for columns layout
    """
    from dbt_charts.core.render.terminal import render_layout_item_terminal

    if not items:
        return ""

    # Calculate column width
    n = len(items)
    total_gap = gap * (n - 1)
    col_width = (available_width - total_gap) // n

    if _cols_should_stack_vertically(items, col_width):
        return render_rows_layout_terminal(
            items,
            executor,
            variables,
            available_width,
            available_height,
            gap,
            resolved_style=resolved_style,
        )

    # Render each item in its column
    rendered_items: list[list[str]] = []
    max_lines = 0

    for item in items:
        item_output = render_layout_item_terminal(
            item,
            executor,
            variables,
            col_width,
            available_height,
            resolved_style=resolved_style,
        )
        lines = item_output.split("\n")
        rendered_items.append(lines)
        max_lines = max(max_lines, len(lines))

    # Combine columns horizontally
    output_lines: list[str] = []
    for line_idx in range(max_lines):
        line_parts = []
        for item_lines in rendered_items:
            if line_idx < len(item_lines):
                line = item_lines[line_idx]
                if visible_len(line) > col_width:
                    line = truncate_visible(line, col_width)
                line_parts.append(pad_visible(line, col_width))
            else:
                line_parts.append(" " * col_width)

        output_lines.append((" " * gap).join(line_parts))

    return "\n".join(output_lines)


def render_grid_layout_terminal(
    items: list[LayoutItem],
    executor: Executor,
    variables: VariableValues,
    available_width: int,
    available_height: int,
    columns: int = 2,
    gap: int = 1,
    background: str | None = None,
    *,
    resolved_style: ResolvedStyle,
) -> str:
    """Render items in a grid layout for terminal.

    Args:
        items: Layout items to render
        executor: Executor for query execution
        variables: Variable values for queries
        available_width: Available terminal width in characters
        available_height: Available terminal height in characters
        columns: Number of columns in grid
        gap: Gap between items (in characters/lines)
        background: Optional background color (not used in terminal)
        resolved_style: Board-scoped style for grid column config.

    Returns:
        Terminal-formatted string for grid layout
    """
    from dbt_charts.core.render.terminal import render_layout_item_terminal

    if not items:
        return ""

    # Calculate cell dimensions
    total_gap = gap * (columns - 1)
    cell_width = (available_width - total_gap) // columns

    # Group items into rows
    rows: list[list[LayoutItem]] = []
    current_row: list[LayoutItem] = []
    for item in items:
        current_row.append(item)
        if len(current_row) >= columns:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)

    # Render each row
    rendered_rows: list[str] = []
    for row_items in rows:
        # Render items in this row
        row_rendered: list[list[str]] = []
        max_lines = 0

        for item in row_items:
            item_output = render_layout_item_terminal(
                item,
                executor,
                variables,
                cell_width,
                available_height,
                resolved_style=resolved_style,
            )
            lines = item_output.split("\n")
            row_rendered.append(lines)
            max_lines = max(max_lines, len(lines))

        # Combine row items horizontally
        row_lines: list[str] = []
        for line_idx in range(max_lines):
            line_parts = []
            for item_lines in row_rendered:
                if line_idx < len(item_lines):
                    line = item_lines[line_idx]
                    if visible_len(line) > cell_width:
                        line = truncate_visible(line, cell_width)
                    line_parts.append(pad_visible(line, cell_width))
                else:
                    line_parts.append(" " * cell_width)

            row_lines.append((" " * gap).join(line_parts))

        rendered_rows.append("\n".join(row_lines))
        if gap > 0:
            rendered_rows.append("")  # Gap between rows

    return "\n".join(rendered_rows)


def render_tabs_layout_terminal(
    items: list[LayoutItem],
    executor: Executor,
    variables: VariableValues,
    available_width: int,
    available_height: int,
    tab_titles: list[str] | None = None,
    default_tab: int = 0,
    tab_position: str = "top",
    background: str | None = None,
    *,
    resolved_style: ResolvedStyle,
) -> str:
    """Render tabs layout for terminal.

    Note: For terminal, we render all tabs sequentially with headers,
    since terminal doesn't support interactive tabs.

    Args:
        items: Layout items (one per tab)
        executor: Executor for query execution
        variables: Variable values for queries
        available_width: Available terminal width in characters
        available_height: Available terminal height in characters
        tab_titles: Optional list of tab titles
        default_tab: Default active tab (not used in terminal)
        tab_position: Tab position ("top" or "bottom", not used in terminal)
        background: Optional background color (not used in terminal)
        resolved_style: Board-scoped style for grid column config.

    Returns:
        Terminal-formatted string for tabs layout
    """
    from dbt_charts.core.render.terminal import render_layout_item_terminal

    if not items:
        return ""

    output_lines: list[str] = []

    for idx, item in enumerate(items):
        # Add tab header
        tab_title = (
            tab_titles[idx]
            if tab_titles and idx < len(tab_titles)
            else board_word("tab.title", n=str(idx + 1))
        )

        # Create header line
        header_line = f"┌─ {tab_title} {'─' * (available_width - len(tab_title) - 5)}┐"
        output_lines.append(header_line)

        # Render tab content
        item_output = render_layout_item_terminal(
            item,
            executor,
            variables,
            available_width - 4,
            available_height,
            resolved_style=resolved_style,
        )

        # Indent content
        content_lines = item_output.split("\n")
        for line in content_lines:
            output_lines.append(f"│ {line.ljust(available_width - 4)} │")

        # Add footer
        footer_line = f"└{'─' * (available_width - 2)}┘"
        output_lines.append(footer_line)
        output_lines.append("")  # Gap between tabs

    return "\n".join(output_lines)
