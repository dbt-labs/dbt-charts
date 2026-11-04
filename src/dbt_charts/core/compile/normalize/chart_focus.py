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
from dbt_charts.core.compile.normalize.chart_styles import attach_chart_styles
from dbt_charts.core.compile.normalize.query_references import (
    referenced_query_names,
)
from dbt_charts.core.diagnostics import ERR_UNKNOWN_CHART
from dbt_charts.core.diagnostics.base import DbtChartsError

# Read by compile/validate/board_warnings.py to judge WARN-UNREFERENCED-CHART.
PRE_FOCUS_PLACED_CHARTS_META_KEY = "pre_focus_placed_chart_ids"
# Read by board_warnings.py to judge WARN-UNREFERENCED-QUERY.
PRE_FOCUS_REFERENCED_QUERIES_META_KEY = "pre_focus_referenced_query_names"


def focus_on_chart(board: Board, chart_id: str) -> Board:
    """Transform a compiled board to focus on a single chart.

    Creates a new board with:
    - Only the specified chart in the layout
    - Only the variables that chart depends on
    - The chart at its own intrinsic width, exactly as if it were the only
      chart on a board with no width: its authored width, its preferred width,
      or, for an unpinned table, its width measured from the data. The slot it
      had on the source board and that board's authored ``width:`` do not
      carry over.
    - All sources and queries unchanged (they're lazy-loaded anyway)

    Focus does not preserve color. Narrowing the layout happens before
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
        DbtChartsError: ERR-UNKNOWN-CHART if the board has no such chart

    Example:
        >>> result = compile(yaml_content)
        >>> focused = focus_on_chart(result.board, "revenue_chart")
        >>> render(focused, executor, format="svg")
    """
    # Find the chart - all charts (including inline) are now in board.charts
    chart = board.charts.get(chart_id)
    if chart is None:
        raise DbtChartsError.from_code(
            ERR_UNKNOWN_CHART, chart=chart_id, available=sorted(board.charts)
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

    chart_item = LayoutItem(type="chart", chart=chart)
    simple_layout = Layout(type=LayoutType.ROWS, items=[chart_item])

    # Create new board with focused content.
    # No title - this is a focused view, not a board. The source board's
    # authored width belongs to its layout, so the focused board has none and
    # hugs the chart's own width.
    focused_board = board.model_copy(
        update={
            "variables": focused_variables,
            "layout": simple_layout,
            "title": "",
            "authored_width": None,
            "meta": {
                **board.meta,
                PRE_FOCUS_PLACED_CHARTS_META_KEY: pre_focus_placed,
                PRE_FOCUS_REFERENCED_QUERIES_META_KEY: sorted(
                    referenced_query_names(board)
                ),
            },
        }
    )

    attach_chart_styles(focused_board)
    return focused_board


def focus_on_item(board: Board, index: int) -> Board:
    """*board* narrowed to its top-level layout item at *index*, drawn alone.

    The item keeps reading every query and chart the rest of the board names,
    since names are board-global; only the layout narrows. Like
    ``focus_on_chart``, the result has no title and no authored width, and
    keeps only the variables its charts read.
    """
    item = board.layout.items[index]
    if item.chart is not None:
        charts = [item.chart]
    else:
        assert item.board is not None  # a layout item holds a chart or a board
        charts = item.board.placed_charts()
    needed = {name for chart in charts for name in chart.variable_dependencies}
    focused_board = board.model_copy(
        update={
            "variables": {
                name: var for name, var in board.variables.items() if name in needed
            },
            "layout": Layout(type=LayoutType.ROWS, items=[item]),
            "title": "",
            "authored_width": None,
            "meta": {
                **board.meta,
                PRE_FOCUS_PLACED_CHARTS_META_KEY: sorted(
                    c.id for c in board.placed_charts() if c.id
                ),
                PRE_FOCUS_REFERENCED_QUERIES_META_KEY: sorted(
                    referenced_query_names(board)
                ),
            },
        }
    )
    attach_chart_styles(focused_board)
    return focused_board
