"""Layout rendering functions for SVG and HTML.

Stage: RENDER
Purpose: Render different layout types (rows, cols, grid, tabs) to SVG and HTML.

This module provides functions to render layouts based on their type:
- Rows: Vertical stacking
- Cols: Horizontal distribution
- Grid: Positioned grid cells
- Tabs: Tabbed container (active tab only for SVG)

Dependencies:
    - compile.models.board.normalized (Layout, LayoutItem, Board)
    - compile.models.chart.normalized (Chart)
    - .renderer (for rendering charts and nested boards)

OPTIMIZATION OPPORTUNITY: Batch Vega-Lite Rendering
---------------------------------------------------
Currently, each chart in a layout is rendered individually via separate
vl-convert calls. This could be optimized by:

1. Collecting all Vega-Lite charts in the layout
2. Combining them into a single vconcat/hconcat spec
3. Making ONE vl-convert call for all charts
4. Extracting individual SVGs from the combined result

Expected improvement: ~30% faster rendering for boards with 4+ charts.
See: plans/archive/VEGA_SVG_BATCH_CONVERSION_ANALYSIS.md
"""

import html

from dbt_charts.core.compile.models.board.normalized import (
    Board,
    LayoutItem,
    VariableValues,
)
from dbt_charts.core.compile.models.board.resolved import ResolvedLayoutItem
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.execute.chart_data_provider import ChartDataProvider
from dbt_charts.core.render.chart.rendering import render_layout_item
from dbt_charts.core.render.layout_sizing import RenderCache
from dbt_charts.core.render.sizing import resolve_active_tab_index
from dbt_charts.core.render.svg_utils import (
    border_dash_attrs,
    escape_attr,
    px,
    translate_group,
)

__all__ = [
    "is_details_expanded",
    "render_rows_layout",
    "render_cols_layout",
    "render_grid_layout",
    "render_tabs_layout",
    "render_details_summary",
]


def _bg_rect(
    width: float,
    height: float,
    fill: str,
    rx: float = 4,
    x: float = 0,
    y: float = 0,
    stroke: str | None = None,
    stroke_width: float = 1,
) -> str:
    """Return a background rect SVG element."""
    stroke_attr = (
        f' stroke="{escape_attr(stroke)}" stroke-width="{escape_attr(stroke_width)}"'
        if stroke
        else ""
    )
    return f'<rect x="{escape_attr(x)}" y="{escape_attr(y)}" width="{escape_attr(width)}" height="{escape_attr(height)}" fill="{escape_attr(fill)}"{stroke_attr} rx="{escape_attr(rx)}"/>'


def render_rows_layout(
    items: tuple[ResolvedLayoutItem, ...],
    executor: ChartDataProvider,
    variables: VariableValues,
    available_width: float,
    available_height: float,
    card_gap: float,
    gap: float,
    background: str | None = None,
    *,
    resolved_style: ResolvedStyle,
    render_cache: RenderCache,
    error_collector: list[Diagnostic] | None = None,
) -> tuple[str, float]:
    """Render items in vertical stack.

    In a rows layout, items stack vertically. Heights are determined by:
    1. Pre-calculated dimensions from sizing module (preferred)
    2. Content-aware fallback if not pre-calculated

    Args:
        items: Layout items to render
        executor: ChartDataProvider for query execution
        variables: Variable values for queries
        available_width: Available container width
        available_height: Available container height
        gap: Gap between items
        background: Optional background color

    Returns:
        (svg_elements_string, total_actual_height) — no <svg> wrapper.
    """
    if not items:
        return "", 0.0

    rendered_items: list[str] = []
    current_y = 0.0
    actual_total_height = 0.0

    for item in items:
        # Use pre-calculated item dimensions for the render call.
        # Actual rendered height is read back from the item SVG.
        item_height = item.height if item.height > 0 else available_height
        item_width = item.width if item.width > 0 else available_width

        item_svg, actual_item_height = render_layout_item(
            item,
            executor,
            variables,
            card_gap=card_gap,
            available_width=item_width,
            available_height=item_height,
            gap=gap,
            resolved_style=resolved_style,
            render_cache=render_cache,
            source_path=item.source_path,
            error_collector=error_collector,
        )

        if item_svg:
            rendered_items.append(translate_group(0, px(current_y), item_svg))
            actual_total_height = current_y + actual_item_height
            current_y += actual_item_height + gap + card_gap

    bg_rect = ""
    if background:
        bg_rect = _bg_rect(available_width, actual_total_height, background)

    return f"{bg_rect}\n{''.join(rendered_items)}", actual_total_height


def render_cols_layout(
    items: tuple[ResolvedLayoutItem, ...],
    executor: ChartDataProvider,
    variables: VariableValues,
    available_width: float,
    available_height: float,
    card_gap: float,
    gap: float,
    background: str | None = None,
    *,
    resolved_style: ResolvedStyle,
    render_cache: RenderCache,
    error_collector: list[Diagnostic] | None = None,
) -> tuple[str, float]:
    """Render items in horizontal distribution.

    Trusts the normalizer for all sizing. Uses pre-calculated item.x, item.width,
    and item.height values.

    Args:
        items: Layout items to render (with pre-calculated dimensions from normalizer)
        executor: ChartDataProvider for query execution
        variables: Variable values for queries
        available_width: Available container width
        available_height: Available container height (upper bound)
        gap: Gap between items (unused - normalizer already applied it)
        background: Optional background color

    Returns:
        (svg_elements_string, max_actual_item_height) — no <svg> wrapper.
    """
    if not items:
        return "", 0.0

    # Render items using pre-calculated positions from normalizer
    rendered_items: list[str] = []
    max_actual_height = 0.0

    for item in items:
        # Trust normalizer for dimensions
        item_w = item.width if item.width > 0 else available_width
        item_h = item.height if item.height > 0 else available_height
        x_pos = item.x  # Use pre-calculated x position from normalizer

        item_svg, actual_item_height = render_layout_item(
            item,
            executor,
            variables,
            card_gap=card_gap,
            available_width=item_w,
            available_height=item_h,
            gap=gap,
            resolved_style=resolved_style,
            render_cache=render_cache,
            source_path=item.source_path,
            error_collector=error_collector,
        )

        if item_svg:
            rendered_items.append(translate_group(px(x_pos), 0, item_svg))
            max_actual_height = max(max_actual_height, actual_item_height)

    bg_rect = ""
    if background:
        bg_rect = _bg_rect(available_width, max_actual_height, background)

    return f"{bg_rect}\n{''.join(rendered_items)}", max_actual_height


def render_grid_layout(
    items: tuple[ResolvedLayoutItem, ...],
    executor: ChartDataProvider,
    variables: VariableValues,
    available_width: float,
    available_height: float,
    card_gap: float,
    gap: float,
    background: str | None = None,
    *,
    resolved_style: ResolvedStyle,
    render_cache: RenderCache,
    error_collector: list[Diagnostic] | None = None,
) -> tuple[str, float]:
    """Render items in positioned grid.

    Grid items have explicit x, y positions and width, height spans.
    Each item's dimensions are calculated based on the grid columns/rows.

    Args:
        items: Layout items with grid positions (x, y, width, height)
        executor: ChartDataProvider for query execution
        variables: Variable values for queries
        available_width: Available container width
        available_height: Available container height
        columns: Number of grid columns
        gap: Gap between grid cells
        background: Optional background color

    Returns:
        (svg_elements_string, max_actual_bottom_edge) — no <svg> wrapper.
    """
    if not items:
        return "", 0.0

    # Trust the normalizer - sizing.py calculates all grid positions and dimensions
    rendered_items: list[str] = []
    max_bottom_edge = 0.0

    for item in items:
        # Use pre-calculated pixel positions and dimensions from sizing.py
        pixel_x = item.x
        pixel_y = item.y
        item_w = item.width if item.width > 0 else available_width
        item_h = item.height if item.height > 0 else available_height

        item_svg, actual_item_height = render_layout_item(
            item,
            executor,
            variables,
            card_gap=card_gap,
            available_width=item_w,
            available_height=item_h,
            gap=gap,
            resolved_style=resolved_style,
            render_cache=render_cache,
            source_path=item.source_path,
            error_collector=error_collector,
        )

        if item_svg:
            rendered_items.append(translate_group(px(pixel_x), px(pixel_y), item_svg))
            max_bottom_edge = max(max_bottom_edge, pixel_y + actual_item_height)

    bg_rect = ""
    if background:
        bg_rect = _bg_rect(available_width, max_bottom_edge, background)

    return f"{bg_rect}\n{''.join(rendered_items)}", max_bottom_edge


def _build_toggle_url(
    variables: VariableValues, param_name: str, new_value: str
) -> str:
    """Build URL that changes one param while preserving all others.

    Only includes non-default variable values to keep URLs clean.
    """
    from urllib.parse import urlencode

    params = {k: str(v) for k, v in variables.items() if v is not None}
    params[param_name] = new_value
    return "?" + urlencode(params)


def render_tabs_layout(
    items: tuple[ResolvedLayoutItem, ...],
    executor: ChartDataProvider,
    variables: VariableValues,
    available_width: float,
    available_height: float,
    tab_titles: list[str] | None = None,
    tab_slugs: list[str] | None = None,
    tab_variable: str | None = None,
    active_tab: int = 0,
    tab_position: str = "top",
    background: str | None = None,
    *,
    resolved_style: ResolvedStyle,
    render_cache: RenderCache,
    error_collector: list[Diagnostic] | None = None,
) -> tuple[str, float]:
    """Render tabbed container (active tab only).

    In a tabs layout, each tab gets the full container size minus the tab bar.
    Only the active tab is rendered in SVG output. The tab bar is rendered as
    clickable SVG <a href> links that update URL params for server re-rendering.

    Args:
        items: Layout items (one per tab)
        executor: ChartDataProvider for query execution
        variables: Variable values for queries
        available_width: Available container width
        available_height: Available container height
        tab_titles: Display titles for tabs
        tab_slugs: URL-safe slugs for tabs (used in URL params)
        tab_variable: Variable name for tab selection (URL param name)
        active_tab: Index of active tab (0-based)
        tab_position: Position of tabs ("top" or "left")
        background: Optional background color

    Returns:
        (svg_elements_string, actual_height) — no <svg> wrapper.
    """
    if not items:
        return "", 0.0

    active_tab = resolve_active_tab_index(
        len(items), tab_variable, tab_slugs, active_tab, variables
    )

    tabs_config = resolved_style.layout.tabs
    tab_bar_height = tabs_config.bar_height

    # Render only active tab
    content_height = (
        available_height - tab_bar_height if tab_position == "top" else available_height
    )
    content_y = tab_bar_height if tab_position == "top" else 0.0

    active_item = items[active_tab]
    item_svg, actual_item_height = render_layout_item(
        active_item,
        executor,
        variables,
        card_gap=0.0,
        available_width=available_width,
        available_height=content_height,
        resolved_style=resolved_style,
        render_cache=render_cache,
        source_path=active_item.source_path,
        error_collector=error_collector,
    )

    # Compute tab titles once - use provided titles or generate defaults
    titles = (
        tab_titles
        if tab_titles and len(tab_titles) == len(items)
        else [f"Tab {idx + 1}" for idx in range(len(items))]
    )
    slugs = tab_slugs or [f"tab_{idx}" for idx in range(len(items))]

    # Render tab bar as clickable SVG links
    tab_width = available_width / len(titles)
    tab_bar_parts: list[str] = []
    # Active tab fill from table header background; inactive from row stripe.
    # When the theme omits these, the rects are skipped — no fallback to board
    # background. The SVG parent canvas provides the visual fill naturally.
    _active_fill = resolved_style.chart_defaults.table.header.background
    _inactive_fill = (
        resolved_style.chart_defaults.table.row.stripe.color
        if resolved_style.chart_defaults.table.row.stripe
        else None
    )
    for idx, (title, slug) in enumerate(zip(titles, slugs, strict=True)):
        is_active = idx == active_tab
        x = idx * tab_width
        weight = tabs_config.active_weight if is_active else tabs_config.inactive_weight

        _fill = _active_fill if is_active else _inactive_fill
        # Always emit the rect so the border stroke delineates tabs; use
        # fill="none" when the theme omits header/stripe background.
        _tab_rect = (
            f'<rect x="{escape_attr(x)}" y="0" width="{escape_attr(tab_width)}" height="{escape_attr(tab_bar_height)}" '
            f'fill="{escape_attr(_fill or "none")}" '  # type-state: silent_fallback — no tab fill authored; fill="none" is the real SVG value the comment above already documents, not a fabricated color
            f'stroke="{escape_attr(resolved_style.border.color)}" stroke-width="{escape_attr(tabs_config.border.width)}"'
            f"{border_dash_attrs(tabs_config.border)}/>"
        )
        tab_svg = (
            _tab_rect
            + f'<text x="{escape_attr(x + tab_width / 2)}" y="{escape_attr(tab_bar_height / 2 + tabs_config.title_baseline_offset)}" '
            f'text-anchor="middle" font-size="{escape_attr(tabs_config.font.size)}" fill="{escape_attr(resolved_style.title.font.color if is_active else resolved_style.variables.font.color)}" '
            f'font-weight="{escape_attr(weight)}">{html.escape(title)}</text>'
        )

        if tab_variable and not is_active:
            href = _build_toggle_url(variables, tab_variable, slug)
            tab_bar_parts.append(f'<a href="{escape_attr(href)}">{tab_svg}</a>')
        else:
            tab_bar_parts.append(tab_svg)

    tab_bar_svg = "\n".join(tab_bar_parts)

    content_svg = ""
    if item_svg:
        content_svg = translate_group(0, px(content_y), item_svg)

    actual_height = content_y + actual_item_height
    bg_rect = ""
    if background:
        bg_rect = _bg_rect(available_width, actual_height, background)

    return f"{bg_rect}\n{tab_bar_svg}\n{content_svg}", actual_height


def is_details_expanded(
    item: LayoutItem | ResolvedLayoutItem, variables: VariableValues
) -> bool:
    """Check if a details section is expanded based on its variable value.

    Resolution order:
      1. Live variable value (user has toggled the section, URL overrides).
      2. `board.meta["details_expanded_default"]` when the item still carries
         a `Board` (compile-time path used by the primary SVG
         renderer).

    NOTE: `ResolvedBoard` does not carry `meta`, so a resolved layout item
    with `details_expanded_default: true` that has not been lifted into a
    variable default will render collapsed here. In practice the
    variable-normalization pass (`normalize.variables`) should seed the
    details variable default from meta so the first branch covers it; if
    a future call site exercises this with Resolved items and finds details
    stuck collapsed, wire `details_expanded_default` into
    `ResolvedLayoutItem` instead of adding a second meta dict.
    """
    if item.details_variable in variables:
        return str(variables[item.details_variable]).lower() == "true"
    board = item.board
    if isinstance(board, Board) and board.meta:
        return bool(board.meta.get("details_expanded_default", False))
    return False


def render_details_summary(
    item: LayoutItem | ResolvedLayoutItem,
    variables: VariableValues,
    available_width: float,
    expanded: bool | None = None,
    *,
    resolved_style: ResolvedStyle,
) -> str:
    """Render a collapsible section's summary bar as clickable SVG.

    The summary bar shows a disclosure triangle (▶/▼) and text.
    Clicking navigates to a URL that toggles the expanded state.

    Args:
        item: LayoutItem with details metadata
        variables: Current variable values (for building toggle URL)
        available_width: Width of the summary bar
        expanded: Pre-computed expanded state (avoids recomputing)

    Returns:
        SVG string for the summary bar
    """
    var_name = item.details_variable
    is_expanded = (
        expanded if expanded is not None else is_details_expanded(item, variables)
    )
    arrow = "▼" if is_expanded else "▶"
    label = item.details_expanded_summary if is_expanded else item.details_summary
    new_value = "false" if is_expanded else "true"
    if not var_name:
        raise ValueError(
            "Details summary rendering requires item.details_variable to be set"
        )
    href = _build_toggle_url(variables, var_name, new_value)

    # Summary bar fill from header background (expanded) or stripe (collapsed).
    # No fallback to board background — when the theme omits these, use
    # fill="none" so the border stroke still delineates the summary bar.
    _expanded_fill = resolved_style.chart_defaults.table.header.background
    _collapsed_fill = (
        resolved_style.chart_defaults.table.row.stripe.color
        if resolved_style.chart_defaults.table.row.stripe
        else None
    )
    details_config = resolved_style.layout.details
    summary_height = float(details_config.summary_height)
    _summary_fill = _expanded_fill if is_expanded else _collapsed_fill
    # Always emit the rect so the border stroke is visible; fall back to
    # fill="none" when neither header nor stripe has a background color.
    _summary_rect = (
        f'<rect x="0" y="0" width="{escape_attr(available_width)}" height="{escape_attr(summary_height)}" '
        f'fill="{escape_attr(_summary_fill or "none")}" '  # type-state: silent_fallback — no summary fill authored; fill="none" is the real SVG value the comment above already documents, not a fabricated color
        f'stroke="{escape_attr(resolved_style.border.color)}" stroke-width="{escape_attr(details_config.border.width)}"'
        f"{border_dash_attrs(details_config.border)} "
        f'rx="{escape_attr(details_config.border.radius)}" class="dbt-details-toggle"/>'
    )
    return (
        f'<a href="{escape_attr(href)}">'
        + _summary_rect
        + f'<text x="{escape_attr(details_config.arrow.x)}" y="{escape_attr(summary_height / 2 + details_config.text_baseline_offset)}" font-size="{escape_attr(details_config.arrow.font.size)}" fill="{escape_attr(resolved_style.title.font.color)}">{arrow}</text>'
        + f'<text x="{escape_attr(details_config.label_x)}" y="{escape_attr(summary_height / 2 + details_config.text_baseline_offset)}" font-size="{escape_attr(details_config.font.size)}" '
        + f'fill="{escape_attr(resolved_style.title.font.color)}" font-weight="500">{html.escape(label or "")}</text>'  # type-state: silent_fallback — no summary label authored; empty text paints an empty run, not a fabricated label
        + "</a>"
    )
