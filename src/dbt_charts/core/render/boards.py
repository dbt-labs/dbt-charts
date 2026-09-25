"""Board rendering functions.

Stage: RENDER
Purpose: Render board structures (root and nested) to SVG.

This module handles:
- Root board SVG rendering (render_board_svg)
- Nested board SVG rendering (render_nested_board)

Dependencies:
    - .layouts (for layout rendering)
    - .variables_strip / .controls (for variable rendering)
    - .svg_utils (for SVG utilities)
    - .themes (for theme colors)
"""

import html
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from dbt_charts.core.compile.models.board.resolved import ResolvedBoard
from dbt_charts.core.diagnostics import Diagnostic

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
    from dbt_charts.core.compile.models.style.theme import TextStyle
    from dbt_charts.core.compile.models.style.theme.page import FooterStyle
    from dbt_charts.core.compile.models.variable.authored import Variable

from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.style.resolved import (
    effective_padding as _effective_padding,
)
from dbt_charts.core.execute.chart_data_provider import ChartDataProvider
from dbt_charts.core.font_measure import (
    get_font_measurer,
    get_weighted_font_measurer,
)
from dbt_charts.core.render.board_variables import board_variables
from dbt_charts.core.render.chart_interactivity import (
    hover_runtime_attributes,
)
from dbt_charts.core.render.layout_sizing import RenderCache
from dbt_charts.core.render.prose import render_prose_svg
from dbt_charts.core.render.svg_utils import (
    authored_attrs,
    authored_kind_attr,
    border_dash_attrs,
    extract_svg_inner_content,
    format_svg_numeric,
    padded_authoring_content,
    px,
    translate_group,
)
from dbt_charts.core.render.variables_resolve import resolve_controls
from dbt_charts.core.render.variables_strip import (
    StripAlign,
    render_variables_strip_svg,
)
from dbt_charts.core.text.format_d3 import portable_strftime

__all__ = [
    "render_board_svg",
    "render_nested_board",
]

# -----------------------------------------------------------------------------
# Shared rendering helpers (DRY - single source of truth)
# -----------------------------------------------------------------------------


# The brand phrase linked in the footer watermark (case-insensitive, whole-word).
_FOOTER_BRAND_WORD_RE = re.compile(r"\bdbt charts\b", re.IGNORECASE)


def _footer_attribution_svg(
    footer: "FooterStyle", font_family: str, x: float, y: float
) -> tuple[str, float]:
    """Footer attribution ending at (x, y), and its total width.

    The brand phrase is set in type, one weight heavier than the words around
    it, and carries the link when ``footer.link`` is set. The words on either
    side are emitted as separate right-anchored runs.

    Separate runs rather than one <text> with <tspan> children: rasterizers
    disagree about mixed content. Both cairosvg and resvg drop the space before
    a child element and ignore ``text-anchor="end"`` for the child's advance, so
    a single-<text> version renders "made withdbt Charts", overflowing its
    anchor. Positioning each run from a measured width is renderer-independent.

    The brand run is measured at its own weight — a variable font face instanced at
    600 has wider advances than at 400, and measuring with the regular instance
    would let the words behind it overlap.
    """
    # FooterStyle._require_font_size_and_color guarantees both non-None.
    assert footer.font.size is not None and footer.font.color is not None
    size = float(footer.font.size)
    brand_weight = get_chart_rendering().frame.footer_brand_weight
    measure = get_font_measurer(font_family).measure
    brand_measure = get_weighted_font_measurer(font_family, brand_weight).measure

    def run(end_x: float, content: str, *, brand: bool = False) -> str:
        weight = f' font-weight="{brand_weight}"' if brand else ""
        # Snapped (svg_utils.px) for two reasons: a run on a fractional
        # pixel is split across two columns by the rasterizer, and the Rust
        # port interpolates the 600-weight advances ~0.004px away from this
        # one — an integer position is what makes the board sweep's exact
        # chrome-byte comparison hold.
        return (
            f'<text x="{format_svg_numeric(px(end_x))}" y="{format_svg_numeric(y)}" '
            f'text-anchor="end" font-size="{format_svg_numeric(size)}" '
            f'fill="{footer.font.color}" font-family="{font_family}"{weight}>'
            f"{html.escape(content)}</text>"
        )

    match = _FOOTER_BRAND_WORD_RE.search(footer.text)
    if match is None:
        return run(x, footer.text), measure(footer.text, size)

    prefix = footer.text[: match.start()].rstrip()
    brand_word = match.group(0)
    suffix = footer.text[match.end() :].lstrip()
    gap = measure(" ", size)

    parts: list[str] = []
    right = x
    if suffix:
        parts.append(run(right, suffix))
        right -= measure(suffix, size) + gap

    brand_run = run(right, brand_word, brand=True)
    if footer.link:
        brand_run = (
            f'<a class="dbt-footer-link" href="{html.escape(footer.link, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer">{brand_run}</a>'
        )
    parts.insert(0, brand_run)
    right -= brand_measure(brand_word, size)

    if prefix:
        parts.insert(0, run(right - gap, prefix))
        right -= gap + measure(prefix, size)
    return "".join(parts), x - right


def _resolve_jinja(template: str, variables: VariableValues) -> str:
    """Resolve Jinja templates in a string (lenient mode).

    Returns the original string if no Jinja syntax is present.
    """
    if "{{" not in template and "{%" not in template:
        return template
    from dbt_charts.core.compile.template.jinja import resolve_jinja_template

    return resolve_jinja_template(template, variables, strict=False)


_MARKDOWN_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})([^\r\n]*)$")


def _resolve_markdown_jinja(text: str, variables: VariableValues) -> str:
    masked, fences = _mask_markdown_fenced_code(text)
    resolved = _resolve_jinja(masked, variables)
    for token, fence in fences.items():
        resolved = resolved.replace(token, fence)
    return resolved


def _mask_markdown_fenced_code(text: str) -> tuple[str, dict[str, str]]:
    fences: dict[str, str] = {}
    result: list[str] = []
    fence_marker: str | None = None
    fence_lines: list[str] = []

    for line in text.splitlines(keepends=True):
        if fence_marker is not None:
            fence_lines.append(line)
            if _is_markdown_fence_close(line, fence_marker):
                token = f"__DCT_MARKDOWN_FENCE_{len(fences)}__"
                fences[token] = "".join(fence_lines)
                result.append(token)
                fence_marker = None
                fence_lines = []
            continue

        fence_marker = _markdown_fence_marker(line)
        if fence_marker is not None:
            fence_lines = [line]
            continue

        result.append(line)

    if fence_marker is not None:
        result.extend(fence_lines)

    return "".join(result), fences


def _markdown_fence_marker(line: str) -> str | None:
    match = _MARKDOWN_FENCE_RE.match(line)
    if match is None:
        return None
    return match.group(1)


def _is_markdown_fence_close(line: str, opening_marker: str) -> bool:
    marker_char = re.escape(opening_marker[0])
    min_width = len(opening_marker)
    return bool(re.match(rf"^\s*{marker_char}{{{min_width},}}\s*$", line))


def _render_title_svg(
    title: str,
    variables: VariableValues,
    width: float,
    resolved_style: "ResolvedStyle",
    text_align: Literal["left", "center", "right"] = "left",
    level: int = 1,
) -> str:
    """Render a title with Jinja resolution and case transform."""
    from dbt_charts.core.render.svg_utils import render_title
    from dbt_charts.core.text.case import apply_case

    resolved = _resolve_jinja(title, variables)
    # Apply the case transform from style.title.font.case (cascaded from root).
    # Board body markdown (TextStyle.font.case) is explicitly excluded — case
    # transforms on prose blocks would corrupt code spans, links, and emphasis.
    _title_case = resolved_style.title.font.case
    if _title_case is not None:
        resolved = apply_case(resolved, _title_case)
    return render_title(
        resolved,
        width,
        text_align=text_align,
        level=level,
        resolved_style=resolved_style,
    )


def _prose_authoring_padding(
    pad_left: float, pad_right: float, top: float
) -> dict[str, float]:
    """The 4-sided box a prose block may claim around its own ink.

    Horizontal is real on both sides in the common case: ``_build_board_content_items``
    insets title and text to ``x_offset + card_padding`` so they align with chart
    content, and that inset is the block's to claim on both edges. The title-inline
    band is the exception — its right neighbor is the variables column, not the
    card edge, so its caller passes a narrower ``pad_right`` there instead.

    ``top`` is that same inset on the other axis, and only the block that opens
    a band gets it — ``compute_board_content_box`` allocates it once, before the
    first element. Blocks after it stack flush (a deliberate 0 gap — the
    after-heading rhythm lives in the heading's own margin), so a second block
    claiming ``card_padding`` would reach past where its neighbor's glyphs
    begin and the two selection marks would overlap.

    Bottom stays zero: the gaps below a band separate it from what follows, and
    are laid out by the gap arithmetic rather than claimed here.
    """
    return {"left": pad_left, "right": pad_right, "top": top, "bottom": 0.0}


def _tagged_authoring_block(
    content: str,
    attrs: str,
    content_x: float,
    content_y: float,
    content_width: float,
    content_height: float,
    pad_left: float,
    pad_right: float,
    top_padding: float,
    line_box: tuple[float, float] | None = None,
) -> str:
    """A tagged authoring group whose own box contains ``content``'s padding.

    ``content_x``/``content_y`` are where the content itself sits; the group
    starts one padding earlier on each axis and translates the content back in,
    so the ink lands exactly where it did untagged while the tagged box — the one
    a host traces — is the padded outer one. The two offsets are equal and
    opposite by construction here, which is the point: written out per call site
    they are free to stop cancelling.

    ``pad_left``/``pad_right`` are separate, not one ``card_padding`` for both
    sides, because a block's neighbors can differ: the two stacked call sites
    (a standalone title/text/header) have the card edge on both sides and pass
    the same value twice, but the title-inline band's title column has the
    variables strip for a right neighbor and must claim no more than the gap
    to it — passing the same padding on both sides there would let the box
    reach past where the strip begins.

    ``line_box`` is ``(top, height)`` — the text's own span inside a block that
    reserved more height than its text fills. A heading is that case, and a mark
    traced on the reservation rides above the words by the difference. It seats
    the mark rect alone: the hit target keeps the whole block, and the content
    keeps its position, so nothing that paints moves.
    """
    pad = _prose_authoring_padding(pad_left, pad_right, top_padding)
    return (
        f'<g transform="translate({px(content_x - pad["left"])},'
        f' {px(content_y - pad["top"])})"{attrs}>'
        f"{padded_authoring_content(content, content_width, content_height, pad, line_box)}"
        f"</g>"
    )


def _measured_variable_values(
    board: ResolvedBoard, variables: VariableValues
) -> VariableValues:
    """The values the board is being drawn for, for anything that measures it.

    A control is as wide as the value it displays and a Jinja title is as wide
    as the value it interpolates, so both columns of the title-inline band have
    to be measured against what this request committed. Measuring the compiled
    defaults instead sizes the band for a board nobody asked for, and the two
    columns then overlap by exactly the difference.

    Layered over the defaults rather than replacing them: a request names only
    the variables it changed, and an unmentioned one still renders its default.
    """
    return {**board.variable_defaults, **variables}


def _board_uses_title_inline_band(
    board: ResolvedBoard, variables: VariableValues, layout_content_width: float
) -> bool:
    from dbt_charts.core.render.sizing import should_use_title_inline_band

    return should_use_title_inline_band(
        board.title,
        board.visible_variables,
        layout_content_width,
        board.style,
        float(board.style.frame.card_padding),
        _measured_variable_values(board, variables),
        board.level,
    )


def _paint_title_svg_fill(title_svg: str, color: str) -> str:
    r"""Apply a solid fill to every ``<text>`` element in the title SVG.

    Uses inline ``style="fill: ..."`` rather than the ``fill="..."`` presentation
    attribute. The title text carries a scoped ``class="md-<hash>-heading"``
    (mdsvg), and SVG/CSS specificity rules let class-rule properties beat
    presentation attributes — so a bare ``fill="…"`` override is silently
    swallowed by the class's fill. An inline style attribute trumps the class
    rule and the override sticks.

    Two independent passes (both run unconditionally):

    1. **Inject** ``style="fill: …"`` on every ``<text>`` element that does
       not already carry a style attribute. mdsvg emits plain-heading text
       as ``<text class="md-<hash>-heading">…</text>``; that text gets the
       new inline style here.
    2. **Rewrite** any existing ``fill:`` declaration inside any ``style="…"``
       attribute. mdsvg wraps code-styled or link-styled runs inside a
       title in ``<tspan style="…; fill: …">…</tspan>``; those tspans get
       their existing fill rewritten in place so a mixed-content title
       like ``Sales \`Q3\``` paints uniformly in the override color.

    The rewrite is scoped to the attribute value (not the whole document)
    so CSS rules inside any ``<style>`` block — ``.md-<hash>-text { fill: ... }``
    etc. — are untouched. Other declarations in the same ``style="..."``
    attribute (e.g. ``font-weight: 500``) are preserved.
    """
    color_escaped = html.escape(str(color))

    # Pass 1 — inject inline style on every <text> that doesn't already carry
    # one. Inserting before any attribute means subsequent attributes survive.
    no_style_re = re.compile(r"<text(?![^>]*\bstyle=)")
    title_svg = no_style_re.sub(f'<text style="fill: {color_escaped}"', title_svg)

    # Pass 2 — rewrite any existing fill: declaration inside a style attribute.
    attr_with_fill_re = re.compile(r'(style=")([^"]*\bfill\s*:\s*)[^;"]*([^"]*)(")')
    title_svg = attr_with_fill_re.sub(
        rf"\g<1>\g<2>{color_escaped}\g<3>\g<4>", title_svg
    )

    return title_svg


def _nested_variables_path(source_path: str) -> str:
    """Where a nested board's ``variables:`` mapping sits in the file being edited.

    Empty in, empty out, and that is the whole rule: ``source_path`` already
    means "absolute dotted path, empty when the item has no authored coordinates
    in this file", so an imported board propagates its emptiness and its controls
    stamp nothing. An inline one extends by the single key this band draws.
    """
    return f"{source_path}.variables" if source_path else ""


def _render_variables_band(
    variable_defs: "dict[str, Variable]",
    current_values: VariableValues,
    width: float,
    executor: ChartDataProvider,
    resolved_style: "ResolvedStyle",
    align: StripAlign,
    variables_path: str,
) -> tuple[str, float]:
    """One variables band, drawn.

    There is nothing for a host to pair this with any more: the controls are in
    the band, so a runtime binds the elements it finds rather than mounting a
    parallel layer over anchor boxes and hoping the orders match.

    ``variables_path`` is supplied by whoever composes the band, for the reason
    ``title_authored_attrs`` is: the band cannot know where it sits. It is the
    position ``LayoutItem.source_path`` already carries, extended by the one key
    this band draws — composed here rather than re-derived, so the two cannot
    disagree about which board a control belongs to.
    """
    controls = resolve_controls(
        variable_defs, current_values, executor, resolved_style.variables
    )
    return render_variables_strip_svg(
        controls, width, resolved_style, align, variables_path=variables_path
    )


def _render_title_variables_inline_band(
    board: ResolvedBoard,
    variables: VariableValues,
    layout_content_width: float,
    card_pad: float,
    executor: ChartDataProvider,
    text_align: Literal["left", "center", "right"],
    title_authored_attrs: str,
    variables_path: str,
) -> tuple[str, float]:
    """One horizontal band: board title (left column) + variable controls (right).

    Both handles are supplied by whoever composes the band, because the band
    cannot know the path it sits at — and they are not the same answer, which is
    why they are two arguments. The root board's title is the document-root
    ``title`` key, while a nested board's band is covered by that board's own
    header handle and must not offer a second one inside it; its *variables*, by
    contrast, are keys of this file whenever the board is inline, and want the
    handle the header cannot give them.
    """
    from dbt_charts.core.render.sizing import (
        compute_title_variables_inline_baseline_layout,
        get_title_height,
        resolve_title_variables_inline_widths,
        title_baseline_offset,
        title_line_box,
    )

    vs = board.style.variables
    inner = max(layout_content_width - 2 * card_pad, 1.0)
    # One merge for the whole band: the column split, the title's height and
    # the controls all describe the same board, so they measure the same values.
    measured_values = _measured_variable_values(board, variables)
    title_w, vars_w = resolve_title_variables_inline_widths(
        inner,
        board.style,
        board.visible_variables,
        board.title,
        measured_values,
        board.level,
    )
    col_gap = float(vs.gap)

    # Pass the board inner width for text-wrap purposes only — board titles
    # are semantic (level-only, see board_title_spec) so width does not change
    # the rendered size. ``title_w`` is what the title CAN occupy; the wrap
    # happens inside that column at the layout step.
    title_svg_raw = _render_title_svg(
        board.title,
        variables,
        inner,
        board.style,
        text_align=text_align,
        level=board.level,
    )
    # Paint the title fill from cascade-resolved board style so user overrides
    # win over mdsvg's CSS-baked class fill.
    # style.title.font.color inherits font.color via the Inherit graph and is
    # always non-None for any shipped theme — no fallback needed.
    title_color = board.style.title.font.color
    if title_color:
        title_svg_raw = _paint_title_svg_fill(title_svg_raw, str(title_color))
    title_inner = extract_svg_inner_content(title_svg_raw)
    # Measure at inner (the same width _render_title_svg draws at) so the
    # reserved height matches the drawn height when a case transform widens
    # the title past the wrap threshold at the narrower title_w column.
    measured_title_h = get_title_height(
        board.title,
        inner,
        measured_values,
        level=board.level,
        resolved_style=board.style,
    )
    title_h = max(measured_title_h, float(board.style.title.min_height))
    inline_title_line_box = title_line_box(measured_title_h, board.level, board.style)

    # The compact band packs its controls against the far edge, opposite the title.
    # Its variables are addressable exactly when its title is: both are keys of
    # the same file, and the composer told us which file that is.
    vars_svg, vars_h = _render_variables_band(
        board.visible_variables,
        variables,
        vars_w,
        executor,
        board.style,
        "end",
        variables_path=variables_path,
    )
    assert vs.font.size is not None, "style.variables.font.size must be configured"
    assert vs.font.family is not None, "style.variables.font.family must be configured"
    title_dy, vars_dy, band_h = compute_title_variables_inline_baseline_layout(
        title_h,
        vars_h,
        title_baseline_offset(board.style, board.level),
        float(vs.font.size),
        vs.font.family,
        float(vs.title_inline_band_bottom_pad),
    )
    if title_authored_attrs:
        title_group = _tagged_authoring_block(
            title_inner,
            title_authored_attrs,
            card_pad,
            title_dy,
            title_w,
            title_h,
            card_pad,
            # The band's right neighbor is the variables column, not the card
            # edge — claiming card_pad on the right would reach past col_gap
            # into the column's own space. min() also covers the (unusual)
            # case where col_gap exceeds card_pad, where card_pad is still the
            # honest claim for this side.
            min(card_pad, col_gap),
            # The band claims the top inset once, as a whole, where it is placed;
            # inside it this title's y is baseline alignment, not a card inset.
            0.0,
            line_box=inline_title_line_box,
        )
    else:
        # No path here: the band is inside a board's combined header handle, which
        # already carries it. The kind still belongs on this sub-group rather than
        # on the band, because the band also holds the variables strip — the
        # layout only builds one when there are visible variables to place.
        title_group = (
            f'<g transform="translate({px(card_pad)}, {px(title_dy)})"'
            f"{authored_kind_attr('title')}>"
            f"{title_inner}</g>"
        )
    vars_group = translate_group(
        px(card_pad + title_w + col_gap), px(vars_dy), vars_svg
    )
    band = f"{title_group}{vars_group}"
    return band, band_h


def _render_text_svg(
    text: str,
    variables: VariableValues,
    width: float,
    resolved_style: "ResolvedStyle",
    text_style: "TextStyle",
    allow_raw_html: bool = False,
) -> tuple[str, float]:
    """Render markdown text with Jinja resolution and board-link rewriting.

    Returns (svg_string, height).
    """
    from dbt_charts.core.render.board_links import get_link_context, rewrite_board_links

    resolved = _resolve_markdown_jinja(text, variables)
    resolved = rewrite_board_links(resolved, get_link_context())
    return render_prose_svg(resolved, width, text_style, resolved_style, allow_raw_html)


def _build_board_content_items(
    x_offset: float,
    y_offset: float,
    gap: float,
    title_svg: str,
    title_height: float,
    text_svg: str,
    text_height: float,
    variables_svg: str,
    variables_height: float,
    layout_content: str,
    layout_content_height: float,
    card_padding: float,
    content_width: float,
    inline_header_svg: str = "",
    inline_header_height: float = 0.0,
    title_authored_attrs: str = "",
    text_authored_attrs: str = "",
    header_authored_attrs: str = "",
    title_line_box: tuple[float, float] | None = None,
) -> tuple[list[str], float]:
    """Build inner content item list; return (items, items_height).

    Delegates gap arithmetic to compute_board_content_box (the single owner).
    card_padding: title, text, and variables are inset on x; layout keeps base x_offset.
    content_width: the authored block width title and text were rendered at —
    the width they declare, not the narrower measure markdown wraps its prose
    to. The hit target for each spans that block, not the ink inside it.

    header_authored_attrs makes the title and text one authored unit: they are keys
    of the same board (``title:`` and ``text:`` on a col or row), the way a chart's
    title is a key inside the chart, so one handle over both is what matches how
    they were written. It is the *header* rather than the whole board on purpose —
    a board holding nested charts would otherwise have to offer its entire subtree
    as the target. Rendering makes that safe: the title (or its inline band) and
    text are always a contiguous band above variables and any nested layout.

    Passing it supersedes title_authored_attrs/text_authored_attrs, which remain for
    the root board — its keys sit at the document root, which has no path of its
    own to hand a combined handle.
    """
    from dbt_charts.core.render.sizing import compute_board_content_box

    box = compute_board_content_box(
        title_height=title_height,
        text_height=text_height,
        variables_height=variables_height,
        inline_band_height=inline_header_height,
        gap=gap,
        has_layout_items=bool(layout_content),
        card_padding=card_padding,
    )

    items: list[str] = []
    content_x = (
        x_offset + card_padding
    )  # title, text, variables align with chart content
    # ...and on y for the same reason: box.content_top is the inset a chart's
    # ink already carries, so the first prose ink starts level with it.
    y = y_offset + box.content_top

    # One handle over the header band when the board owns a path (see docstring);
    # otherwise a handle per key, which is all the root board can address. Pieces
    # are collected first so the combined band can carry one selection boundary,
    # and are positioned relative to it — the rects have to stay direct children of
    # group holding the path for a host to resolve the two together.
    header: list[str] = []
    header_start = y
    header_end = y

    def header_piece(
        svg: str,
        at_x: float,
        at_y: float,
        own_attrs: str,
        own_h: float,
        kind: str,
        top_inset: float,
    ) -> str:
        if header_authored_attrs:
            # Offset from the *rounded* wrapper origin, not the unrounded one:
            # px() each separately and the two roundings can disagree, landing the
            # composed position a pixel off where it rendered before the wrapper.
            # The leaf carries a bare kind (no path — the block already has it),
            # which key of the combined header handle a double-click landed on.
            return translate_group(
                px(at_x) - px(content_x),
                px(at_y) - px(header_start),
                svg,
                authored_kind_attr(kind) if kind else "",
            )
        if not own_attrs:
            return translate_group(px(at_x), px(at_y), svg)
        return _tagged_authoring_block(
            svg,
            own_attrs,
            at_x,
            at_y,
            content_width,
            own_h,
            card_padding,
            card_padding,
            top_inset,
            line_box=title_line_box if kind == "title" else None,
        )

    if inline_header_svg:
        header.append(
            header_piece(
                inline_header_svg,
                x_offset,
                y,
                "",
                inline_header_height,
                "",
                box.content_top,
            )
        )
        y += inline_header_height
        header_end = y
        if text_svg:
            y += gap  # inline_band → text: normal gap
    elif title_svg:
        header.append(
            header_piece(
                title_svg,
                content_x,
                y,
                title_authored_attrs,
                title_height,
                "title",
                box.content_top,
            )
        )
        y += title_height
        header_end = y
        # title → text: 0 gap (heading_margin_bottom_px IS the after-heading rhythm)

    if text_svg:
        md = extract_svg_inner_content(text_svg)
        if md:
            header.append(
                # The band's top inset is claimed once, by whichever block opens
                # it — the title if there is one, this text if there is not.
                header_piece(
                    md,
                    content_x,
                    y,
                    text_authored_attrs,
                    text_height,
                    "text",
                    0.0 if header else box.content_top,
                )
            )
        y += text_height
        header_end = y
        if variables_svg and not inline_header_svg:
            y += gap  # text → variables: normal gap

    if header_authored_attrs and header:
        # A combined header opening with a title inherits that title's dead top
        # margin, so its handle is seated the way the standalone title is. It
        # still ends where the header ends: the heading's trailing margin is only
        # slack when the title is the whole header, and is otherwise the rhythm
        # between the heading and the text under it.
        header_box: tuple[float, float] | None = None
        if title_line_box and title_svg:
            line_top, line_height = title_line_box
            header_box = (
                line_top,
                line_height if not text_svg else header_end - header_start - line_top,
            )
        items.append(
            _tagged_authoring_block(
                "".join(header),
                header_authored_attrs,
                content_x,
                header_start,
                content_width,
                header_end - header_start,
                card_padding,
                card_padding,
                box.content_top,
                line_box=header_box,
            )
        )
    else:
        items.extend(header)

    if variables_svg and not inline_header_svg:
        if not text_svg and title_svg:
            y += gap  # title/band → variables (no text between): normal gap
        items.append(translate_group(px(content_x), px(y), variables_svg))
        y += variables_height

    # Layout block position is authoritative from the box, not from y accumulation.
    y_layout = y_offset + box.non_layout_height + box.gap_before_layout
    if layout_content:
        items.append(translate_group(px(x_offset), px(y_layout), layout_content))

    # Total height = non-layout stack + gap-before-layout + layout slot.
    # For nested boards: sizing computed layout_content_height to fill the slot exactly.
    # For the root board: if non-layout content overflows (available goes negative),
    # clamp to 0 — the SVG grows via max(height, ...) in total_height.
    total_height = (
        box.non_layout_height + box.gap_before_layout + max(layout_content_height, 0)
    )
    return items, total_height


def _render_layout(
    board: ResolvedBoard,
    executor: ChartDataProvider,
    variables: VariableValues,
    layout_content_width: float,
    layout_content_height: float,
    card_gap: float,
    gap: float,
    background: str | None,
    *,
    render_cache: RenderCache,
    error_collector: list[Diagnostic] | None = None,
) -> tuple[str, float]:
    """Render layout based on type (single dispatch point).

    This is the single source of truth for layout type dispatch.

    Returns:
        (svg_elements_string, actual_layout_height)
    """
    from dbt_charts.core.render.layouts import (
        render_cols_layout,
        render_grid_layout,
        render_rows_layout,
        render_tabs_layout,
    )

    if not board.layout.items:
        return "", 0.0

    layout = board.layout
    items = layout.items
    resolved_style = board.style  # ResolvedBoard.style is the ResolvedStyle

    if layout.type == "cols":
        result = render_cols_layout(
            items,
            executor,
            variables,
            layout_content_width,
            layout_content_height,
            card_gap,
            gap,
            background,
            resolved_style=resolved_style,
            render_cache=render_cache,
            error_collector=error_collector,
        )
    elif layout.type == "grid":
        result = render_grid_layout(
            items,
            executor,
            variables,
            layout_content_width,
            layout_content_height,
            card_gap,
            gap,
            background,
            resolved_style=resolved_style,
            render_cache=render_cache,
            error_collector=error_collector,
        )
    elif layout.type == "tabs":
        result = render_tabs_layout(
            items,
            executor,
            variables,
            layout_content_width,
            layout_content_height,
            list(layout.tab_titles),
            list(layout.tab_slugs),
            layout.tab_variable,
            layout.default_tab or 0,
            layout.tab_position or "top",
            background,
            resolved_style=resolved_style,
            render_cache=render_cache,
            error_collector=error_collector,
        )
    else:
        # Default to rows (handles "rows" and any unknown type)
        result = render_rows_layout(
            items,
            executor,
            variables,
            layout_content_width,
            layout_content_height,
            card_gap,
            gap,
            background,
            resolved_style=resolved_style,
            render_cache=render_cache,
            error_collector=error_collector,
        )

    return result


_STYLE_BLOCK_RE = re.compile(r"(\n?)  <style>\n(.*?)\n  </style>", re.DOTALL)
_SVG_TAG_RE = re.compile(r"<svg\b|</svg>")
# mdsvg's own class-scoping (SVGRenderer._scoped_class): every `.md-*` rule any
# renderer (callout, prose, table) emits is named `md-<hash>-<selector>`, the
# hash a pure function of that renderer's own style. Two renderers with
# byte-identical style always produce byte-identical class names and rule
# text -- that identity is what makes a duplicate *line* safe to drop here.
_DEDUPE_ELIGIBLE_RULE_RE = re.compile(r"^\s*\.md-[0-9a-f]+-[a-z]+\s*\{")


def _enclosing_svg_key(svg: str, pos: int) -> int:
    """A stable id for the ``<svg>`` element that most tightly encloses `pos`.

    The offset of that element's own opening tag -- two positions get the same
    key only when they sit inside the exact same ``<svg>`` element, never
    merely a shared ancestor. `_dedupe_repeated_style_rules` below keeps the
    first occurrence of a byte-identical rule per this scope and only ever
    drops a *later* one, so a rule any chart still needs survives dedup
    somewhere in the document. A per-chart "download as SVG/PNG/PDF" feature
    (`apps/cloud/apps/renders/chart_slice.py`'s `extract_chart_svg`) relies on
    exactly that: it copies every `<style>` block in the document along with
    one chart's sliced-out `<g data-chart-id>` group, so the surviving
    occurrence travels with the slice regardless of which chart's own block
    it happened to survive in.
    """
    stack: list[int] = []
    for m in _SVG_TAG_RE.finditer(svg, 0, pos):
        if m.group(0) == "<svg":
            stack.append(m.start())
        elif stack:
            stack.pop()
    return stack[-1] if stack else -1


def _dedupe_repeated_style_rules(svg: str) -> str:
    """Collapse byte-identical CSS rule lines repeated across ``<style>`` blocks
    that sit inside the same ``<svg>`` element, in a fully assembled board SVG.

    Applied only once every chart has already rendered its own complete,
    self-sufficient CSS, so this can only ever remove a line already provably
    present elsewhere in the same scope — see ``_enclosing_svg_key`` for why
    that scope is "the same enclosing ``<svg>``", not "the whole document".
    """
    seen: set[tuple[int, str]] = set()

    def dedupe_block(match: re.Match[str]) -> str:
        leading_newline, body = match.group(1), match.group(2)
        scope = _enclosing_svg_key(svg, match.start())
        kept = []
        for line in body.split("\n"):
            if _DEDUPE_ELIGIBLE_RULE_RE.match(line):
                key = (scope, line)
                if key in seen:
                    continue
                seen.add(key)
            kept.append(line)
        if not kept:
            # Consumes the leading newline too, so a fully-emptied block
            # leaves no blank line behind at its old position.
            return ""
        return f"{leading_newline}  <style>\n" + "\n".join(kept) + "\n  </style>"

    return _STYLE_BLOCK_RE.sub(dedupe_block, svg)


def render_board_svg(
    board: ResolvedBoard,
    executor: ChartDataProvider,
    variables: VariableValues,
    background: str | None,
    grid: bool = False,
    *,
    render_cache: RenderCache,
    margins: bool = False,
    error_collector: list[Diagnostic] | None = None,
    embed_fonts: bool = False,
) -> str:
    """Render board to SVG.

    Walks the layout structure and renders each item based on layout type.

    Args:
        board: ResolvedBoard with board config already baked in — no config lookup here.
        executor: ChartDataProvider for query execution
        variables: Variable values for queries
        background: Background color or pattern URL
        grid: Whether to show grid overlay pattern
        margins: Whether to show vertical margin guide lines
        embed_fonts: Carry the font bytes inline as ``data:`` URIs instead of
            naming ``/static/fonts/…``. For an artifact with no host to fetch
            from — a standalone HTML export opened from disk. Requires an open
            ``collect_painted_italic_families()`` sink to select italics.

    Returns:
        Complete SVG string for the board
    """
    from dbt_charts.core.render.font_selection import (
        board_font_face_css,
        italic_sink_is_open,
        painted_italic_families,
    )
    from dbt_charts.core.render.sizing import get_title_height, title_line_box
    from dbt_charts.core.render.svg_utils import (
        create_grid_pattern,
        generate_svg_styles,
    )

    # Stated as an invariant alongside the cascade ones below, because an empty
    # italic set reads exactly like "no italics were painted": a caller that forgot
    # the sink would get a plausible export with every italic row missing and no
    # error, its prose measured against the real italic and painted with a
    # synthesized oblique.
    assert not embed_fonts or italic_sink_is_open(), (
        "embed_fonts=True requires an open collect_painted_italic_families() sink; "
        "without one the italic boards a board paints cannot be known"
    )

    resolved_style = board.style  # ResolvedBoard.style is the ResolvedStyle
    from dbt_charts.core.compile.template.jinja import resolve_jinja_template

    page_title = (
        resolve_jinja_template(board.title, variables, strict=False)
        if board.title
        else "dbt Charts"
    )
    font_family = resolved_style.font.family
    assert font_family is not None, "cascade should populate style.font.family"
    board_background = resolved_style.background
    # Board config is baked into ResolvedBoard, so no config lookup here. These are
    # None only on nested boards, which render through render_nested_board; on the
    # root path build_resolved_board sets all three unconditionally. Falling back
    # to 0.0 here would silently reproduce the edge-flush bug this path exists to
    # prevent, so assert the invariant instead.
    assert board.page_padding is not None, "root board must carry page_padding"
    assert board.card_padding is not None, "root board must carry card_padding"
    assert board.card_gap is not None, "root board must carry card_gap"
    page_padding = board.page_padding
    card_pad = board.card_padding
    card_gap = board.card_gap
    # gap between layout items: ResolvedBoard.layout.gap (set during resolve_board).
    gap = board.layout.gap or 0.0
    effective_gap = gap + card_gap

    width = board.width
    height = board.height

    # Root content width should match the sizing pass exactly.
    layout_content_width = board.layout.content_width
    # Title and text render within a narrower width (inset by card_padding on both sides).
    title_text_width = max((layout_content_width or 0.0) - 2 * card_pad, 0.0)
    # The width title and text wrap to. Bound once: their selection boxes describe
    # this block, so a site that drifts from it mis-sizes the hover target.
    prose_width = title_text_width or layout_content_width
    # Width is container-driven and precomputed during sizing; height remains
    # content-driven and is finalized here after title/content/controls render.
    layout_content_height = height - (2 * page_padding)
    # The root board renders title/content/variable heights dynamically here
    # (rather than reading pre-computed values from sizing) because the root board
    # is the source of truth for its own height — it has no pre-allocated slot.
    # Nested boards follow the opposite contract (see render_nested_board).
    # Gap arithmetic is owned by compute_board_content_box (single owner).

    text_align = resolved_style.text.align

    inline_header_svg = ""
    inline_header_height = 0.0
    title_svg = ""
    title_height = 0.0
    root_title_line_box: tuple[float, float] | None = None

    if _board_uses_title_inline_band(board, variables, layout_content_width):
        inline_header_svg, inline_header_height = _render_title_variables_inline_band(
            board,
            variables,
            layout_content_width,
            card_pad,
            executor,
            text_align=text_align,
            title_authored_attrs=authored_attrs("title", "title"),
            variables_path="variables",
        )
    elif board.title:
        title_svg = _render_title_svg(
            board.title,
            variables,
            prose_width,
            resolved_style,
            text_align=text_align,
            level=board.level,
        )
        title_color = resolved_style.title.font.color
        if title_color:
            title_svg = _paint_title_svg_fill(title_svg, str(title_color))
        measured_title_height = get_title_height(
            board.title,
            prose_width,
            _measured_variable_values(board, variables),
            level=board.level,
            resolved_style=resolved_style,
        )
        title_height = max(measured_title_height, float(board.style.title.min_height))
        root_title_line_box = title_line_box(
            measured_title_height, board.level, resolved_style
        )

    # Render text (markdown) if present (using shared helper)
    text_svg = ""
    text_height = 0.0
    if board.text:
        text_svg, text_height = _render_text_svg(
            board.text,
            variables,
            prose_width,
            resolved_style=resolved_style,
            text_style=resolved_style.text,
            allow_raw_html=board.html_policy == "trusted-raw",
        )

    # Render variable controls for root-level variables only (skip when merged into title band).
    variables_svg = ""
    variables_height = 0.0
    # Variable controls are inset by card_pad on the left (aligned with chart
    # content); reduce width by the same amount so the right edge is unchanged.
    variables_width = max(layout_content_width - card_pad, 0.0)
    if board.visible_variables and variables and not inline_header_svg:
        variables_svg, variables_height = _render_variables_band(
            board.visible_variables,
            variables,
            variables_width,
            executor,
            resolved_style,
            "start",
            variables_path="variables",
        )

    # Deduct non-layout content height from the available layout slot.
    # compute_board_content_box is the single owner of the gap arithmetic:
    # title→text uses 0 gap (heading_margin_bottom_px IS the rhythm);
    # all other adjacent pairs use effective_gap.
    from dbt_charts.core.render.sizing import compute_board_content_box

    content_box = compute_board_content_box(
        title_height=title_height,
        text_height=text_height,
        variables_height=variables_height,
        inline_band_height=inline_header_height,
        gap=effective_gap,
        has_layout_items=bool(board.layout.items),
        card_padding=card_pad,
    )
    layout_content_height -= (
        content_box.non_layout_height + content_box.gap_before_layout
    )

    # The chart hover runtime ships with the host, like the variable controls
    # (render/controls.py): a board is a picture, and code never ships inside
    # it. What it needs from the theme rides on the board root as data.
    hover_attributes = hover_runtime_attributes(resolved_style=resolved_style)

    # Render layout (using shared helper). Scoped in board_variables() so any
    # chart reachable from here (table pagination today) can read the current
    # page off current_board_variables() without a variables= parameter
    # threaded down through every render_resolved_chart call site -- this is
    # the one seam both the live render() pass and dct artifact render's
    # replay path (board_replay.py) share, so opening it here covers both
    # rather than at each caller.
    with board_variables(variables):
        layout_content, actual_layout_height = _render_layout(
            board,
            executor,
            variables,
            layout_content_width,
            layout_content_height,
            card_gap,
            gap,
            resolved_style.background,
            render_cache=render_cache,
            error_collector=error_collector,
        )

    # Combine title, content, variables, and layout into positioned SVG groups.
    # Use actual_layout_height (from rendered content) rather than the pre-computed
    # layout_content_height so the root board SVG grows to fit Vega charts.
    content_items, content_items_height = _build_board_content_items(
        x_offset=page_padding,
        y_offset=page_padding,
        gap=effective_gap,
        title_svg=title_svg,
        title_height=title_height,
        text_svg=text_svg,
        text_height=text_height,
        variables_svg=variables_svg,
        variables_height=variables_height,
        layout_content=layout_content,
        layout_content_height=actual_layout_height,
        card_padding=card_pad,
        content_width=prose_width,
        inline_header_svg=inline_header_svg,
        inline_header_height=inline_header_height,
        title_authored_attrs=authored_attrs("title", "title") if title_svg else "",
        text_authored_attrs=authored_attrs("text", "text") if text_svg else "",
        title_line_box=root_title_line_box,
    )

    # Calculate final dimensions. The two axes are not symmetric:
    # width is already padding-inclusive (sizing sets layout.width = container
    # width and derives content width as container - 2 * frame.margin), so the
    # first arm normally wins and the second only guards the degenerate
    # frame.width < 2 * frame.margin case, where content width clamps to 0.
    # height is content-only (layout.height is layout + title, no page padding),
    # so the second arm is what adds vertical padding at all and normally wins.
    total_width = max(width, layout_content_width + (2 * page_padding))
    total_height = max(height, content_items_height + (2 * page_padding))

    # Reserve vertical space for the page footer (right-aligned text, optional
    # hairline rule above) so it doesn't overlap the bottom card.
    # All footer values now live in board.style.footer (style cascade). Footer and
    # timestamp are root-board chrome — only this root render path reads them;
    # a nested board authoring style.footer/timestamp has no effect.
    footer_style = board.style.footer
    if footer_style.visible:
        # FooterStyle._require_font_size_and_color guarantees font.size is not None.
        assert footer_style.font.size is not None
        footer_font_size = float(footer_style.font.size)
        footer_rule_gap_px = get_chart_rendering().frame.footer_rule_gap_px
        rule_gap = footer_rule_gap_px if footer_style.rule is not None else 0
        total_height += footer_style.y_offset + footer_font_size + rule_gap

    # Grid pattern if enabled
    grid_defs = ""
    if grid:
        grid_defs = create_grid_pattern()
        background = "url(#grid-pattern)"

    # Background rect
    bg_rect = ""
    if background:
        bg_rect = f'<rect x="0" y="0" width="{total_width}" height="{total_height}" fill="{html.escape(background)}"/>'

    # Margin guide lines (print-media style alignment guides)
    margin_lines = ""
    if margins:
        margin_color = "rgba(219, 112, 147, 0.45)"
        inset = 16
        left = page_padding
        right = total_width - page_padding
        lines = [
            f'<line x1="{format_svg_numeric(left)}" y1="0" x2="{format_svg_numeric(left)}" y2="{total_height}" stroke="{margin_color}" stroke-width="1"/>',
            f'<line x1="{format_svg_numeric(left + inset)}" y1="0" x2="{format_svg_numeric(left + inset)}" y2="{total_height}" stroke="{margin_color}" stroke-width="1"/>',
            f'<line x1="{format_svg_numeric(right)}" y1="0" x2="{format_svg_numeric(right)}" y2="{total_height}" stroke="{margin_color}" stroke-width="1"/>',
            f'<line x1="{format_svg_numeric(right - inset)}" y1="0" x2="{format_svg_numeric(right - inset)}" y2="{total_height}" stroke="{margin_color}" stroke-width="1"/>',
        ]
        margin_lines = "\n".join(lines)

    # Fonts last, and deliberately: which boards this board needs is a fact about
    # what it just painted. The markup below is what that is read from — content
    # items, the interactivity runtime (whose tooltip family is applied at hover
    # time), and `font_family`, which covers both the family the HTML host applies
    # to the page around the board and the footer and timestamp, since those two
    # render after this point and read that same string. Only assembled in embed
    # mode: URL mode ignores it, and every `dct serve` request would otherwise pay
    # for a second copy of the whole board.
    painted_markup = "".join(content_items) + str(font_family) if embed_fonts else ""
    svg_styles = generate_svg_styles(
        emoji_mode=resolved_style.emoji_mode,
        font_face_css=board_font_face_css(
            painted_markup,
            resolved_style.emoji_mode,
            painted_italic_families(),
            embed=embed_fonts,
        ),
    )

    render_time_utc = datetime.now(timezone.utc)
    render_timestamp_iso = render_time_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    footer_x = total_width - page_padding
    footer_y = total_height - footer_style.y_offset

    timestamp_element = ""
    # Painted ahead of the timestamp block, which positions itself against this
    # width. Measuring it a second time there would use the regular-weight
    # advances and quietly deliver a narrower gap than footer_timestamp_gap_px
    # configures, because the brand run is painted heavier than the rest.
    attribution_svg = ""
    attribution_width = 0.0
    if footer_style.visible:
        attribution_svg, attribution_width = _footer_attribution_svg(
            footer_style, str(board.style.font.family), footer_x, footer_y
        )

    timestamp_style = board.style.timestamp
    if timestamp_style.visible:
        # All timestamp values come from the style cascade.
        # TimestampStyle._require_font_size_and_color guarantees both non-None.
        assert timestamp_style.font.size is not None
        assert timestamp_style.font.color is not None
        timestamp_font_size = float(timestamp_style.font.size)
        # Stamp data freshness, not render time, always in UTC: a static export
        # can't know the viewer's zone, and the render process's own zone is an
        # accidental deployment artifact. min() shows the stalest cached query so
        # the board never reads fresher than it is.
        cache_hit_ats = executor.cache_hit_ats
        data_as_of = min(cache_hit_ats) if cache_hit_ats else render_time_utc
        display_timestamp = portable_strftime(
            data_as_of.astimezone(timezone.utc), timestamp_style.format
        )
        if timestamp_style.position == "top":
            timestamp_y = timestamp_style.y
        else:
            timestamp_y = footer_y
        if timestamp_style.align == "left":
            timestamp_x = page_padding
            timestamp_anchor = "start"
        else:
            timestamp_x = total_width - page_padding
            timestamp_anchor = "end"
            if timestamp_style.position == "footer" and footer_style.visible:
                timestamp_x = (
                    footer_x
                    - attribution_width
                    - get_chart_rendering().frame.footer_timestamp_gap_px
                )
        timestamp_element = (
            f'<text data-role="render-timestamp" x="{format_svg_numeric(timestamp_x)}" y="{format_svg_numeric(timestamp_y)}" text-anchor="{timestamp_anchor}" '
            f'font-size="{format_svg_numeric(timestamp_font_size)}" fill="{timestamp_style.font.color}" font-family="{board.style.font.family}" '
            f'style="font-variant-numeric: tabular-nums lining-nums;">'
            f"{html.escape(display_timestamp)}</text>"
        )

    footer_element = ""
    # All footer values come from board.style.footer (style cascade).
    if footer_style.visible:
        # FooterStyle._require_font_size_and_color guarantees both non-None.
        assert footer_style.font.size is not None
        assert footer_style.font.color is not None
        footer_parts = []
        if footer_style.rule is not None:
            rule_y = (
                footer_y
                - float(footer_style.font.size)
                - get_chart_rendering().frame.footer_rule_gap_px
            )
            footer_parts.append(
                f'<line x1="{format_svg_numeric(page_padding)}" y1="{format_svg_numeric(rule_y)}" '
                f'x2="{format_svg_numeric(footer_x)}" y2="{format_svg_numeric(rule_y)}" '
                f'stroke="{footer_style.rule.color}" stroke-width="{format_svg_numeric(footer_style.rule.stroke_width)}"/>'
            )
        footer_parts.append(attribution_svg)
        footer_element = "\n".join(footer_parts)

    # data-dbt-page-background keeps its pre-deletion name even though its
    # value now comes from style.background, not the deleted style.page: it
    # is a pure transport attribute to_html() reads back, never seen by a
    # user, and already-persisted Cloud renders carry this exact name --
    # renaming it would 500 on every one of them for no benefit.
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_width} {total_height}" width="{format_svg_numeric(total_width)}" height="{format_svg_numeric(total_height)}" preserveAspectRatio="xMinYMin meet" style="display: block;" data-rendered-at="{render_timestamp_iso}" data-dbt-page-title="{html.escape(page_title, quote=True)}"{hover_attributes} data-dbt-page-background="{html.escape(str(board_background), quote=True)}" aria-label="{html.escape(page_title, quote=True)}">

<defs>
{grid_defs}
{svg_styles}
</defs>
{bg_rect}
{"".join(content_items)}
{margin_lines}
{timestamp_element}
{footer_element}
</svg>"""
    return _dedupe_repeated_style_rules(svg)


def render_nested_board(
    board: ResolvedBoard,
    executor: ChartDataProvider,
    variables: VariableValues,
    available_width: float,
    available_height: float,
    card_gap: float,
    *,
    source_path: str = "",
    render_cache: RenderCache,
    error_collector: list[Diagnostic] | None = None,
) -> tuple[str, float]:
    """Render a nested board.

    Args:
        board: ResolvedBoard for the nested board (board config fields are None).
        executor: ChartDataProvider for query execution
        variables: Variable values for queries
        available_width: Width in pixels for the board
        card_gap: Gap between cards (inter-item spacing)
        available_height: Pre-allocated slot height from parent layout (e.g. cols max
                          height). When provided, the board renders at least this tall so
                          all siblings in a cols row share the same height.

    Returns:
        (svg_string, actual_height) — actual_height is derived from rendered content.
    """

    resolved_style = board.style  # ResolvedBoard.style is the ResolvedStyle

    gap = resolved_style.gap if resolved_style.gap is not None else 0.0
    ep = _effective_padding(resolved_style)

    # Trust the normalizer - content dimensions are always set by sizing.py
    layout_content_width = board.layout.content_width
    layout_content_height = board.layout.content_height

    card_pad = float(resolved_style.frame.card_padding)
    title_text_width = max(layout_content_width - 2 * card_pad, 0.0)
    prose_width = title_text_width or layout_content_width

    board_width = available_width - (
        resolved_style.margin.horizontal if resolved_style.margin else 0.0
    )
    text_align = resolved_style.text.align

    inline_header_svg = ""
    inline_header_height = 0.0
    title_svg = ""
    title_height = 0.0
    nested_title_line_box: tuple[float, float] | None = None
    if _board_uses_title_inline_band(board, variables, layout_content_width):
        inline_header_svg, inline_header_height = _render_title_variables_inline_band(
            board,
            variables,
            layout_content_width,
            card_pad,
            executor,
            text_align=text_align,
            # The board's own header handle wraps this band and covers its heading.
            # An imported board has no handle at all, so the band's kind-only leaf
            # is the one tag inside that subtree — inert, because a host resolves
            # selection from `data-authored-path` and there is none to find.
            title_authored_attrs="",
            variables_path=_nested_variables_path(source_path),
        )
    elif board.title:
        from dbt_charts.core.render.sizing import get_title_height, title_line_box

        title_svg = _render_title_svg(
            board.title,
            variables,
            prose_width,
            resolved_style,
            text_align=text_align,
            level=board.level,
        )
        measured_title_height = get_title_height(
            board.title,
            prose_width,
            _measured_variable_values(board, variables),
            level=board.level,
            resolved_style=resolved_style,
        )
        title_height = max(measured_title_height, float(board.style.title.min_height))
        nested_title_line_box = title_line_box(
            measured_title_height, board.level, resolved_style
        )

    # Render text (markdown) if present (using shared helper)
    text_svg = ""
    text_height = 0.0
    if board.text:
        text_svg, text_height = _render_text_svg(
            board.text,
            variables,
            prose_width,
            resolved_style=resolved_style,
            text_style=resolved_style.text,
            allow_raw_html=board.html_policy == "trusted-raw",
        )

    # Read-only strip for this nested board's local variables, plus its control
    # layer for hosts that mount one.
    nested_variables_svg = ""
    nested_variables_height = 0.0
    # Variable controls are inset by card_pad on the left (aligned with chart
    # content); reduce width by the same amount so the right edge is unchanged.
    nested_variables_width = max(layout_content_width - card_pad, 0.0)
    if board.visible_variables and variables and not inline_header_svg:
        nested_variables_svg, nested_variables_height = _render_variables_band(
            board.visible_variables,
            variables,
            nested_variables_width,
            executor,
            resolved_style,
            "start",
            variables_path=_nested_variables_path(source_path),
        )

    # _calculate_nested_board_layout deducted variable controls height from
    # board.layout.content_height. Pass that as the layout slot hint. The actual
    # rendered height is read back from the layout renderer below.
    authored_bg = resolved_style.background
    layout_content, actual_layout_height = _render_layout(
        board,
        executor,
        variables,
        layout_content_width,
        layout_content_height,
        card_gap,
        gap,
        authored_bg,
        render_cache=render_cache,
        error_collector=error_collector,
    )

    # Apply board-level title color overrides before building items. Painting
    # happens AFTER mdsvg renders so the override wins over mdsvg's CSS-baked
    # class fill regardless of upstream cascade quirks in get_compact_style.
    title_color = resolved_style.title.font.color
    if title_color and title_svg:
        title_svg = _paint_title_svg_fill(title_svg, str(title_color))

    # Combine title, text, variables, and layout into positioned groups.
    # Use actual_layout_height (from rendered content) so Vega charts that render
    # taller than their pre-computed slot expand the board rather than being clipped.
    inner_items, inner_items_height = _build_board_content_items(
        x_offset=ep.left,
        y_offset=ep.top,
        gap=gap,
        title_svg=title_svg,
        title_height=title_height,
        title_line_box=nested_title_line_box,
        text_svg=text_svg,
        text_height=text_height,
        variables_svg=nested_variables_svg,
        variables_height=nested_variables_height,
        layout_content=layout_content,
        layout_content_height=actual_layout_height,
        card_padding=card_pad,
        content_width=prose_width,
        inline_header_svg=inline_header_svg,
        inline_header_height=inline_header_height,
        header_authored_attrs=(
            authored_attrs(source_path, "header")
            if source_path and (title_svg or inline_header_svg or text_svg)
            else ""
        ),
    )

    # Compute board dimensions from actual rendered content, floored at the pre-allocated
    # slot height from the parent layout (e.g. cols max height) so all siblings share
    # the same height.  Content overflow still expands beyond the slot.
    natural_board_height = ep.top + inner_items_height + ep.bottom
    board_margin_vertical = (
        resolved_style.margin.vertical if resolved_style.margin else 0.0
    )
    board_margin_left = resolved_style.margin.left if resolved_style.margin else 0.0
    board_margin_top = resolved_style.margin.top if resolved_style.margin else 0.0
    allocated_board_height = max(available_height - board_margin_vertical, 0.0)
    board_height = max(natural_board_height, allocated_board_height)
    total_svg_width = available_width
    total_svg_height = board_height + board_margin_vertical

    bg_rect = ""
    border_rect = ""
    border_radius = resolved_style.border.radius

    if authored_bg:
        bg = html.escape(str(authored_bg))
        bg_rect = f'<rect x="0" y="0" width="{board_width}" height="{board_height}" fill="{bg}" rx="{border_radius}"/>'

    border_width = resolved_style.border.width
    border_color = resolved_style.border.color
    if border_width > 0 and border_color:
        stroke_width = border_width
        stroke_color = html.escape(border_color)
        stroke_inset = stroke_width / 2.0
        border_rect = (
            f'<rect x="{stroke_inset}" y="{stroke_inset}" '
            f'width="{max(board_width - stroke_width, 0)}" '
            f'height="{max(board_height - stroke_width, 0)}" '
            f'fill="none" stroke="{stroke_color}" stroke-width="{stroke_width}"'
            f"{border_dash_attrs(resolved_style.border)} "
            f'rx="{max(border_radius - stroke_inset, 0)}"/>'
        )

    board_group = translate_group(
        px(board_margin_left),
        px(board_margin_top),
        f"{bg_rect}\n{border_rect}\n{''.join(inner_items)}",
    )

    svg = f"""<svg width="{total_svg_width}" height="{total_svg_height}" viewBox="0 0 {total_svg_width} {total_svg_height}">
{board_group}
</svg>"""
    return svg, total_svg_height
