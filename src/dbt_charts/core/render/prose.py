"""Measured, flowed prose: the engine both rendering and sizing go through.

Body text is measured and flowed whether or not it asked for columns. The
renderer picks a column count whose measure reads well and how much text there
is decides how many of those columns are worth using; an author narrows that
choice with ``style.text.column`` rather than switching it on.

This lives outside ``boards.py`` because it is no longer part of board rendering.
``get_markdown_text_height`` reserves the height a board's prose will occupy and
``_render_text_svg`` draws it, and the two must agree exactly -- a disagreement
shows up as clipped or floating text rather than a test failure. They agree by
calling the same function here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from dbt_charts.core.render.column_packer import MAX_COLUMN_COUNT
from dbt_charts.core.render.svg_utils import (
    escape_attr,
    format_svg_numeric,
    px,
    translate_group,
)

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
    from dbt_charts.core.compile.models.style.theme import (
        ColumnRuleStyle,
        TextColumnStyle,
        TextStyle,
    )
    from dbt_charts.core.render.column_packer import MeasurePlan
    from mdsvg.fonts import FontFace
    from mdsvg.style import Style as MarkdownStyle

# Deliberately hard-coded (Dave, 2026-08-10) -- not exposed to dbt_charts.yml.
# Gutter between columns, in line boxes, used when an authored column.gap
# (pixels) is unset.
_DEFAULT_GUTTER_LINE_BOXES = 1.5


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


def _plan_text_columns(
    col: TextColumnStyle,
    available: float,
    gutter: float,
    face: FontFace,
    style: MarkdownStyle,
    ceiling: int,
) -> MeasurePlan:
    """Decide how many columns the slot gets, and how wide each one is.

    ``max_chars`` is an author overriding the shipped measure, so their width is
    used exactly and the count follows from it. Otherwise the renderer's own
    opinion picks both. ``ceiling`` caps the count -- the authored
    ``max_number`` before the text is measured, then the count the text can
    actually fill.
    """
    from dbt_charts.core.render.column_packer import MeasurePlan, plan_measure
    from dbt_charts.core.render.sizing import max_chars_to_px

    font_size = float(style.base_font_size)
    if col.max_chars is not None:
        col_width = max_chars_to_px(face, font_size, col.max_chars)
        count = min(max(1, int((available + gutter) // (col_width + gutter))), ceiling)
        # An authored measure overrides the shipped one, not the card holding it:
        # the containing SVG is a viewport, so a column wider than its slot is
        # painted over its neighbors rather than spilling harmlessly.
        fits = (available - (count - 1) * gutter) / count
        return MeasurePlan(columns=count, column_width=min(col_width, fits))
    return plan_measure(
        available,
        char_width=max_chars_to_px(face, font_size, 1),
        gutter=gutter,
        ceiling=ceiling,
    )


def render_prose_svg(
    markdown_text: str,
    width: float,
    text_style: TextStyle,
    resolved_style: ResolvedStyle,
    allow_raw_html: bool = False,
) -> tuple[str, float]:
    """Render body-text markdown as pure SVG, measured and flowed.

    Every prose block comes through here, whether or not it asked for columns:
    the measure is the renderer's opinion, not an opt-in. A block that wants
    only one column still gets one of a readable width, with the rest of the
    slot left white, because a 174-character line is unreadable at any length.

    Handles align and multi-column flow entirely in SVG — no foreignObject, no
    browser HTML layout dependency.

    Returns (svg_string, height).
    """
    from dbt_charts.core.font_measure import markdown_font_faces
    from dbt_charts.core.render.font_selection import record_painted_faces
    from dbt_charts.core.render.sizing import (
        body_text_font_family,
        get_compact_style,
    )
    from mdsvg import parse as parse_markdown
    from mdsvg.renderer import SVGRenderer

    col = text_style.column

    _align: Literal["left", "center", "right"] = text_style.align
    effective_family = body_text_font_family(resolved_style)

    style = get_compact_style(resolved_style, text_align=_align)
    fonts = markdown_font_faces(effective_family, style)

    # A gutter is a typographic measure, not a layout gap: express it in line
    # boxes so it scales with the type it separates. layout.rows.gap is 8px --
    # a row gap doing a gutter's job, far too tight between wide columns.
    line_box = float(style.base_font_size) * float(style.line_height)
    column_gap = (
        col.gap if col.gap is not None else _DEFAULT_GUTTER_LINE_BOXES * line_box
    )

    authored_ceiling = (
        col.max_number if col.max_number is not None else MAX_COLUMN_COUNT
    )
    plan = _plan_text_columns(
        col, width, column_gap, fonts.regular, style, authored_ceiling
    )
    n_cols, per_col_width = plan.columns, plan.column_width

    # fetch_image_sizes=False: see comment in _render_text_svg.
    renderer = SVGRenderer(
        style=style,
        fonts=fonts,
        fetch_image_sizes=False,
        allow_raw_html=allow_raw_html,
    )

    blocks = list(parse_markdown(markdown_text))

    if n_cols <= 1:
        result = renderer.render_content(blocks, width=per_col_width, padding=0.0)
        offset = _measure_offset(_align, width, per_col_width)
        w = format_svg_numeric(width)
        h = format_svg_numeric(result.height)
        body = (
            result.content
            if offset == 0.0
            else f'<g transform="translate({px(offset)}, 0)">{result.content}</g>'
        )
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{escape_attr(w)}" height="{escape_attr(h)}" '
            f'viewBox="0 0 {escape_attr(w)} {escape_attr(h)}">{body}</svg>'
        )
        record_painted_faces(effective_family, renderer.used_faces)
        return svg, result.height

    # Measure every block's lines once, then let the packer choose the breaks.
    # Columns are equal width, so a block wraps identically wherever it lands --
    # the line list is sliced, never re-flowed.
    from dbt_charts.core.render.column_packer import (
        PackUnit,
        pack_columns,
        viable_column_count,
    )

    metrics = renderer.measure_blocks(blocks, width=per_col_width)

    # Width says how many columns fit; the amount of text says how many are
    # worth using. Re-derive the geometry if the count steps down.
    total_lines = sum(m.line_count for m in metrics)
    viable = viable_column_count(total_lines, n_cols)
    if viable != n_cols:
        plan = _plan_text_columns(col, width, column_gap, fonts.regular, style, viable)
        n_cols, per_col_width = plan.columns, plan.column_width
        # One step, deliberately. Stepping the count down makes each column
        # wider, so the re-measured line total falls and viability can get
        # worse -- a short passage can still end up with a column under the
        # floor. Iterating to a fixed point would trade that for a jumpier
        # count on small edits; the cost here is a slightly short column.
        metrics = renderer.measure_blocks(blocks, width=per_col_width)

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
        col_x = ci * (per_col_width + column_gap)
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
                width=per_col_width,
                start=first,
                count=last - first + 1,
            )
            if not style_block:
                style_block = rendered.style_block
            col_elements.append(translate_group(0, px(y - lift), rendered.elements))
            y += (last - first + 1) * m.line_advance - lift
        column_heights.append(y)
        if col_elements:
            column_parts.append(translate_group(px(col_x), 0, "".join(col_elements)))

    actual_col_height = max(column_heights) if column_heights else 0.0

    for ci in range(n_cols - 1):
        if col.rule:
            col_x = ci * (per_col_width + column_gap)
            column_parts.append(
                _column_rule_line(
                    col_x + per_col_width + column_gap / 2, actual_col_height, col.rule
                )
            )

    total_width = n_cols * per_col_width + (n_cols - 1) * column_gap
    offset = _measure_offset(_align, width, total_width)
    columns_svg = "".join(column_parts)
    if offset:
        columns_svg = f'<g transform="translate({px(offset)}, 0)">{columns_svg}</g>'
    w = format_svg_numeric(width)
    h = format_svg_numeric(actual_col_height)
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{escape_attr(w)}" height="{escape_attr(h)}">{style_block}{columns_svg}</svg>'
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
