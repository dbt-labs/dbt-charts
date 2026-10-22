"""Measured, flowed prose on the board's card grid.

Body text is measured and flowed whether or not it asked for columns. A prose
column is an invisible card: column ``i`` of an N-column block occupies the box
card ``i`` of an N-up ``cols:`` row occupies at the board's width, and its text
is inset by ``card_padding``. Runs of stacked prose share one grid -- the
thirds or the halves of the board -- chosen once by :func:`plan_board_prose`
and handed to both callers through ``ProsePlan``; each block then takes as many
of the grid's spans as its text earns.

This lives outside ``boards.py`` because it is no longer part of board rendering.
``get_markdown_text_height`` reserves the height a board's prose will occupy and
``_render_text_svg`` draws it, and the two must agree exactly -- a disagreement
shows up as clipped or floating text rather than a test failure. They agree by
calling the same function here with the same plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from dbt_charts.core.compile.models.board.authored import LayoutType
from dbt_charts.core.compile.models.board.normalized import ProsePlan, VariableValues
from dbt_charts.core.compile.resolve.style.markdown_style import readable_measure
from dbt_charts.core.compile.sizing import card_row_gap, prose_slot_widths
from dbt_charts.core.render.column_packer import (
    choose_grid,
    columns_earned,
    grid_span,
    span_boxes,
)
from dbt_charts.core.render.conditions import item_is_drawn
from dbt_charts.core.render.svg_utils import (
    divider_dash_attr,
    escape_attr,
    format_svg_numeric,
    px,
    translate_group,
)
from mdsvg import OrderedList, Table, UnorderedList, parse as parse_markdown

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.normalized import Board
    from dbt_charts.core.compile.models.primitives import DividerStyle
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
    from dbt_charts.core.compile.models.style.theme import (
        TextColumnStyle,
        TextStyle,
    )
    from dbt_charts.core.diagnostics.diagnostic import Diagnostic
    from mdsvg import AnyBlock, ListItem
    from mdsvg.fonts import FontFaces
    from mdsvg.renderer import BlockMetrics, SVGRenderer
    from mdsvg.style import Style as MarkdownStyle


def _measure(span_text: float, max_chars: int | None, char_px: float) -> float:
    """Width the text wraps at inside a span.

    The cap trims the measure, not the span: the next column's left edge is
    measured from the span, so the excess lands at the right of each span and
    every left edge stays on a card content edge.
    """
    return min(span_text, readable_measure(char_px, max_chars))


def _group_spans(
    boxes: tuple[tuple[float, float], ...], size: int
) -> tuple[tuple[float, float], ...]:
    """Merge ``boxes`` into equal groups of ``size`` consecutive spans."""
    return tuple(
        (boxes[i][0], boxes[i + size - 1][0] + boxes[i + size - 1][1] - boxes[i][0])
        for i in range(0, len(boxes) - size + 1, size)
    )


def _fit_tables(
    renderer: SVGRenderer,
    tables: list[Table],
    boxes: tuple[tuple[float, float], ...],
    pad: float,
    max_chars: int | None,
    char_px: float,
    slot: float,
) -> tuple[tuple[tuple[float, float], ...], float]:
    """Fewest spans per column, and the table measure, that hold every table unwrapped.

    A table cannot reflow and draws equal columns across whatever width it is
    given, so it is unwrapped once its height stops shrinking. Candidates are
    the whole-span groups of the run's grid, at the capped prose measure first
    and at the group's full width only if no capped measure holds the tables;
    a table wider than the slot keeps the whole slot and wraps.
    """

    def height(table: Table, width: float) -> float:
        return renderer.measure_blocks([table], width=width)[0].line_advances[0]

    unwrapped = [height(table, slot) for table in tables]

    def text_width(group: tuple[tuple[float, float], ...]) -> float:
        return min(box_width for _, box_width in group) - 2 * pad

    for capped in (True, False):
        for size in range(1, len(boxes) + 1):
            group = _group_spans(boxes, size)
            width = text_width(group)
            if capped:
                width = _measure(width, max_chars, char_px)
            if all(
                height(table, width) <= natural
                for table, natural in zip(tables, unwrapped, strict=True)
            ):
                return group, width
    group = _group_spans(boxes, len(boxes))
    return group, text_width(group)


def _measure_offset(align: str, available: float, used: float) -> float:
    """Left offset of the measure block within its card.

    Capping the measure puts a second box between the text and the card, and
    ``align`` belongs to the outer one -- an author centering a block is
    centering it on the card they can see, not on a width the renderer chose.
    """
    slack = max(available - used, 0.0)
    if align == "center":
        return slack / 2.0
    if align == "right":
        return slack
    return 0.0


def _prose_typography(
    text_style: TextStyle, resolved_style: ResolvedStyle
) -> tuple[str, MarkdownStyle, FontFaces, float]:
    """The family, markdown style, faces and one-character pixel width a block uses.

    Planning and rendering both measure through this, so the width a grid is
    chosen at is the width the text is wrapped at.
    """
    from dbt_charts.core.compile.resolve.style.markdown_style import (
        body_text_font_family,
        get_compact_style,
        max_chars_to_px,
    )
    from dbt_charts.core.font_measure import markdown_font_faces

    family = body_text_font_family(resolved_style)
    style = get_compact_style(resolved_style, text_align=text_style.align)
    fonts = markdown_font_faces(family, style)
    char_px = max_chars_to_px(fonts.regular, float(style.base_font_size), 1)
    return family, style, fonts, char_px


def _columns_of(
    blocks: list[AnyBlock],
    renderer: SVGRenderer,
    width: float,
    pad: float,
    col: TextColumnStyle,
    char_px: float,
    grid: int,
    board_width: float,
    gap: float,
) -> tuple[
    tuple[tuple[float, float], ...],
    float,
    float,
    tuple[BlockMetrics, ...],
    list[Table],
    int,
]:
    """Boxes, prose and table measures, metrics, tables and columns of one block.

    The render pass and the board-level plan both decide how many columns a
    block earns through this, at the block's real slot ``width``. Tables alone
    take the width that holds them unwrapped; every other block keeps the
    capped reading measure, so a wide table never stretches its paragraphs.
    """
    boxes = span_boxes(width + 2 * pad, board_width, grid, gap)
    tables = [b for b in blocks if isinstance(b, Table)]
    table_measure: float | None = None
    if tables:
        boxes, table_measure = _fit_tables(
            renderer, tables, boxes, pad, col.max_chars, char_px, width
        )
    # Columns share one measure, so a box a hair short of its span sets it.
    measure = _measure(
        min(box_width for _, box_width in boxes) - 2 * pad, col.max_chars, char_px
    )
    # Every column is equal width, so a block wraps identically wherever it
    # lands -- the line list is sliced, never re-flowed.
    metrics = renderer.measure_blocks(blocks, width=measure)
    if table_measure is not None and table_measure != measure:
        at_table_width = renderer.measure_blocks(blocks, width=table_measure)
        metrics = tuple(
            at_table_width[i] if isinstance(b, Table) else m
            for i, (b, m) in enumerate(zip(blocks, metrics, strict=True))
        )
    spans = len(boxes)
    ceiling = spans if col.max_number is None else min(spans, col.max_number)
    n_cols = columns_earned(sum(len(m.line_advances) for m in metrics), ceiling)
    table_width = measure if table_measure is None else table_measure
    return boxes, measure, table_width, metrics, tables, n_cols


def render_prose_svg(
    markdown_text: str,
    width: float,
    text_style: TextStyle,
    resolved_style: ResolvedStyle,
    plan: ProsePlan | None,
    authored: str,
    base_dir: str | None,
    error_collector: list[Diagnostic] | None,
    allow_raw_html: bool = False,
) -> tuple[str, float]:
    """Render body-text markdown as pure SVG, measured and flowed on the card grid.

    Every prose block comes through here, whether or not it asked for columns.
    ``width`` is the slot the text may occupy, already inset by ``card_padding``;
    the container it sits in is ``width + 2 * card_padding``, and the plan says
    which grid that container's spans belong to. A block takes the most of its
    container's spans that each earn ``MIN_LINES_PER_COLUMN`` lines, filling from
    the left, and a single column is one whole span, never a free measure.

    Handles align and multi-column flow entirely in SVG — no foreignObject, no
    browser HTML layout dependency.

    *markdown_text* is the Jinja rendering of *authored*. Its images resolve
    against *base_dir*, the directory of the file the text is written in; a
    bad one degrades the text to its error, reported to *error_collector*
    (``None`` in the sizing pass, so the paint pass alone reports it).

    ``plan`` is the board's ``prose_plan``; it is None only when
    :func:`plan_board_prose` never ran, which is a caller bug.

    Returns (svg_string, height).
    """
    assert plan is not None, (
        "board text must be planned (plan_board_prose) before it is sized or drawn"
    )
    from dbt_charts.core.compile.image_assets import rewrite_board_markdown_images
    from dbt_charts.core.render.column_packer import PackUnit, pack_columns
    from dbt_charts.core.render.font_selection import record_painted_faces
    from mdsvg.renderer import SVGRenderer

    col = text_style.column
    align: Literal["left", "center", "right"] = text_style.align
    effective_family, style, fonts, char_px = _prose_typography(
        text_style, resolved_style
    )

    renderer = SVGRenderer(
        style=style,
        fonts=fonts,
        allow_raw_html=allow_raw_html,
    )

    blocks = rewrite_board_markdown_images(
        list(parse_markdown(markdown_text)),
        authored,
        markdown_text,
        base_dir,
        error_collector,
    )

    pad = float(resolved_style.frame.card_padding)
    boxes, measure, table_measure, metrics, tables, n_cols = _columns_of(
        blocks,
        renderer,
        width,
        pad,
        col,
        char_px,
        plan.grid,
        plan.board_width,
        plan.gap,
    )
    tables_differ = table_measure != measure
    if n_cols == 1 and not tables and not plan.snap:
        # Columns are for text long enough to earn them. On a board where no
        # block goes multi-column, a lone column wraps at least as wide as sizing
        # asked for, so a line that fits stays one line; once it outgrows its
        # span it sits in the whole card. Where some block does, the lone ones
        # keep to their span so every edge on the board is a span edge.
        wanted = min(
            renderer.max_content_width(blocks),
            _measure(width, col.max_chars, char_px),
        )
        if wanted > measure:
            measure = wanted
            boxes = ((0.0, width + 2 * pad),)

    # Alignment belongs to the span the reader sees, not to a width the renderer chose.
    insets = [
        box_x + _measure_offset(align, box_width - 2 * pad, measure)
        for box_x, box_width in boxes
    ]

    if n_cols <= 1 and not tables_differ:
        result = renderer.render_content(blocks, width=measure, padding=0.0)
        inset = insets[0]
        w = format_svg_numeric(width)
        h = format_svg_numeric(result.height)
        body = (
            result.content
            if inset == 0.0
            else f'<g transform="translate({px(inset)}, 0)">{result.content}</g>'
        )
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{escape_attr(w)}" height="{escape_attr(h)}" '
            f'viewBox="0 0 {escape_attr(w)} {escape_attr(h)}">{body}</svg>'
        )
        record_painted_faces(effective_family, renderer.used_faces)
        return svg, result.height

    def block_measure(block: AnyBlock) -> float:
        return table_measure if isinstance(block, Table) else measure

    units: list[PackUnit] = []
    origin: list[tuple[int, int]] = []  # (block index, line index) per unit
    for block_index, m in enumerate(metrics):
        line_total = len(m.line_advances)
        for line_index, advance in enumerate(m.line_advances):
            units.append(
                PackUnit(
                    advance=advance,
                    space_before=m.space_before if line_index == 0 else 0.0,
                    block_id=block_index,
                    line_index=line_index,
                    line_total=line_total,
                    splittable=m.splittable,
                    keep_with_next=m.keep_with_next,
                )
            )
            origin.append((block_index, line_index))

    assignment = pack_columns(units, n_cols)

    # Collapse per-line assignments back into one window per (column, block).
    windows: dict[int, list[tuple[int, int, int]]] = {c: [] for c in range(n_cols)}
    for unit_index, column in enumerate(assignment):
        block_index, line_index = origin[unit_index]
        current = windows[column]
        if current and current[-1][0] == block_index:
            current[-1] = (block_index, current[-1][1], line_index)
        else:
            current.append((block_index, line_index, line_index))

    # Taken off the first rendered window rather than the renderer's private
    # accessor: mdsvg is published separately, so only its public surface is a
    # contract we can hold it to.
    style_block = ""
    column_parts: list[str] = []
    column_heights: list[float] = []

    for ci in range(n_cols):
        y = 0.0
        col_elements: list[str] = []
        for position, (block_index, first, last) in enumerate(windows[ci]):
            m = metrics[block_index]
            # A block opening a column sits at the top: both the gap that would
            # separate it from preceding text and the whitespace it draws above
            # itself collapse, exactly as they do at the top of the board.
            opens_column = position == 0
            if not opens_column:
                y += m.space_before
            lift = m.leading_margin if opens_column else 0.0
            block_width = block_measure(blocks[block_index])
            rendered = renderer.render_block_window(
                blocks[block_index],
                width=block_width,
                start=first,
                count=last - first + 1,
            )
            if not style_block:
                style_block = rendered.style_block
            # A block narrower or wider than the column's measure re-aligns
            # itself within the span; the column itself is placed by `measure`.
            dx = _measure_offset(
                align, boxes[ci][1] - 2 * pad, block_width
            ) - _measure_offset(align, boxes[ci][1] - 2 * pad, measure)
            col_elements.append(
                translate_group(px(dx), px(y - lift), rendered.elements)
            )
            y += sum(m.line_advances[first : last + 1]) - lift
        column_heights.append(y)
        if col_elements:
            column_parts.append(
                translate_group(px(insets[ci]), 0, "".join(col_elements))
            )

    actual_col_height = max(column_heights) if column_heights else 0.0

    if col.divider.visible:
        for x, box_width in boxes[: n_cols - 1]:
            # Midway through the card gap between this span's box and the next;
            # boxes are container-relative and the slot starts `pad` inside it.
            column_parts.append(
                _column_divider_line(
                    x + box_width + plan.gap / 2 - pad, actual_col_height, col.divider
                )
            )

    w = format_svg_numeric(width)
    h = format_svg_numeric(actual_col_height)
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{escape_attr(w)}" height="{escape_attr(h)}">{style_block}{"".join(column_parts)}</svg>'
    record_painted_faces(effective_family, renderer.used_faces)
    return svg, actual_col_height


def _column_divider_line(x: float, height: float, divider: DividerStyle) -> str:
    """Generate a vertical SVG line between prose columns."""
    # Snap x to integer: a 1px structural mark at fractional x blurs horizontally.
    x_snapped = px(x)
    h_s = format_svg_numeric(height)
    sw_s = format_svg_numeric(divider.width)
    return (
        f'<line x1="{escape_attr(x_snapped)}" y1="0" x2="{escape_attr(x_snapped)}" y2="{escape_attr(h_s)}"'
        f' stroke="{escape_attr(divider.color)}" stroke-width="{escape_attr(sw_s)}"{divider_dash_attr(divider.dasharray)}/>'
    )


def _list_items(markdown: str) -> list[ListItem]:
    """Top-level list items of a markdown passage."""
    return [
        item
        for block in parse_markdown(markdown)
        if isinstance(block, (UnorderedList, OrderedList))
        for item in block.items
    ]


def _item_lines(renderer: SVGRenderer, item: ListItem, width: float) -> int:
    """Lines a list item's own text wraps to at ``width``."""
    from mdsvg import Paragraph

    return len(
        renderer.measure_blocks([Paragraph(spans=item.spans)], width=width)[
            0
        ].line_advances
    )


def _run_grid(
    run: list[Board],
    variable_values: VariableValues,
    board_width: float,
    gap: float,
    drawn: set[int],
) -> Literal[1, 2, 3]:
    """Grid for a run: the narrowest any block needs, and halves if it is list-heavy.

    List-heavy means more than half of the run's list items fit on one line at
    the halves measure but wrap at the thirds measure, at the board's width.
    """
    from dbt_charts.core.render.boards import resolve_display_text_from_provider
    from mdsvg.renderer import SVGRenderer

    items = one_line_at_halves = 0
    grid: Literal[1, 2, 3] = 3
    for block in run:
        if id(block) not in drawn:
            continue
        rs = block.resolved_style
        col = rs.text.column
        _, style, fonts, char_px = _prose_typography(rs.text, rs)
        pad = float(rs.frame.card_padding)
        grid = min(grid, choose_grid(board_width, gap, pad, char_px))
        text = resolve_display_text_from_provider(
            block.text, variable_values, resolved_style=rs, base_dir=block.base_dir
        )
        list_items = _list_items(text)
        if not list_items:
            continue
        renderer = SVGRenderer(style=style, fonts=fonts)
        half, third = (
            _measure(grid_span(board_width, n, gap) - 2 * pad, col.max_chars, char_px)
            - style.list_indent
            for n in (2, 3)
        )

        items += len(list_items)
        one_line_at_halves += sum(
            _item_lines(renderer, item, half) == 1
            and _item_lines(renderer, item, third) > 1
            for item in list_items
        )
    return min(grid, 2) if one_line_at_halves * 2 > items else grid


@dataclass
class _Runs:
    """Runs of stacked prose found so far, each with the card gap it sits on."""

    card_gap: float
    variable_values: VariableValues
    runs: list[tuple[list[Board], float]] = field(default_factory=list)
    # Gap of the card layout walked most recently: prose with no card row after
    # it is adjacent to the one above.
    last_gap: float | None = None

    def close(self, run: list[Board], gap: float) -> list[Board]:
        """Finish ``run`` on ``gap``, the gap of the layout that ends or holds it."""
        if run:
            self.runs.append((run, gap))
        return []

    def close_unended(self, run: list[Board], board: Board) -> list[Board]:
        """Finish a run no card row follows: the row above it, else a nested row's gap."""
        gap = self.last_gap
        if gap is None:
            gap = card_row_gap(board, is_root=False, card_gap=self.card_gap)
        return self.close(run, gap)


def _placing_gap(
    board: Board, is_root: bool, found: _Runs, panel: Board | None = None
) -> float:
    """Card gap of the layout ``board`` holds its items in.

    Tab panels are full width and pitch no cards themselves; the cards live in
    the panel, at the panel's own gap. Prose above the tabs takes the active
    panel's, resolved the way sizing and rendering resolve it.
    """
    if board.layout.type == LayoutType.TABS:
        from dbt_charts.core.render.sizing import active_layout_items

        if panel is None:
            active = [
                i.board
                for i in active_layout_items(board.layout, found.variable_values)
                if i.board is not None
            ]
            panel = active[0] if active else board
        return card_row_gap(panel, is_root=False, card_gap=found.card_gap)
    return card_row_gap(board, is_root=is_root, card_gap=found.card_gap)


def _gather_runs(
    board: Board, run: list[Board], found: _Runs, *, is_root: bool
) -> list[Board]:
    """Collect ``board``'s prose into runs of stacked blocks; return the open run.

    A run is prose stacked with nothing between it but more prose, so only a
    chart or a ``cols``/``grid``/``tabs`` layout ends one: those have card edges
    of their own, and their gap is the run's. A card's text is its own run, on
    the gap of the layout holding the card. A run that no card row follows sits
    on the gap of the card row above it.
    """
    if board.text:
        run.append(board)
    if board.layout.type == LayoutType.ROWS:
        for item in board.layout.items:
            if item.board is None:
                run = found.close_unended(run, board)
            else:
                run = _gather_runs(item.board, run, found, is_root=False)
        return run
    gap = _placing_gap(board, is_root, found)
    found.close(run, gap)
    for item in board.layout.items:
        if item.board is not None:
            # Each card starts from the row holding it, not from a card beside it.
            gap_here = _placing_gap(board, is_root, found, item.board)
            found.last_gap = gap_here
            found.close(_gather_runs(item.board, [], found, is_root=False), gap_here)
    found.last_gap = gap
    return []


def _goes_multi_column(
    block: Board,
    slot: float,
    grid: int,
    board_width: float,
    gap: float,
    variable_values: VariableValues,
) -> bool:
    """Whether ``block``, at its real ``slot`` width, lays out in two or more columns."""
    from dbt_charts.core.compile.image_assets import rewrite_board_markdown_images
    from dbt_charts.core.render.boards import resolve_display_text_from_provider
    from mdsvg.renderer import SVGRenderer

    rs = block.resolved_style
    _, style, fonts, char_px = _prose_typography(rs.text, rs)
    renderer = SVGRenderer(
        style=style,
        fonts=fonts,
        allow_raw_html=block.html_policy == "trusted-raw",
    )
    text = resolve_display_text_from_provider(
        block.text, variable_values, resolved_style=rs, base_dir=block.base_dir
    )
    # The same degradation the sizing pass applies: a bad image becomes its error.
    blocks = rewrite_board_markdown_images(
        list(parse_markdown(text)), block.text, text, block.base_dir, None
    )
    pad = float(rs.frame.card_padding)
    *_, n_cols = _columns_of(
        blocks, renderer, slot, pad, rs.text.column, char_px, grid, board_width, gap
    )
    return n_cols > 1


def _drawn_text(board: Board, variable_values: VariableValues) -> set[int]:
    """``id`` of every board whose text is actually drawn.

    A tabs layout draws its active panel only, a collapsed details item draws
    its summary only, and a hidden item draws nothing.
    """
    from dbt_charts.core.render.sizing import (
        active_layout_items,
        is_item_collapsed_summary,
    )

    drawn: set[int] = set()
    pending = [board]
    for node in pending:
        if node.text:
            drawn.add(id(node))
        for item in active_layout_items(node.layout, variable_values):
            if item.board is None or is_item_collapsed_summary(item, variable_values):
                continue
            pending.append(item.board)  # noqa: B909 — queue growth: BFS over the board tree
    return drawn


def plan_board_prose(
    board: Board, variable_values: VariableValues, board_width: float
) -> None:
    """Choose each run of prose's grid once and stamp it on every board in the run.

    Runs ahead of both sizing and rendering, which read ``board.prose_plan``
    instead of deciding anything themselves: two decisions over the same text
    would be free to disagree. Idempotent, so a board rendered again at another
    width is simply re-planned.

    ``board_width`` is the root board's content width: the width its cards
    (and so its prose columns) divide.
    """
    card_gap = float(board.resolved_style.frame.card_gap) if board.card_gap else 0.0
    found = _Runs(card_gap, variable_values)
    found.close_unended(_gather_runs(board, [], found, is_root=True), board)
    slots = prose_slot_widths(board, board_width, variable_values, item_is_drawn)
    drawn = _drawn_text(board, variable_values)
    grids: list[tuple[list[Board], Literal[1, 2, 3], float]] = [
        (run, _run_grid(run, variable_values, board_width, gap, drawn), gap)
        for run, gap in found.runs
    ]
    # Board-scoped: one block that really lays out in columns, at the slot the
    # layout gives it, snaps every lone column on the board to its span. Only
    # text that is drawn can do it.
    snap = any(
        _goes_multi_column(
            block, slots[id(block)], grid, board_width, gap, variable_values
        )
        for run, grid, gap in grids
        for block in run
        if id(block) in drawn
    )
    for run, grid, gap in grids:
        plan = ProsePlan(grid, board_width, gap, snap)
        for block in run:
            block.prose_plan = plan
