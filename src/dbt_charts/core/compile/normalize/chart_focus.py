"""Chart focus utilities.

This module provides the `focus_on_chart()` function for rendering a single
chart from a board. It transforms a compiled board to show only one chart
with its dependent variables.

There are two ways to focus on a chart:

1. **YAML field** (recommended for users):
   Add `chart_focus: chart_id` to your board YAML.
   The compiler will automatically apply the focus during normalization.

   ```yaml
   title: Sales Board
   chart_focus: revenue_chart  # Render only this chart
   charts:
     revenue_chart:
       query: sales
       type: bar
   rows:
     - revenue_chart
   ```

2. **Python API** (for programmatic use):
   ```python
   from dbt_charts.core.compile import compile, focus_on_chart

   result = compile(yaml_content)
   focused = focus_on_chart(result.board, "revenue_chart")
   render(focused, executor, format="svg")
   ```

Use cases:
- Chart editing (focus on one chart while editing)
- Export single chart (SVG/PNG/PDF)
- Embed a single chart in another page
"""

from dbt_charts.core.compile.models.board.authored import LayoutType
from dbt_charts.core.compile.models.board.normalized import (
    Board,
    Layout,
    LayoutItem,
)
from dbt_charts.core.compile.sizing import chart_slot_width

# Read by compile/validate/board_warnings.py to judge WARN-UNREFERENCED-CHART.
PRE_FOCUS_PLACED_CHARTS_META_KEY = "pre_focus_placed_chart_ids"


def focus_on_chart(board: Board, chart_id: str) -> Board:
    """Transform a compiled board to focus on a single chart.

    Creates a new board with:
    - Only the specified chart in the layout
    - Only the variables that chart depends on
    - The chart pinned at the slot width the dashboard layout assigned it,
      so the focused render is geometry-identical to the dashboard (WYSIWYG);
      a chart not placed in the layout keeps its preferred width
    - All sources and queries unchanged (they're lazy-loaded anyway)

    WYSIWYG covers geometry, not color. Narrowing the layout happens before
    board-wide category colors are planned, so a focused chart falls under the
    two-chart threshold and colors itself locally — it can differ from the same
    chart in the dashboard. Planning over the unfocused layout would mean
    executing the sibling queries focusing exists to skip.

    PINNED values are the exception, and the board's pins ride along for it:
    an authored field bypasses the threshold, and a pin addresses a slot, so
    the pinned value lands on the same swatch whether or not its siblings were
    executed. Wiping the pins here would make the one case that costs nothing
    diverge too.

    This is called automatically when `chart_focus` is set in the YAML.
    It can also be called directly for programmatic chart focusing.

    Args:
        board: The compiled board containing the chart
        chart_id: The ID of the chart to focus on

    Returns:
        A new Board with simplified layout and filtered variables

    Raises:
        ValueError: If the chart is not found

    Example:
        >>> result = compile(yaml_content)
        >>> focused = focus_on_chart(result.board, "revenue_chart")
        >>> render(focused, executor, format="svg")
    """
    # Find the chart - all charts (including inline) are now in board.charts
    chart = board.charts.get(chart_id)
    if chart is None:
        available_charts = (
            ", ".join(sorted(board.charts.keys())) if board.charts else "none"
        )
        raise ValueError(
            f"Chart '{chart_id}' not found in board. Available charts: {available_charts}"
        )

    # Filter variables to only those the chart needs.
    # variable_dependencies is declared on every Chart family.
    focused_variables = {
        name: var
        for name, var in board.variables.items()
        if name in chart.variable_dependencies
    }

    # The layout as written, before focus narrows it to one chart.
    pre_focus_placed = sorted(c.id for c in board.placed_charts() if c.id)

    # Pin the dashboard slot width so the focused chart keeps the exact
    # geometry it had on the board (band widths, axis-label fit). The board
    # container hugs the pinned width the same way it hugs preferred widths.
    slot_width = chart_slot_width(board, chart_id)
    chart_item = LayoutItem(
        type="chart",
        chart=chart,
        user_width=None if slot_width is None else f"{slot_width}px",
    )
    simple_layout = Layout(type=LayoutType.ROWS, items=[chart_item])

    # Create new board with focused content.
    # No title - this is a focused view, not a board. The focused container is
    # the pinned slot plus margins — an exact width, so a slot wider than
    # max_width is never clamped down and a slot narrower than the board's own
    # width never inherits a full-board canvas. An unplaced chart has no slot
    # to pin, so its board drops any exact width and hugs the chart's
    # preferred width instead. The style cascade re-runs on the edited patch.
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.normalize.dispatch import compile_board_resolved_style

    authored = board.authored_style
    patch_data = authored.model_dump(exclude_unset=True) if authored is not None else {}
    frame_data = dict(patch_data["frame"]) if "frame" in patch_data else {}
    if slot_width is None:
        frame_data.pop("width", None)
    else:
        frame_data["width"] = slot_width + 2 * float(board.resolved_style.frame.margin)
    if frame_data:
        patch_data["frame"] = frame_data
    else:
        patch_data.pop("frame", None)
    authored = StylePatch.model_validate(patch_data) if patch_data else None
    resolved_style, chart_style_context, _ = compile_board_resolved_style(
        authored, None, None, theme_name=board.theme
    )
    focused_board = board.model_copy(
        update={
            "variables": focused_variables,
            "layout": simple_layout,
            "title": "",
            "authored_style": authored,
            "resolved_style": resolved_style,
            "chart_style_context": chart_style_context,
            "meta": {**board.meta, PRE_FOCUS_PLACED_CHARTS_META_KEY: pre_focus_placed},
        }
    )

    return focused_board
