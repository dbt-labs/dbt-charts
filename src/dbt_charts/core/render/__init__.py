"""Rendering stage: Board + data → output.

Stage: RENDER
Purpose: Transform compiled boards into visual output formats.

This module provides the rendering pipeline that takes a Board
and data (from the execute stage) and produces output in various formats
(SVG, HTML, PNG, PDF, Terminal).

Main Entry Points:
    render(): Render a full board

The render stage:
1. Takes a Board (from compile stage)
2. Uses Executor to fetch data lazily as needed
3. Generates Vega-Lite specs for charts
4. Produces output in requested format

Supported Formats:
    - svg: Scalable vector graphics (default)
    - html: Interactive HTML pages with embedded charts
    - png: Raster image format
    - pdf: PDF documents
    - terminal: Terminal output with ASCII/Unicode charts
"""

from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart import (
    generate_vega_lite_spec,
    slug_to_text,
)
from dbt_charts.core.render.controls import (
    controls_runtime_source,
    controls_stylesheet,
)
from dbt_charts.core.render.errors import (
    MissingRequiredVariablesError,
    MissingVariable,
    RenderError,
)
from dbt_charts.core.render.render_result import RenderResult
from dbt_charts.core.render.renderer import render

# Terminal rendering
from dbt_charts.core.render.terminal import (
    render_board_terminal,
    render_chart_item_terminal,
    render_layout_item_terminal,
)
from dbt_charts.core.render.terminal_charts import (
    render_chart_terminal,
    render_kpi_terminal,
    render_table_terminal,
)
from dbt_charts.core.render.variables_strip import render_variables_strip_svg

__all__ = [
    # Main functions
    "render",
    "RenderResult",
    # Errors
    "ChartDataError",
    "MissingRequiredVariablesError",
    "MissingVariable",
    "RenderError",
    # Variables: read-only strip (artifact) + control layer (hosts)
    "render_variables_strip_svg",
    "controls_runtime_source",
    "controls_stylesheet",
    # Vega-Lite
    "generate_vega_lite_spec",
    "slug_to_text",
    # Terminal rendering
    "render_board_terminal",
    "render_layout_item_terminal",
    "render_chart_item_terminal",
    "render_chart_terminal",
    "render_table_terminal",
    "render_kpi_terminal",
]
