"""Placeholder data generation for charts without queries.

Stage: RENDER
Purpose: Generate appropriate dummy/placeholder data for charts without queries.

This module provides:
- generate_placeholder_data(chart_type, chart): Generate dummy data for a chart type
- add_placeholder_overlay(svg, width, height): Add "add data" overlay to SVG

The placeholder feature helps users:
- Build boards iteratively (design first, connect data later)
- See what charts will look like before writing SQL
- Use the playground for learning/experimentation

Visual Treatment:
- Placeholder charts render with ~40% opacity (high transparency)
- "add data" text overlay centered on the chart
- Makes it obvious this is placeholder state, not real data
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.models.chart.normalized import _CartesianChartFields
from dbt_charts.core.render.svg_utils import escape_attr
from dbt_charts.core.text.format_d3 import (
    active_time_locale,
    board_word,
)

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle


def generate_placeholder_data(
    chart_type: str | None,
    chart: Chart | None = None,
) -> list[dict[str, Any]]:
    """Generate appropriate placeholder/dummy data for a chart type.

    The data generated depends on the chart type:
    - Bar/Column: Categorical + numeric data
    - Line/Area: Time series-like data with month labels
    - Scatter/Point: Two numeric columns (x, y)
    - Pie: Categorical + numeric proportions
    - Table: Multi-column sample data
    - KPI: Single numeric value
    - Histogram: Numeric values for binning
    - Heatmap: X, Y, Value grid data

    Args:
        chart_type: The type of chart (bar, line, table, etc.)
        chart: Optional Chart to extract field names from

    Returns:
        List of dicts containing placeholder data appropriate for the chart type.
        Data is consistent (not random) for predictable rendering.

    """
    # Extract field names from chart config if provided
    x_field = "category"
    y_field = "value"
    if chart:
        # x / y are per-family fields — pie (theta/color), kpi (value), and table
        # (columns) family models don't declare them, so read defensively.
        x_field = getattr(chart, "x", None) or "category"
        y_raw = getattr(chart, "y", None)
        if isinstance(y_raw, list):
            y_field = y_raw[0] if y_raw else "value"
        elif y_raw:
            y_field = y_raw
        else:
            y_field = "value"

    # Normalize chart type to lowercase, handling None, empty, and whitespace
    chart_type = (
        chart_type.strip().lower() if chart_type and chart_type.strip() else "bar"
    )

    data = _placeholder_rows(chart_type, x_field, y_field)
    # Small multiples: cross the base rows with synthetic partition values so the
    # placeholder renders as a faceted skeleton AND carries the facet columns the
    # FacetFeature validates against (placeholder data otherwise has only x/y).
    # multiples lives only on the cartesian families (bar/line/area/scatter/
    # heatmap) — pie/kpi/table don't declare it, so narrow the union member
    # first instead of probing for the attribute.
    if isinstance(chart, _CartesianChartFields) and chart.multiples is not None:
        data = _expand_for_multiples(data, chart.multiples)
    return data


def _placeholder_rows(
    chart_type: str, x_field: str, y_field: str
) -> list[dict[str, Any]]:
    """Base placeholder rows for a chart type (before small-multiples expansion)."""
    # Bar chart (default) - categorical + numeric
    if chart_type in ("bar", "column"):
        return _generate_bar_placeholder(x_field, y_field)

    # Line and area charts - time series
    if chart_type in ("line", "area"):
        return _generate_line_placeholder(x_field, y_field)

    # Scatter and circle charts - two numeric columns
    # Use "x" and "y" as defaults for scatter if not specified
    if chart_type in ("scatter", "circle"):
        scatter_x = x_field if x_field != "category" else "x"
        scatter_y = y_field if y_field != "value" else "y"
        return _generate_scatter_placeholder(scatter_x, scatter_y)

    # Pie charts - proportional data
    if chart_type in ("pie", "donut"):
        return _generate_pie_placeholder(x_field, y_field)

    # Table - multi-column sample data
    if chart_type == "table":
        return _generate_table_placeholder()

    # KPI - single value
    if chart_type == "kpi":
        return _generate_kpi_placeholder(y_field)

    # Histogram - numeric values for binning
    if chart_type == "histogram":
        return _generate_histogram_placeholder()

    # Boxplot - grouped numeric values
    if chart_type == "boxplot":
        return _generate_boxplot_placeholder(x_field, y_field)

    # Heatmap/Rect - grid data
    if chart_type in ("heatmap", "rect"):
        return _generate_heatmap_placeholder()

    # Spark bar - compact horizontal bars for profiler cards
    if chart_type == "spark_bar":
        return _generate_spark_bar_placeholder(x_field, y_field)

    # Default fallback to bar chart data
    return _generate_bar_placeholder(x_field, y_field)


def _expand_for_multiples(
    base: list[dict[str, Any]], multiples: Any
) -> list[dict[str, Any]]:
    """Cross base placeholder rows with two synthetic values per facet field.

    Gives the placeholder the partition column(s) it needs to render as a
    small-multiples skeleton (rows-only → 2 panels, grid → 2x2) and to pass the
    FacetFeature partition-field check.
    """
    fields = [f for f in (multiples.rows, multiples.columns) if f]
    if not fields:
        return base
    combos: list[dict[str, Any]] = [{}]
    for field in fields:
        combos = [
            {**combo, field: value}
            for combo in combos
            for value in (f"{field} A", f"{field} B")
        ]
    return [{**row, **combo} for combo in combos for row in base]


def _category(letter: str) -> str:
    return board_word("placeholder.category", letter=letter)


def _generate_bar_placeholder(x_field: str, y_field: str) -> list[dict[str, Any]]:
    """Generate placeholder data for bar charts."""
    return [
        {x_field: _category(letter), y_field: value}
        for letter, value in zip("ABCDE", (100, 75, 50, 85, 60), strict=True)
    ]


def _generate_line_placeholder(x_field: str, y_field: str) -> list[dict[str, Any]]:
    """Generate placeholder data for line/area charts."""
    # Use month names for time-like x axis
    time_field = x_field if x_field != "category" else "month"
    months = active_time_locale()["shortMonths"]
    return [
        {time_field: months[month], y_field: value}
        for month, value in enumerate((10, 25, 40, 35, 50, 45, 60))
    ]


def _generate_scatter_placeholder(
    x_field: str = "x",
    y_field: str = "y",
) -> list[dict[str, Any]]:
    """Generate placeholder data for scatter/point charts."""
    # Generate a pattern of points
    return [
        {x_field: 10, y_field: 20},
        {x_field: 25, y_field: 45},
        {x_field: 35, y_field: 30},
        {x_field: 50, y_field: 60},
        {x_field: 65, y_field: 55},
        {x_field: 75, y_field: 80},
        {x_field: 90, y_field: 70},
        {x_field: 40, y_field: 50},
        {x_field: 55, y_field: 40},
        {x_field: 80, y_field: 90},
    ]


def _generate_pie_placeholder(x_field: str, y_field: str) -> list[dict[str, Any]]:
    """Generate placeholder data for pie charts."""
    cat_field = x_field if x_field != "category" else "category"
    return [
        {cat_field: board_word("placeholder.segment", letter=letter), y_field: value}
        for letter, value in zip("ABCD", (40, 30, 20, 10), strict=True)
    ]


def _generate_table_placeholder() -> list[dict[str, Any]]:
    """Generate placeholder data for tables."""
    active = board_word("placeholder.status_active")
    statuses = (
        active,
        board_word("placeholder.status_pending"),
        active,
        board_word("placeholder.status_inactive"),
        active,
    )
    return [
        {
            "name": board_word("placeholder.item", n=str(number)),
            "value": value,
            "status": status,
        }
        for number, value, status in zip(
            range(1, 6), (123, 456, 789, 234, 567), statuses, strict=True
        )
    ]


def _generate_kpi_placeholder(value_field: str) -> list[dict[str, Any]]:
    """Generate placeholder data for KPI charts."""
    return [{value_field: 1234}]


def _generate_histogram_placeholder() -> list[dict[str, Any]]:
    """Generate placeholder data for histograms."""
    # Generate values that will bin nicely

    values = []
    # Create a distribution-like pattern
    for i in range(50):
        # Bell curve-ish distribution centered around 50
        val = 50 + (i % 10 - 5) * 5 + (i // 10 - 2) * 3
        values.append({"value": max(0, min(100, val))})
    return values


def _generate_boxplot_placeholder(x_field: str, y_field: str) -> list[dict[str, Any]]:
    """Generate placeholder data for boxplots."""
    cat_field = x_field if x_field != "category" else "category"
    data = []
    # Generate data for 3 categories with different distributions
    for letter, base in (("A", 50), ("B", 70), ("C", 40)):
        cat = board_word("placeholder.group", letter=letter)
        for offset in [-20, -10, -5, 0, 5, 10, 20, 25, -15, 15]:
            data.append({cat_field: cat, y_field: base + offset})
    return data


def _generate_heatmap_placeholder() -> list[dict[str, Any]]:
    """Generate placeholder data for heatmaps."""
    data = []
    rows = [board_word("placeholder.row", letter=letter) for letter in "ABCD"]
    cols = [board_word("placeholder.col", n=str(number)) for number in range(1, 5)]
    # Generate a pattern of values
    values = [
        [80, 60, 40, 20],
        [70, 90, 50, 30],
        [40, 50, 80, 60],
        [30, 40, 60, 90],
    ]
    for i, row in enumerate(rows):
        for j, col in enumerate(cols):
            data.append({"y": row, "x": col, "value": values[i][j]})
    return data


def _generate_spark_bar_placeholder(x_field: str, y_field: str) -> list[dict[str, Any]]:
    """Generate placeholder data for spark bar charts."""
    # Use appropriate field names - y is category, x is frequency
    cat_field = y_field if y_field != "value" else "value"
    freq_field = x_field if x_field != "category" else "frequency"
    return [
        {cat_field: _category(letter), freq_field: value}
        for letter, value in zip("ABCDE", (100, 75, 50, 35, 20), strict=True)
    ]


def add_placeholder_overlay(
    svg: str,
    width: float,
    height: float,
    font: FontStyle,
    resolved_style: ResolvedStyle,
    text: str | None = None,
) -> str:
    """Add placeholder overlay to an SVG chart.

    Adds:
    1. A semi-transparent white overlay rectangle
    2. "add data" text centered on the chart

    The overlay makes it clear the chart is showing placeholder data.

    Args:
        svg: The SVG string to add overlay to
        width: Chart width in pixels
        height: Chart height in pixels
        font: FontStyle for the overlay text (reads .family)
        resolved_style: Board-scoped ResolvedStyle for placeholder overlay config.
        text: Text to display (default: "add data")

    Returns:
        SVG string with overlay added

    """
    import html as html_module

    overlay = resolved_style.placeholder.overlay
    font_family = (
        font.family
        or ""  # type-state: silent_fallback — no override font passed; an empty font-family lets the SVG/CSS cascade inherit the ambient family instead of forcing one
    )
    display_text = text if text is not None else overlay.text
    _overlay_case = font.case
    if _overlay_case is not None and _overlay_case != "none":
        from dbt_charts.core.text.case import apply_case

        display_text = apply_case(display_text, _overlay_case)

    # Calculate center position
    center_x = width / 2
    center_y = height / 2

    # Escape text for safety
    escaped_text = html_module.escape(display_text)

    # Create overlay elements
    # Semi-transparent overlay rectangle
    overlay_rect = (
        f'<rect class="dbt-pointer-inert" x="0" y="0" width="{escape_attr(width)}" height="{escape_attr(height)}" '
        f'fill="{escape_attr(overlay.background)}"/>'
    )

    # Text element centered on chart
    overlay_text = (
        f'<text class="dbt-pointer-inert" x="{escape_attr(center_x)}" y="{escape_attr(center_y)}" '
        f'text-anchor="middle" dominant-baseline="middle" '
        f'font-family="{escape_attr(font_family)}" '
        f'font-size="{escape_attr(overlay.font.size)}" font-weight="{escape_attr(overlay.font.weight)}" fill="{escape_attr(overlay.font.color)}">'
        f"{escaped_text}"
        f"</text>"
    )

    # Create overlay group
    overlay_group = f'<g class="placeholder-overlay">{overlay_rect}{overlay_text}</g>'

    # Insert overlay before closing </svg> tag
    # Use rfind to only replace the LAST </svg> (handles nested SVGs correctly)
    if svg.endswith("</svg>"):
        svg = svg[:-6] + overlay_group + "</svg>"
    elif "</svg>" in svg:
        idx = svg.rfind("</svg>")
        svg = svg[:idx] + overlay_group + svg[idx:]
    else:
        # If no closing tag found, append
        svg = svg + overlay_group

    return svg


def apply_placeholder_opacity(
    svg: str,
    resolved_style: ResolvedStyle,
    opacity: float | None = None,
) -> str:
    """Apply reduced opacity to SVG content for placeholder appearance.

    Wraps the SVG content in a group with reduced opacity to make it
    appear faded/disabled.

    Args:
        svg: The SVG string to modify
        resolved_style: Board-scoped ResolvedStyle for placeholder opacity config.
        opacity: Opacity value (0.0-1.0), overrides resolved_style when provided.

    Returns:
        SVG string with opacity applied to content

    """
    effective_opacity = (
        opacity if opacity is not None else resolved_style.placeholder.opacity
    )

    # Extract SVG parts directly - typical for Vega-Lite output (flat structure)
    match = re.search(r"(<svg[^>]*>)(.*)(</svg>)", svg, re.DOTALL)
    if not match:
        return svg

    opening_tag, content, closing_tag = match.groups()
    wrapped_content = f'<g opacity="{escape_attr(effective_opacity)}">{content}</g>'
    return f"{opening_tag}{wrapped_content}{closing_tag}"
