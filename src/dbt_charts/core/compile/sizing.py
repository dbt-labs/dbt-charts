"""Compile-time sizing primitives: pure functions of Board/theme config.

Everything here reads only authored config (Board fields, including the baked
resolved_style) or authored strings — no query data, no executor, no
render-phase measurement. Data-aware sizing (Vega sizing, table row counts)
lives in render/layout_sizing.py; the render-time assignment pass in
render/sizing.py builds on these primitives so width arithmetic has a single
source.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.board.normalized import Board, Layout, LayoutItem
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedStyle,
    effective_padding,
)
from dbt_charts.core.compile.resolve import preferred_chart_width

DEFAULT_GRID_COLUMNS = 24


def get_board_gap(board: Board) -> float:
    """Return the row/col/grid gap for a board based on its layout type.

    When card_gap is active the gap is always 0 — spacing is owned by the
    margin, not the individual layout rows/cols/grid.  Otherwise the gap comes
    from the board's resolved style (board/theme-level style.layout.<type>.gap
    overrides), not the global default theme.
    """
    if board.card_gap:
        return 0.0
    _layout = board.resolved_style.layout
    if board.layout.type == "rows":
        return float(_layout.rows.gap)
    if board.layout.type == "cols":
        return float(_layout.cols.gap)
    if board.layout.type == "grid":
        return float(_layout.grid.gap)
    return 0.0


def card_row_gap(board: Board, *, is_root: bool, card_gap: float) -> float:
    """Gap between the cards of the layout ``board`` places, as the sizing pass pitches them.

    A root ``cols``/``grid`` layout takes its own type's gap (``get_board_gap``); any other
    board is sized from its ``style.gap`` (0 when unset). Either way
    ``card_gap`` is added. Prose that must land on card edges asks here instead
    of restating either rule.
    """
    # Only the root layouts that pitch cards themselves; a tab panel is a board
    # of its own, sized like a nested one.
    if is_root and board.layout.type in ("cols", "grid"):
        return get_board_gap(board) + card_gap
    own = board.resolved_style.gap
    return (own if own is not None else 0.0) + card_gap


def parse_dimension(value: str | None, total: float) -> float | None:
    """Parse a dimension string to pixels.

    Supports:
    - Percentages: "30%", "70%" -> fraction of total
    - Pixels: "200px", "200" -> exact pixels
    - None -> returns None (auto-distribute)

    A leaf value parser needed by both compile-time validation and
    render-time layout sizing.

    Args:
        value: Dimension string or None
        total: Total available size for percentage calculations

    Returns:
        Pixel value or None if not specified
    """
    if value is None:
        return None

    value = str(value).strip()

    if value.endswith("%"):
        try:
            percent = float(value[:-1])
            return (percent / 100.0) * total
        except ValueError:
            return None

    if value.endswith("px"):
        try:
            return float(value[:-2])
        except ValueError:
            return None

    try:
        return float(value)
    except ValueError:
        return None


def item_grid_span(item: LayoutItem) -> tuple[int, int]:
    """Return (col_span, row_span) for a grid item, defaulting unset spans to 1."""
    return item.col_span or 1, item.row_span or 1


def grid_span_width(track_width: float, span: int, gap: float) -> float:
    """Return the arranged width of a span in an equal-track grid."""
    return track_width * span + gap * (span - 1)


def resolve_cols_widths(
    specified_widths: list[float | None], content_width: float, item_count: int
) -> list[float]:
    """Resolve column widths, avoiding zero-width auto items in mixed layouts.

    Normal case:
    - explicit widths keep their authored size
    - auto items split the leftover width equally

    Defensive overflow case:
    - when explicit widths consume the full row and auto siblings exist,
      reserve one equal-share slot per auto item
    - scale explicit widths proportionally into the remaining space

    This preserves authored ratios for explicit siblings while avoiding
    unreadable 0px auto columns in over-constrained mixed layouts.
    """
    if item_count == 0:
        return []

    total_specified = sum(width for width in specified_widths if width is not None)
    auto_count = sum(1 for width in specified_widths if width is None)

    if auto_count == 0:
        return [width if width is not None else 0.0 for width in specified_widths]

    remaining_width = content_width - total_specified
    if remaining_width > 0:
        auto_width = remaining_width / auto_count
        return [auto_width if width is None else width for width in specified_widths]

    equal_share = content_width / item_count if item_count > 0 else 0.0
    auto_width = equal_share
    specified_budget = max(content_width - (auto_width * auto_count), 0.0)
    scale = specified_budget / total_specified if total_specified > 0 else 0.0

    return [
        auto_width if width is None else width * scale for width in specified_widths
    ]


def measure_preferred_layout_width(
    layout: Layout,
    resolved_style: ResolvedStyle,
    chart_style_context: ChartStyleContext,
    *,
    max_content_width: float,
    gap: float,
    card_gap: float,
) -> float | None:
    """Measure a layout's intrinsic width from its charts' preferred widths.

    Preferred widths are pure config (authored chart width, chart-local style
    patch, theme family default), so this runs before any chart is resolved —
    chart resolution happens lazily at the final slot widths this measurement
    helps determine. ``chart_style_context`` supplies the chart-local-style-patch
    merge ``preferred_chart_width`` needs; ``resolved_style`` supplies board
    chrome (padding, margin) for nested-board insets.
    """

    def item_width(item: LayoutItem) -> float | None:
        explicit = None
        if item.user_width is not None and not (
            layout.type == "rows" and str(item.user_width).strip().endswith("%")
        ):
            # A percentage width on a rows item means a fraction of the row
            # (rows_item_width resolves it at assignment); feeding it into
            # the hug too would compound the two into a quarter-width slot.
            # Tabs items cannot carry a width (TabItem has no such field).
            explicit = parse_dimension(item.user_width, max_content_width)
        if explicit is not None:
            return explicit
        if item.chart is not None:
            return preferred_chart_width(item.chart, chart_style_context)
        if item.board is None:
            return None

        nested = item.board
        measured = measure_preferred_layout_width(
            nested.layout,
            nested.resolved_style,
            nested.chart_style_context,
            max_content_width=max_content_width,
            gap=get_board_gap(nested),
            card_gap=card_gap,
        )
        if measured is None:
            return None
        padding = effective_padding(nested.resolved_style).horizontal
        margin = (
            nested.resolved_style.margin.horizontal
            if nested.resolved_style.margin is not None
            else 0.0
        )
        return measured + padding + margin

    widths = [item_width(item) for item in layout.items]
    present = [width for width in widths if width is not None]
    if not present:
        return None

    effective_gap = gap + card_gap
    if layout.type in {"rows", "tabs"}:
        return max(present)
    if layout.type == "cols":
        # Gaps count only between measured items — an auto-width item (no chart
        # preference, no explicit width) contributes neither width nor a gap, so
        # it does not widen the content-sized board.
        return sum(present) + effective_gap * (len(present) - 1)
    if layout.type == "grid":
        columns = layout.columns if layout.columns is not None else DEFAULT_GRID_COLUMNS
        required_track_width = 0.0
        has_measured_item = False
        for item, width in zip(layout.items, widths, strict=True):
            if width is None:
                continue
            start = item.col if item.col is not None else 0
            span = item.col_span if item.col_span is not None else 1
            end = min(start + span, columns)
            if end <= start:
                continue
            measured_span = end - start
            internal_gaps = effective_gap * (measured_span - 1)
            required_track_width = max(
                required_track_width,
                max(width - internal_gaps, 0.0) / measured_span,
            )
            has_measured_item = True
        if not has_measured_item:
            return None
        return grid_span_width(required_track_width, columns, effective_gap)
    return max(present)


def board_container_width(board: Board) -> float:
    """Final board container width: ``frame.width`` exactly, else the hug.

    ``width`` set anywhere in the cascade (board, extends template, meta.yml
    — root ``width:`` sugar lands in the same slot) is the board's exact
    width; the layout distributes it. Without one, the board hugs whatever
    its charts prefer, bounded by ``frame.max_width``, so a lone KPI does not
    sit in a full-width card.

    Single source for the container computation shared by the render sizing
    pass and chart_slot_width.
    """
    frame = board.resolved_style.frame
    if frame.width is not None:
        return float(frame.width)
    max_container_width = float(frame.max_width)
    max_content_width = max(max_container_width - 2 * float(frame.margin), 0.0)
    preferred_content_width = measure_preferred_layout_width(
        board.layout,
        board.resolved_style,
        board.chart_style_context,
        max_content_width=max_content_width,
        gap=get_board_gap(board),
        card_gap=float(frame.card_gap) if board.card_gap else 0.0,
    )
    if preferred_content_width is None:
        return max_container_width
    return min(max_container_width, preferred_content_width + 2 * float(frame.margin))


def chart_slot_width(board: Board, chart_id: str) -> float | None:
    """The px slot width ``board``'s layout assigns to ``chart_id``, from config alone.

    Mirrors the per-layout-type width arithmetic of the render-time sizing
    pass — slot widths never depend on query data or measured heights, so the
    value is exact. Returns None when the chart is not placed in the layout:
    an unplaced chart has no slot.
    """
    frame = board.resolved_style.frame
    content_width = max(board_container_width(board) - 2 * float(frame.margin), 0.0)
    card_gap = float(frame.card_gap) if board.card_gap else 0.0
    return _chart_slot_in_layout(
        board.layout, chart_id, content_width, get_board_gap(board), card_gap
    )


def _chart_slot_in_layout(
    layout: Layout,
    chart_id: str,
    available_width: float,
    gap: float,
    card_gap: float,
) -> float | None:
    widths = _layout_item_widths(layout, available_width, gap, card_gap)
    for item, width in zip(layout.items, widths, strict=True):
        if item.chart is not None and item.chart.id == chart_id:
            return width
        if item.board is not None:
            nested = item.board
            nested_style = nested.resolved_style
            inner_width = (
                width
                - effective_padding(nested_style).horizontal
                - (nested_style.margin.horizontal if nested_style.margin else 0.0)
            )
            child_gap = nested_style.gap if nested_style.gap is not None else 0.0
            found = _chart_slot_in_layout(
                nested.layout, chart_id, inner_width, child_gap, card_gap
            )
            if found is not None:
                return found
    return None


def rows_item_width(item: LayoutItem, available_width: float) -> float:
    """A rows item's slot width: its authored width (capped at the row), else the row.

    An authored ``width:`` — on the layout item or on the chart itself
    (cartesian/pie/geo families, the same authored field
    ``preferred_chart_width`` reads) — is honored directly: an absolute
    width is a fixed footprint, and a percentage is a fraction of the row,
    the same meaning it has in cols. Percentages are excluded from the
    content-hug measurement (see ``measure_preferred_layout_width``) so the
    two never compound. Shared with the render sizing pass so the two
    assignments cannot drift.
    """
    parsed = parse_dimension(item.user_width, available_width)
    if parsed is None and item.chart is not None:
        chart_width = item.chart.__dict__.get("width")
        parsed = float(chart_width) if chart_width is not None else None
    if parsed is not None and parsed > 0:
        return min(parsed, available_width)
    return available_width


def _layout_item_widths(
    layout: Layout, available_width: float, gap: float, card_gap: float
) -> list[float]:
    """Slot width per item, mirroring the sizing pass's width assignment.

    rows give an item its authored width (capped at the row) or the full
    available width; tabs give every item the full available width; cols
    partition it via resolve_cols_widths; grid uses equal tracks so an item's
    width depends only on its col_span, not its position.
    """
    effective_gap = gap + card_gap
    if layout.type == "rows":
        return [rows_item_width(item, available_width) for item in layout.items]
    if layout.type == "cols":
        n = len(layout.items)
        content_width = available_width - effective_gap * (n - 1)
        specified = [
            parse_dimension(item.user_width, content_width) for item in layout.items
        ]
        return resolve_cols_widths(specified, content_width, n)
    if layout.type == "grid":
        columns = layout.columns if layout.columns is not None else DEFAULT_GRID_COLUMNS
        col_width = (available_width - effective_gap * (columns - 1)) / columns
        return [
            grid_span_width(col_width, item_grid_span(item)[0], effective_gap)
            for item in layout.items
        ]
    return [available_width] * len(layout.items)
