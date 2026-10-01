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
from dbt_charts.core.compile.sizing import card_row_gap
from dbt_charts.core.render.column_packer import (
    MAX_CHARS,
    choose_grid,
    columns_earned,
    grid_span,
    span_boxes,
)
from dbt_charts.core.render.svg_utils import (
    escape_attr,
    format_svg_numeric,
    px,
    translate_group,
)
from mdsvg import OrderedList, Table, UnorderedList, parse as parse_markdown

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.normalized import Board
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
    from dbt_charts.core.compile.models.style.theme import (
        ColumnRuleStyle,
        TextStyle,
    )
    from mdsvg import ListItem
    from mdsvg.fonts import FontFaces
    from mdsvg.renderer import SVGRenderer
    from mdsvg.style import Style as MarkdownStyle


def _measure(span_text: float, max_chars: int | None, char_px: float) -> float:
    """Width the text wraps at inside a span.

    The cap trims the measure, not the span: the next column's left edge is
    measured from the span, so the excess lands at the right of each span and
    every left edge stays on a card content edge.
    """
    return min(span_text, (MAX_CHARS if max_chars is None else max_chars) * char_px)


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
    """Fewest spans per column, and the measure, that hold every table unwrapped.

    A table cannot reflow and draws equal columns across whatever width it is
    given, so it is unwrapped once its height stops shrinking. Candidates are
    the whole-span groups of the run's grid, at the capped prose measure first
    and at the group's full width only if no capped measure holds the tables;
    a table wider than the slot keeps the whole slot and wraps.
    """

    def height(table: Table, width: float) -> float:
        return renderer.measure_blocks([table], width=width)[0].line_advance

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
    from dbt_charts.core.font_measure import markdown_font_faces
    from dbt_charts.core.render.sizing import (
        body_text_font_family,
        get_compact_style,
        max_chars_to_px,
    )

    family = body_text_font_family(resolved_style)
    style = get_compact_style(resolved_style, text_align=text_style.align)
    fonts = markdown_font_faces(family, style)
    char_px = max_chars_to_px(fonts.regular, float(style.base_font_size), 1)
    return family, style, fonts, char_px


def render_prose_svg(
    markdown_text: str,
    width: float,
    text_style: TextStyle,
    resolved_style: ResolvedStyle,
    plan: ProsePlan | None,
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

    ``plan`` is the board's ``prose_plan``; it is None only when
    :func:`plan_board_prose` never ran, which is a caller bug.

    Returns (svg_string, height).
    """
    assert plan is not None, (
        "board text must be planned (plan_board_prose) before it is sized or drawn"
    )
    from dbt_charts.core.render.column_packer import PackUnit, pack_columns
    from dbt_charts.core.render.font_selection import record_painted_faces
    from mdsvg.renderer import SVGRenderer

    col = text_style.column
    align: Literal["left", "center", "right"] = text_style.align
    effective_family, style, fonts, char_px = _prose_typography(
        text_style, resolved_style
    )

    # fetch_image_sizes=False: see comment in _render_text_svg.
    renderer = SVGRenderer(
        style=style,
        fonts=fonts,
        fetch_image_sizes=False,
        allow_raw_html=allow_raw_html,
    )

    blocks = list(parse_markdown(markdown_text))

    pad = float(resolved_style.frame.card_padding)
    boxes = span_boxes(width + 2 * pad, plan.board_width, plan.grid, plan.gap)
    tables = [b for b in blocks if isinstance(b, Table)]
    if tables:
        boxes, measure = _fit_tables(
            renderer, tables, boxes, pad, col.max_chars, char_px, width
        )
    else:
        # Columns share one measure, so a box a hair short of its span sets it.
        measure = _measure(
            min(box_width for _, box_width in boxes) - 2 * pad, col.max_chars, char_px
        )
    spans = len(boxes)

    # Every column is equal width, so a block wraps identically wherever it
    # lands -- the line list is sliced, never re-flowed.
    metrics = renderer.measure_blocks(blocks, width=measure)
    ceiling = spans if col.max_number is None else min(spans, col.max_number)
    n_cols = columns_earned(sum(m.line_count for m in metrics), ceiling)

    # Alignment belongs to the span the reader sees, not to a width the renderer chose.
    insets = [
        box_x + _measure_offset(align, box_width - 2 * pad, measure)
        for box_x, box_width in boxes
    ]

    if n_cols <= 1:
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

    units: list[PackUnit] = []
    origin: list[tuple[int, int]] = []  # (block index, line index) per unit
    for block_index, m in enumerate(metrics):
        for line_index in range(m.line_count):
            units.append(
                PackUnit(
                    advance=m.line_advance,
                    space_before=m.space_before if line_index == 0 else 0.0,
                    block_id=block_index,
                    line_index=line_index,
                    line_total=m.line_count,
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
            rendered = renderer.render_block_window(
                blocks[block_index],
                width=measure,
                start=first,
                count=last - first + 1,
            )
            if not style_block:
                style_block = rendered.style_block
            col_elements.append(translate_group(0, px(y - lift), rendered.elements))
            y += (last - first + 1) * m.line_advance - lift
        column_heights.append(y)
        if col_elements:
            column_parts.append(
                translate_group(px(insets[ci]), 0, "".join(col_elements))
            )

    actual_col_height = max(column_heights) if column_heights else 0.0

    if col.rule:
        for x, box_width in boxes[: n_cols - 1]:
            # Midway through the card gap between this span's box and the next;
            # boxes are container-relative and the slot starts `pad` inside it.
            column_parts.append(
                _column_rule_line(
                    x + box_width + plan.gap / 2 - pad, actual_col_height, col.rule
                )
            )

    w = format_svg_numeric(width)
    h = format_svg_numeric(actual_col_height)
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{escape_attr(w)}" height="{escape_attr(h)}">{style_block}{"".join(column_parts)}</svg>'
    record_painted_faces(effective_family, renderer.used_faces)
    return svg, actual_col_height


_COLUMN_RULE_DASHARRAY = {"dotted": "2,2", "dashed": "6,3"}


def _column_rule_line(x: float, height: float, rule: ColumnRuleStyle) -> str:
    """Generate a vertical SVG line from a structured column rule."""
    # Snap x to integer: a 1px structural mark at fractional x blurs horizontally.
    x_snapped = px(x)
    h_s = format_svg_numeric(height)
    sw_s = format_svg_numeric(rule.width)
    extra = (
        f' stroke-dasharray="{escape_attr(_COLUMN_RULE_DASHARRAY[rule.style])}"'
        if rule.style in _COLUMN_RULE_DASHARRAY
        else ""
    )
    return (
        f'<line x1="{escape_attr(x_snapped)}" y1="0" x2="{escape_attr(x_snapped)}" y2="{escape_attr(h_s)}"'
        f' stroke="{escape_attr(rule.color)}" stroke-width="{escape_attr(sw_s)}"{extra}/>'
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

    return renderer.measure_blocks([Paragraph(spans=item.spans)], width=width)[
        0
    ].line_count


def _run_grid(
    run: list[Board], variable_values: VariableValues, board_width: float, gap: float
) -> Literal[1, 2, 3]:
    """Grid for a run: the narrowest any block needs, and halves if it is list-heavy.

    List-heavy means more than half of the run's list items fit on one line at
    the halves measure but wrap at the thirds measure, at the board's width.
    """
    from dbt_charts.core.compile.template.jinja import resolve_jinja_template
    from mdsvg.renderer import SVGRenderer

    items = one_line_at_halves = 0
    grid: Literal[1, 2, 3] = 3
    for block in run:
        rs = block.resolved_style
        col = rs.text.column
        _, style, fonts, char_px = _prose_typography(rs.text, rs)
        pad = float(rs.frame.card_padding)
        grid = min(grid, choose_grid(board_width, gap, pad, char_px))
        text = resolve_jinja_template(block.text, variable_values, strict=False)
        list_items = _list_items(text)
        if not list_items:
            continue
        renderer = SVGRenderer(style=style, fonts=fonts, fetch_image_sizes=False)
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
    for run, gap in found.runs:
        grid = _run_grid(run, variable_values, board_width, gap)
        plan = ProsePlan(grid, board_width, gap)
        for block in run:
            block.prose_plan = plan
