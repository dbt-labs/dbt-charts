"""Terminal chart rendering using Plotext and Rich.

Stage: RENDER
Purpose: Convert Vega-Lite specifications to terminal-friendly charts.

This module handles the conversion from Vega-Lite chart specifications
to terminal output using Plotext for charts and Rich for tables.
"""

import logging
from typing import Any

from dbt_charts.core.compile.format import (
    finalize_kpi_value_format,
    kpi_format_native,
)
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    Chart,
    HeatmapChart,
    KpiChart,
    LineChart,
    ScatterChart,
    SparkBarChart,
)
from dbt_charts.core.compile.models.primitives import FormatAliases
from dbt_charts.core.compile.models.style.theme import KpiValueStyle
from dbt_charts.core.compile.resolve.chart.channel import parse_style_channel
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.kpi import kpi_value_text
from dbt_charts.core.render.utils import normalize_data_types, slug_to_text
from dbt_charts.core.text.format_d3 import NULL_DISPLAY

logger = logging.getLogger(__name__)

# Charts with explicit x/y/color fields usable by terminal renderers.
# BarChart/LineChart/AreaChart/ScatterChart/HeatmapChart inherit from
# _CartesianChartFields; SparkBarChart declares x/y/color independently.
_XYChart = (
    BarChart | LineChart | AreaChart | ScatterChart | HeatmapChart | SparkBarChart
)
_XY_CHART_TYPES = (
    BarChart,
    LineChart,
    AreaChart,
    ScatterChart,
    HeatmapChart,
    SparkBarChart,
)


def _sort_key(row: tuple[Any, float]) -> str:
    """Sort key for (x_val, y_val) tuples — None x sorts first."""
    return str(row[0]) if row[0] is not None else ""


def _terminal_color_field(chart: _XYChart) -> str | None:
    """Return str color field for terminal use, or None for non-data-bound forms."""
    color = chart.color
    if color is None:
        return None
    ch = parse_style_channel(color, "color")
    if ch.mode == "series":
        return ch.data_field
    if ch.mode == "literal":
        return None
    raise ValueError(
        f"Terminal renderer does not support '{ch.mode}' color channels. "
        "Use a string field name or {value: '#hex'} for a literal color."
    )


def _terminal_y_field(chart: _XYChart) -> str | None:
    """Return a single y field for terminal renderers that only support one series."""
    if isinstance(chart.y, list):
        if len(chart.y) > 1:
            logger.warning(
                "Terminal chart rendering only supports one y series; using '%s' and dropping %d additional series for chart '%s'.",
                chart.y[0],
                len(chart.y) - 1,
                chart.id,
            )
        return chart.y[0] if chart.y else None
    return chart.y


def render_chart_terminal(
    chart: Chart,
    data: list[dict[str, Any]],
    display_title: str,
    width: int | None = None,
    height: int | None = None,
    colors: bool = True,
    *,
    formats: FormatAliases | None,
) -> str:
    """Render a chart to terminal output.

    Args:
        chart: Chart definition
        data: List of dicts containing chart data
        width: Optional terminal width in characters
        height: Optional terminal height in characters
        colors: Whether to use ANSI colors
        display_title: Final title text to render.
        formats: Board-level style.formats alias map (ResolvedStyle.formats),
            for a KPI/table value format authored as a bare alias name.

    Returns:
        Terminal-formatted chart string
    """
    chart_type: str = chart.type
    # Map unsupported chart types to supported alternatives
    # This allows all chart types to render in terminal mode
    chart_type = _get_terminal_chart_type(chart_type)

    # Handle special chart types
    if chart_type == "table":
        return render_table_terminal(chart, data, display_title, width=width)
    elif chart_type == "kpi":
        return render_kpi_terminal(chart, data, display_title, formats=formats)

    # Narrow to charts that have x/y fields before entering the plotext path.
    # Non-xy charts that map to a plotext type (e.g. PieChart → "bar",
    # CalloutChart → "bar" via the default) fall back to table rendering since
    # they have no x/y channels to pass to the plotext renderers.
    if not isinstance(chart, _XY_CHART_TYPES):
        return render_table_terminal(chart, data, display_title, width=width)

    # chart is now narrowed to _XYChart for all subsequent calls.
    xy_chart: _XYChart = chart

    # Normalize data types
    normalized_data = normalize_data_types(data)

    import plotext as plt  # pyright: ignore[reportMissingTypeStubs]

    # Configure Plotext
    plt.clear_data()
    plt.clear_figure()

    # Set dimensions (Plotext uses width, height)
    # Use more conservative width to avoid overflow issues
    # Plotext can have issues with very wide terminals, so cap at reasonable size
    max_chart_width = min(
        width - 8 if width else 72, 120
    )  # Account for borders and cap width
    chart_height = height - 6 if height else 15  # Account for title and borders

    if width and height:
        plt.plotsize(max_chart_width, chart_height)
    elif width:
        plt.plotsize(max_chart_width, 15)  # Default height if only width specified
    elif height:
        plt.plotsize(72, chart_height)  # Default width if only height specified
    else:
        plt.plotsize(72, 15)  # Default size

    # Configure plotext for better terminal compatibility
    # Disable theme to avoid background color issues
    plt.theme("clear")
    # Use simpler color scheme
    if not colors:
        plt.plotsize(max_chart_width if width else 72, chart_height if height else 15)

    # Set title
    if display_title:
        plt.title(display_title)

    # Render based on chart type
    if chart_type in ("bar", "histogram"):
        return _render_bar_chart_terminal(xy_chart, normalized_data, plt, display_title)
    elif chart_type == "line":
        return _render_line_chart_terminal(
            xy_chart, normalized_data, plt, display_title
        )
    elif chart_type in ("circle", "scatter"):
        return _render_scatter_chart_terminal(
            xy_chart, normalized_data, plt, colors, display_title
        )
    elif chart_type == "area":
        return _render_area_chart_terminal(
            xy_chart, normalized_data, plt, display_title
        )
    else:
        # This shouldn't happen since _get_terminal_chart_type maps all types
        # but fallback to bar chart just in case
        return _render_bar_chart_terminal(xy_chart, normalized_data, plt, display_title)


def _get_terminal_chart_type(chart_type: str) -> str:
    """Map chart types to terminal-supported equivalents.

    Terminal mode supports: bar, line, scatter, area, table, kpi
    All other chart types are mapped to their closest visual equivalent.

    Args:
        chart_type: Original chart type from the chart definition

    Returns:
        Terminal-compatible chart type
    """
    # Chart type mapping: unsupported -> supported equivalent
    # The goal is to show *something* meaningful rather than an error
    chart_type_map = {
        # Direct support (no mapping needed)
        "bar": "bar",
        "histogram": "bar",
        "line": "line",
        "circle": "scatter",
        "scatter": "scatter",
        "area": "area",
        "table": "table",
        "kpi": "kpi",
        # Pie charts -> bar chart (shows same breakdown, different viz)
        "pie": "bar",
        "arc": "bar",
        # Heatmap -> table (shows the grid data)
        "heatmap": "table",
        # Boxplot -> bar (shows aggregated values)
        "boxplot": "bar",
        # Tick marks -> bar (scatter requires numeric x, tick often has categorical)
        "tick": "bar",
        # Rule (reference lines) -> bar (line requires numeric data)
        "rule": "bar",
        # Rect -> bar
        "rect": "bar",
        # Geoshape/Map -> table (can't render maps in terminal)
        "geoshape": "table",
        "map": "table",
        "point_map": "table",
        "bubble_map": "table",
        # Trail -> line (trail is essentially a line with variable width)
        "trail": "line",
        # Square -> bar (scatter requires numeric x)
        "square": "bar",
        # Image -> table (can't render images in terminal)
        "image": "table",
        # Errorbar -> bar (show the main value)
        "errorbar": "bar",
        # Errorband -> area (show the main trend)
        "errorband": "area",
    }

    return chart_type_map.get(chart_type, "bar")  # Default to bar for unknown types


def render_table_terminal(
    chart: Chart,
    data: list[dict[str, Any]],
    display_title: str,
    width: int | None = None,
) -> str:
    """Render a table chart using Rich.

    Args:
        chart: Chart definition
        data: Table data
        width: Optional terminal width

    Returns:
        Terminal-formatted table string
    """
    from rich import box as rich_box
    from rich.console import Console
    from rich.table import Table

    console = Console(width=width, force_terminal=True, legacy_windows=False)
    table = Table(
        show_header=True, box=None if width and width < 80 else rich_box.SIMPLE
    )

    if not data:
        return _empty_table_output(display_title)

    # Get columns
    columns = list(data[0].keys())

    # Add columns
    for col in columns:
        # Format column name
        display_name = slug_to_text(col)
        table.add_column(display_name, overflow="ellipsis", no_wrap=True)

    # Add rows
    for row in data:
        values = []
        for col in columns:
            value = row.get(col, "")
            # Format value
            if value is None:
                display_value = NULL_DISPLAY
            elif isinstance(value, (int, float)):
                if isinstance(value, float) and value.is_integer():
                    display_value = str(int(value))
                else:
                    display_value = (
                        f"{value:,.2f}" if isinstance(value, float) else f"{value:,}"
                    )
            else:
                display_value = str(value)
            values.append(display_value)
        table.add_row(*values)

    # Render to string
    with console.capture() as capture:
        console.print(table)
    output = capture.get()

    # Add title if present
    if display_title:
        return f"{display_title}\n{output}"
    return output


def render_kpi_terminal(
    chart: Chart,
    data: list[dict[str, Any]],  # type-state: explicit_any — raw query row dicts
    display_title: str,
    *,
    formats: FormatAliases | None,
) -> str:
    """Render a KPI chart as text.

    Reads the same `chart.value` contract as the SVG renderer: a column
    reference (string column name). Raises ``ChartDataError`` when the
    column is not present — same behavior as the SVG renderer.

    The data-shape contract matches the SVG renderer: empty data and
    multi-row results both raise ``ChartDataError`` so the same authoring
    mistake fails the same way regardless of output medium.
    """
    from dbt_charts.core.render.chart.kpi import _resolve_value  # noqa: PLC0415

    # render_kpi_terminal is only dispatched for KPI charts; narrow the union.
    assert isinstance(chart, KpiChart)

    chart_id = getattr(chart, "id", "unknown")
    if not data:
        raise ChartDataError(
            f"KPI chart '{chart_id}' has no data — query returned 0 rows",
            chart_id=chart_id,
        )
    if len(data) > 1:
        raise ChartDataError(
            f"KPI chart '{chart_id}' expects exactly 1 row, got {len(data)}. "
            f"Use a query that returns a single row (e.g. SELECT SUM(...) or LIMIT 1).",
            chart_id=chart_id,
        )

    raw_value = chart.value
    row = data[0]
    cell, _ = _resolve_value(raw_value, row, chart_id)

    # chart.style.value is None when a KPI's style: never touches value:, though
    # the patch stub types it non-Optional; the local re-declaration narrows it.
    value_style: KpiValueStyle | None = chart.style.value if chart.style else None
    value_format = value_style.format if value_style is not None else None
    if isinstance(cell, (int, float)):
        if value_format is not None:
            # Mirrors the finalization resolve() bakes onto ResolvedKpiChart.format.
            display_value = kpi_value_text(
                cell,
                finalize_kpi_value_format(value_format, float(cell), formats),
                chart_id,
                formats,
                native=kpi_format_native(value_format, formats),
                format_may_be_cascaded=True,
            )
        elif isinstance(cell, float) and cell.is_integer():
            display_value = f"{int(cell):,}"
        else:
            display_value = f"{cell:,.2f}"
    else:
        display_value = "" if cell is None else str(cell)

    title_text = display_title

    from rich.console import Console
    from rich.text import Text

    console = Console(force_terminal=True, legacy_windows=False)
    text = Text()
    if title_text:
        text.append(title_text, style="bold")
        text.append(": ", style="bold")
    text.append(display_value, style="bold cyan")

    with console.capture() as capture:
        console.print(text)
    return capture.get()


def _render_bar_chart_terminal(
    chart: _XYChart,
    data: list[dict[str, Any]],
    plt: Any,
    display_title: str,
) -> str:
    """Render a bar chart using Plotext."""
    x_field = chart.x
    y_field = _terminal_y_field(chart)

    # If no x/y fields, try to infer from data or render as table
    if not x_field or not y_field:
        # Try to find suitable fields from data
        if data and len(data) > 0:
            keys = list(data[0].keys())
            # Try to find a numeric field for y
            for key in keys:
                try:
                    float(data[0].get(key, 0))
                    if y_field is None:
                        y_field = key
                    elif x_field is None:
                        x_field = key
                except (ValueError, TypeError):
                    if x_field is None:
                        x_field = key
            if not x_field or not y_field:
                return _fallback_chart_render(display_title, data)
        else:
            return _fallback_chart_render(display_title, data)

    # Extract data with error handling for non-numeric values
    x_data = []
    y_data = []
    for row in data:
        if x_field in row:
            x_data.append(str(row.get(x_field, "")))
            try:
                y_val = float(row.get(y_field, 0))
                y_data.append(y_val)
            except (ValueError, TypeError):
                # Non-numeric y value - skip this row
                x_data.pop()  # Remove the x value we just added

    if not y_data:
        return _fallback_chart_render(display_title, data)

    color_field = _terminal_color_field(chart)
    if color_field and data and color_field in data[0]:
        # Group by color - renders as simple bars (grouped rendering not yet implemented)
        plt.bar(y_data)
    else:
        plt.bar(y_data)

    # Set labels
    if x_data and len(x_data) == len(y_data):
        # Limit x-axis labels to avoid overflow (from config)
        from dbt_charts.core.compile.config import get_terminal_config

        max_labels = get_terminal_config().max_labels
        if len(x_data) > max_labels:
            step = len(x_data) // max_labels
            tick_positions = list(range(0, len(x_data), step))
            tick_labels = [x_data[i] for i in tick_positions]
            plt.xticks(tick_positions, tick_labels)
        else:
            plt.xticks(list(range(len(x_data))), x_data)

    # Build chart and clean up output
    chart_output = plt.build()
    # Remove trailing whitespace and normalize line endings
    lines = chart_output.split("\n")
    cleaned_lines = [line.rstrip() for line in lines]
    return "\n".join(cleaned_lines)


def _render_line_chart_terminal(
    chart: _XYChart,
    data: list[dict[str, Any]],
    plt: Any,
    display_title: str,
) -> str:
    """Render a line chart using Plotext."""
    x_field = chart.x
    y_field = _terminal_y_field(chart)

    if not x_field or not y_field:
        return _fallback_chart_render(display_title, data)

    # Extract and sort data with error handling
    rows = []
    for row in data:
        try:
            x_val = row.get(x_field)
            y_val = float(row.get(y_field, 0))
            rows.append((x_val, y_val))
        except (ValueError, TypeError):
            # Skip rows with non-numeric y values
            pass

    if not rows:
        return _fallback_chart_render(display_title, data)

    rows.sort(key=_sort_key)

    x_data = [str(row[0]) if row[0] is not None else "" for row in rows]
    y_data = [row[1] for row in rows]

    color_field = _terminal_color_field(chart)
    if color_field and color_field in data[0]:
        # Group by color
        color_groups: dict[str, list[tuple[Any, ...]]] = {}
        for row in data:
            color_val = str(row.get(color_field, ""))
            x_val = row.get(x_field)
            y_val = float(row.get(y_field, 0))
            if color_val not in color_groups:
                color_groups[color_val] = []
            color_groups[color_val].append((x_val, y_val))

        # Render multiple lines
        for color_val, points in color_groups.items():
            points.sort(key=_sort_key)
            y_vals = [p[1] for p in points]
            plt.plot(y_vals, label=color_val)
    else:
        plt.plot(y_data)

    # Set labels
    if x_data:
        # Limit x-axis labels to avoid overflow - plotext can struggle with many labels
        from dbt_charts.core.compile.config import get_terminal_config

        max_labels = get_terminal_config().max_labels
        if len(x_data) > max_labels:
            # Show every Nth label
            step = len(x_data) // max_labels
            tick_positions = list(range(0, len(x_data), step))
            tick_labels = [x_data[i] for i in tick_positions]
            plt.xticks(tick_positions, tick_labels)
        else:
            plt.xticks(list(range(len(x_data))), x_data)

    # Build chart and clean up output
    chart_output = plt.build()
    # Remove any problematic control characters but keep ANSI color codes
    # Strip trailing whitespace and normalize line endings
    lines = chart_output.split("\n")
    cleaned_lines = [line.rstrip() for line in lines]
    return "\n".join(cleaned_lines)


def _render_scatter_chart_terminal(
    chart: _XYChart,
    data: list[dict[str, Any]],
    plt: Any,
    colors: bool,
    display_title: str,
) -> str:
    """Render a scatter plot using Plotext."""
    x_field = chart.x
    y_field = _terminal_y_field(chart)

    if not x_field or not y_field:
        return _fallback_chart_render(display_title, data)

    # Extract data - try to convert to numeric, fall back to bar chart if not possible
    x_data = []
    y_data = []
    for row in data:
        if x_field in row and y_field in row:
            try:
                x_val = float(row.get(x_field, 0))
                y_val = float(row.get(y_field, 0))
                x_data.append(x_val)
                y_data.append(y_val)
            except (ValueError, TypeError):
                # Non-numeric x values - fall back to bar chart
                return _render_bar_chart_terminal(chart, data, plt, display_title)

    if not x_data or not y_data:
        return _fallback_chart_render(display_title, data)

    plt.scatter(x_data, y_data)
    # Build chart and clean up output
    chart_output = plt.build()
    lines = chart_output.split("\n")
    cleaned_lines = [line.rstrip() for line in lines]
    return "\n".join(cleaned_lines)


def _render_area_chart_terminal(
    chart: _XYChart,
    data: list[dict[str, Any]],
    plt: Any,
    display_title: str,
) -> str:
    """Render an area chart using Plotext (as filled line)."""
    x_field = chart.x
    y_field = _terminal_y_field(chart)

    if not x_field or not y_field:
        return _fallback_chart_render(display_title, data)

    # Extract and sort data with error handling
    rows = []
    for row in data:
        try:
            x_val = row.get(x_field)
            y_val = float(row.get(y_field, 0))
            rows.append((x_val, y_val))
        except (ValueError, TypeError):
            # Skip rows with non-numeric y values
            pass

    if not rows:
        return _fallback_chart_render(display_title, data)

    rows.sort(key=_sort_key)

    x_data = [str(row[0]) if row[0] is not None else "" for row in rows]
    y_data = [row[1] for row in rows]

    # Plotext doesn't have area charts, use filled line
    plt.plot(y_data, fillx=True)
    if x_data:
        plt.xticks(list(range(len(x_data))), x_data)

    return str(plt.build())


def _fallback_chart_render(display_title: str, data: list[dict[str, Any]]) -> str:
    """Fallback rendering when Plotext is not available."""
    title = display_title or "Chart"
    if data:
        return f"{title}\n[Chart rendering not available - install plotext]"
    return f"{title}\n[No data]"


def _empty_table_output(display_title: str) -> str:
    """Output for empty table."""
    title = display_title or "Table"
    return f"{title}\n[No data]"
