"""Compile-time sizing primitives: pure functions of Board/theme config.

Everything here reads only authored config (Board fields, including the baked
resolved_style) or authored strings — no query data, no executor, no
render-phase measurement. Data-aware sizing (Vega sizing, table row counts)
lives in render/layout_sizing.py; the render-time assignment pass in
render/sizing.py builds on these primitives so width arithmetic has a single
source.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import NamedTuple

from dbt_charts.core.compile.conditions import DrawnPredicate, drawn_items, is_drawn
from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.models.board.normalized import (
    Board,
    Layout,
    LayoutItem,
    VariableValues,
)
from dbt_charts.core.compile.models.chart.normalized import Chart, TableChart
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedStyle,
    effective_padding,
)
from dbt_charts.core.compile.models.style.theme.page import FooterStyle
from dbt_charts.core.compile.resolve import preferred_chart_width
from dbt_charts.core.compile.resolve.style.markdown_style import (
    prose_content_widths,
    title_max_content_width,
)
from dbt_charts.core.font_measure import get_font_measurer, get_weighted_font_measurer
from dbt_charts.core.text.format_d3 import portable_strftime

DEFAULT_GRID_COLUMNS = 24


class IntrinsicWidth(NamedTuple):
    """What a contributor needs horizontally, as CSS intrinsic sizing.

    ``min`` is the width it must have to draw on one line (a title, the widest
    unbreakable word), or None when it has no minimum. ``preferred`` is the
    width it asks for, or None when it is flexible and fills whatever it is
    given (prose).
    """

    min: float | None
    preferred: float | None

    def inset(self, chrome: float) -> IntrinsicWidth:
        """This width inside ``chrome`` px of padding and margin; absent stays absent."""
        return IntrinsicWidth(
            None if self.min is None else self.min + chrome,
            None if self.preferred is None else self.preferred + chrome,
        )


NO_WIDTH = IntrinsicWidth(None, None)


def combine_max(*widths: IntrinsicWidth) -> IntrinsicWidth:
    """Contributors sharing one box: it must fit the widest of each field."""
    mins = [w.min for w in widths if w.min is not None]
    preferreds = [w.preferred for w in widths if w.preferred is not None]
    return IntrinsicWidth(max(mins, default=None), max(preferreds, default=None))


def config_chart_width(
    chart: Chart,
    _resolved_style: ResolvedStyle,
    chart_style_context: ChartStyleContext,
) -> IntrinsicWidth:
    """The data-free chart answer: authored width, else the theme family estimate."""
    return IntrinsicWidth(None, preferred_chart_width(chart, chart_style_context))


ChartWidth = Callable[[Chart, ResolvedStyle, ChartStyleContext], IntrinsicWidth]
"""A chart's intrinsic width, given its owning board's style and context."""


@dataclass(frozen=True)
class WidthMeasurer:
    """How contributors answer "what is your intrinsic width?".

    A board's own content (title, prose, root footer) is measured from
    compile-time typography, with ``variable_values`` resolving the Jinja a title
    or prose block interpolates. ``chart`` is the one seam: the data-free
    default is the config width, and the data-aware sizing pass injects one that
    asks tables for their column widths. It receives the owning board's style
    and context. ``drawn`` decides which items take space: the render pass
    injects the one that also reads query-backed ``visible:`` probes.
    """

    variable_values: VariableValues
    chart: ChartWidth = config_chart_width
    drawn: DrawnPredicate = is_drawn

    def board_own_width(self, board: Board, is_root: bool) -> IntrinsicWidth:
        """What ``board``'s own content needs, inside its page margin.

        A title never wraps, so it needs its one-line width at least and asks
        for it. Prose needs its widest unbreakable word and asks for its one-line
        width up to the theme's readable line length. The footer row is a
        minimum of the root alone: a nested board's footer is never drawn.
        """
        style = board.resolved_style
        inset = 2 * float(style.frame.card_padding)
        parts = [NO_WIDTH]
        if board.title:
            one_line = title_max_content_width(
                board.title,
                board.level,
                style,
                self.variable_values,
                board.base_dir,
            )
            parts.append(IntrinsicWidth(one_line, one_line).inset(inset))
        if board.text:
            floor, one_line = prose_content_widths(
                board.text, style, self.variable_values, board.base_dir
            )
            parts.append(
                IntrinsicWidth(
                    floor, min(one_line, float(style.text.preferred_width))
                ).inset(inset)
            )
        if is_root:
            parts.append(IntrinsicWidth(_footer_row_width(board), None))
        return combine_max(*parts)


# A fixed instant, so the floor never depends on when the board renders. The
# timestamp is drawn with tabular digits, so only the format's words (month,
# zone) vary in width; late December is among the widest.
_SAMPLE_TIMESTAMP = datetime(2026, 12, 28, 22, 22, tzinfo=timezone.utc)


FOOTER_BRAND_WORD_RE = re.compile(r"\bdbt charts\b", re.IGNORECASE)


def footer_text_width(footer: FooterStyle, font_family: str) -> float:
    """Width of the footer attribution: the brand phrase one weight heavier than the words around it."""
    # FooterStyle._require_font_size_and_color guarantees both non-None.
    assert footer.font.size is not None
    size = float(footer.font.size)
    measure = get_font_measurer(font_family).measure
    match = FOOTER_BRAND_WORD_RE.search(footer.text)
    if match is None:
        return measure(footer.text, size)
    brand_weight = get_chart_rendering().frame.footer_brand_weight
    brand = get_weighted_font_measurer(font_family, brand_weight).measure(
        match.group(0), size
    )
    prefix = footer.text[: match.start()].rstrip()
    suffix = footer.text[match.end() :].lstrip()
    gap = measure(" ", size)
    return (
        brand
        + (gap + measure(prefix, size) if prefix else 0.0)
        + (measure(suffix, size) + gap if suffix else 0.0)
    )


def _footer_row_width(board: Board) -> float:
    """Attribution, plus the timestamp and its gap when it shares the row."""
    style = board.resolved_style
    if not style.footer.visible:
        return 0.0
    family = str(style.font.family)
    width = footer_text_width(style.footer, family)
    stamp = style.timestamp
    if stamp.visible and stamp.position == "footer":
        assert stamp.font.size is not None, "TimestampStyle guarantees font.size"
        width += get_chart_rendering().frame.footer_timestamp_gap_px
        width += get_font_measurer(family).measure(
            portable_strftime(_SAMPLE_TIMESTAMP, stamp.format), float(stamp.font.size)
        )
    return width


def table_width_is_pinned(chart: TableChart) -> bool:
    """True when the table's own style fixes its preferred width.

    Chart scope is the author's statement about this one table, like a chart
    ``width:``; a board- or theme-level family value only estimates the width
    before data is known.
    """
    return chart.style is not None and "preferred_width" in chart.style.model_fields_set


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


def _fold_layout(
    layout: Layout, items: list[LayoutItem], values: list[float | None], gap: float
) -> float | None:
    """One axis (min or preferred) of a layout from its items'; None is absent.

    rows/tabs take the widest item; cols sum them. Gaps count only between
    present items, so an item with nothing to say neither widens the board
    nor adds a gap. grid is the equal-track arithmetic: the widest per-track
    demand, spread over every column.
    """
    present = [value for value in values if value is not None]
    if not present:
        return None
    if layout.type == "cols":
        return sum(present) + gap * (len(present) - 1)
    if layout.type == "grid":
        columns = layout.columns if layout.columns is not None else DEFAULT_GRID_COLUMNS
        required_track_width = 0.0
        has_measured_item = False
        for item, width in zip(items, values, strict=True):
            if width is None:
                continue
            start = item.col if item.col is not None else 0
            span = item.col_span if item.col_span is not None else 1
            end = min(start + span, columns)
            if end <= start:
                continue
            measured_span = end - start
            internal_gaps = gap * (measured_span - 1)
            required_track_width = max(
                required_track_width,
                max(width - internal_gaps, 0.0) / measured_span,
            )
            has_measured_item = True
        if not has_measured_item:
            return None
        return grid_span_width(required_track_width, columns, gap)
    return max(present)


def layout_intrinsic_width(
    layout: Layout,
    resolved_style: ResolvedStyle,
    chart_style_context: ChartStyleContext,
    *,
    max_content_width: float,
    gap: float,
    card_gap: float,
    measurer: WidthMeasurer,
) -> IntrinsicWidth:
    """A layout's intrinsic width: its items' widths combined per layout type.

    ``measurer`` supplies each chart's and each board's own width. The default
    is pure config (authored chart width, chart-local style patch, theme family
    default); the sizing pass injects one that asks tables for their column
    widths. Its ``chart`` receives the owning board's style and context at each
    nesting level. ``chart_style_context`` supplies the
    chart-local-style-patch merge; ``resolved_style`` supplies board chrome
    for nested boards.
    """

    def item_width(item: LayoutItem) -> IntrinsicWidth:
        if item.user_width is not None and not (
            layout.type == "rows" and str(item.user_width).strip().endswith("%")
        ):
            # A percentage width on a rows item means a fraction of the row
            # (rows_item_width resolves it at assignment); feeding it into
            # the hug too would compound the two into a quarter-width slot.
            # Tabs items cannot carry a width (TabItem has no such field).
            explicit = parse_dimension(item.user_width, max_content_width)
            if explicit is not None:
                return IntrinsicWidth(None, explicit)
        if item.chart is not None:
            return measurer.chart(item.chart, resolved_style, chart_style_context)
        if item.board is None:
            return NO_WIDTH
        return board_intrinsic_width(
            item.board,
            False,
            max_content_width=max_content_width,
            card_gap=card_gap,
            measurer=measurer,
        )

    items = drawn_items(layout.items, measurer.variable_values, measurer.drawn)
    widths = [item_width(item) for item in items]
    effective_gap = gap + card_gap
    return IntrinsicWidth(
        _fold_layout(layout, items, [w.min for w in widths], effective_gap),
        _fold_layout(layout, items, [w.preferred for w in widths], effective_gap),
    )


def board_intrinsic_width(
    board: Board,
    is_root: bool,
    *,
    max_content_width: float,
    card_gap: float,
    measurer: WidthMeasurer,
) -> IntrinsicWidth:
    """A board's own content combined with its layout; nested ones add their chrome.

    Own content (title, prose, root footer) is a min, never a preferred: it
    widens a layout that already has charts to size to, but gives a chart-less
    board no width of its own. The root's page margin is the container's.
    """
    width = combine_max(
        measurer.board_own_width(board, is_root),
        layout_intrinsic_width(
            board.layout,
            board.resolved_style,
            board.chart_style_context,
            max_content_width=max_content_width,
            gap=get_board_gap(board),
            card_gap=card_gap,
            measurer=measurer,
        ),
    )
    if is_root:
        return width
    style = board.resolved_style
    margin = style.margin.horizontal if style.margin is not None else 0.0
    return width.inset(effective_padding(style).horizontal + margin)


def board_container_width(
    board: Board,
    variable_values: VariableValues,
    chart: ChartWidth = config_chart_width,
    drawn: DrawnPredicate = is_drawn,
) -> float:
    """Final board container width: ``frame.width`` exactly, else the hug.

    ``width`` set anywhere in the cascade (board, extends template, meta.yml
    — root ``width:`` sugar lands in the same slot) is the board's exact
    width; the layout distributes it. Without one, the board hugs what its
    items prefer but never below its minimum, bounded by ``frame.max_width``,
    so a lone KPI does not sit in a full-width card. A board that prefers
    nothing (no charts, no text) takes the full ``max_width``.

    Single source for the container computation shared by the render sizing
    pass. ``variable_values`` resolve the Jinja a title or prose block
    interpolates; ``chart`` is the data-aware sizing pass's seam for measuring
    tables from their data; ``drawn`` is the one for which items take space.
    """
    frame = board.resolved_style.frame
    if frame.width is not None:
        return float(frame.width)
    max_container_width = float(frame.max_width)
    max_content_width = max(max_container_width - 2 * float(frame.margin), 0.0)
    width = board_intrinsic_width(
        board,
        True,
        max_content_width=max_content_width,
        card_gap=float(frame.card_gap) if board.card_gap else 0.0,
        measurer=WidthMeasurer(variable_values, chart, drawn),
    )
    preferred = max_content_width if width.preferred is None else width.preferred
    minimum = 0.0 if width.min is None else width.min
    return min(max_container_width, max(minimum, preferred) + 2 * float(frame.margin))


def nested_content_geometry(nested: Board, item_width: float) -> tuple[float, float]:
    """``(content width, child gap)`` of a nested board placed ``item_width`` wide.

    The one rule for how a nested board's own layout is sized: its content sits
    inside its padding and margin, and its items are pitched by its own
    ``style.gap`` (0 when unset). Sizing, slot lookup and prose planning all
    read it here.
    """
    style = nested.resolved_style
    content = (
        item_width
        - effective_padding(style).horizontal
        - (style.margin.horizontal if style.margin else 0.0)
    )
    return content, style.gap if style.gap is not None else 0.0


def _layout_item_widths(
    layout: Layout,
    items: list[LayoutItem],
    available_width: float,
    gap: float,
    card_gap: float,
) -> list[float]:
    """Slot width per drawn item, mirroring the sizing pass's width assignment.

    rows give an item its authored width (capped at the row) or the full
    available width; tabs give every item the full available width; cols
    partition it via resolve_cols_widths; grid uses equal tracks so an item's
    width depends only on its col_span, not its position.
    """
    effective_gap = gap + card_gap
    if layout.type == "rows":
        return [rows_item_width(item, available_width) for item in items]
    if layout.type == "cols":
        n = len(items)
        content_width = available_width - effective_gap * (n - 1)
        specified = [parse_dimension(item.user_width, content_width) for item in items]
        return resolve_cols_widths(specified, content_width, n)
    if layout.type == "grid":
        columns = layout.columns if layout.columns is not None else DEFAULT_GRID_COLUMNS
        col_width = (available_width - effective_gap * (columns - 1)) / columns
        return [
            grid_span_width(col_width, item_grid_span(item)[0], effective_gap)
            for item in items
        ]
    return [available_width] * len(items)


def prose_slot_widths(
    board: Board,
    content_width: float,
    variable_values: VariableValues,
    drawn: DrawnPredicate,
) -> dict[int, float]:
    """The px slot each text-bearing board in the tree gives its prose, from config alone.

    Keyed by ``id(board)``. Mirrors the width arithmetic of the sizing pass --
    which never depends on query data or measured heights -- so the slot is the
    one the text is sized and drawn at. ``content_width`` is the root board's
    content width; a nested board's text sits inside its padding, margin and
    ``card_padding``. A hidden item takes no slot, as in the sizing pass.
    """
    slots: dict[int, float] = {}
    card_gap = float(board.resolved_style.frame.card_gap) if board.card_gap else 0.0

    def visit(node: Board, available: float, gap: float) -> None:
        if node.text:
            pad = float(node.resolved_style.frame.card_padding)
            slots[id(node)] = max(available - 2 * pad, 1.0)
        items = drawn_items(node.layout.items, variable_values, drawn)
        widths = _layout_item_widths(node.layout, items, available, gap, card_gap)
        for item, width in zip(items, widths, strict=True):
            if item.board is not None:
                visit(item.board, *nested_content_geometry(item.board, width))

    visit(board, content_width, get_board_gap(board))
    return slots


def rows_item_width(item: LayoutItem, available_width: float) -> float:
    """A rows item's slot width: its authored width (capped at the row), else the row.

    An authored ``width:`` — on the layout item or on the chart itself
    (cartesian/pie/geo families, the same authored field
    ``preferred_chart_width`` reads) — is honored directly: an absolute
    width is a fixed footprint, and a percentage is a fraction of the row,
    the same meaning it has in cols. Percentages are excluded from the
    content-hug measurement (see ``layout_intrinsic_width``) so the
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
