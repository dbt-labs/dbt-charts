"""The dct theme -> ``mdsvg.Style`` mapping, and the families prose and titles draw in.

A pure function of ``ResolvedStyle``: compile-time sizing measures prose and
titles with it, and render paints with it, so both ask one place.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, Literal

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.image_assets import rewrite_board_markdown_images
from dbt_charts.core.compile.markdown_blocks import map_span_runs
from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.models.style.theme import font_weight_as_css
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.compile.resolve.style.tokens import apply_emoji_to_family
from dbt_charts.core.compile.resolve.style.typography import (
    _coerce_weight,
    board_title_markdown,
    board_title_spec,
)
from dbt_charts.core.compile.template.jinja import resolve_jinja_template
from dbt_charts.core.text.case import apply_case_to_runs
from mdsvg import parse as parse_markdown
from mdsvg.types import SpanType

if TYPE_CHECKING:
    from dbt_charts.core.compile.markdown_blocks import SpanRun
    from dbt_charts.core.diagnostics.diagnostic import Diagnostic
    from dbt_charts.core.text.case import CaseValue
    from mdsvg import Style
    from mdsvg.renderer import SVGRenderer
    from mdsvg.types import AnyBlock


def _mdsvg_case(case: str | None, scope: str) -> str:
    """Map a FontStyle ``case`` value to what mdsvg paints (upper/lower only).

    mdsvg is dependency-light and only does upper/lower string transforms. The
    richer dct cases (``sentence``/``title``/``slug``/``camel``) rely on the
    ``titlecase`` dep and would silently no-op in mdsvg, so reject them loudly
    rather than ship a dead knob.
    """
    if case is None or case == "none":
        return ""
    if case in {"upper", "lower"}:
        return case
    raise CompilationError(
        f"style.text.{scope}.font.case={case!r} is not supported for markdown "
        f"prose; use 'upper' or 'lower'."
    )


# mdsvg.Style fields owned entirely by mdsvg (spacing/layout/image/measurement
# constants). These are NOT driven by the dct theme cascade. New mdsvg fields must
# be bucketed here (structural) or wired into get_compact_style (theme-driven).
# The dct<->mdsvg contract test in tests/core/test_text_box_style_cascade.py
# enforces this: every mdsvg.Style field is either mapper-fed or in this set.
MDSVG_STRUCTURAL_FIELDS: frozenset[str] = frozenset(
    {
        # Font metrics / text measurement
        "mono_font_family",  # mdsvg detects mono font; dct passes font_family separately
        "link_underline",  # always True; no dct token
        "char_width_ratio",
        "bold_char_width_ratio",
        "italic_char_width_ratio",
        # Heading scales (unused — we pass absolute h1..h6 sizes instead)
        "h1_scale",
        "h2_scale",
        "h3_scale",
        "h4_scale",
        "h5_scale",
        "h6_scale",
        # Em-of-heading margin fallbacks (unused — dct drives heading_margin_top_px /
        # heading_margin_bottom_px so the after-heading gap stays one body line
        # regardless of H level)
        "heading_margin_top",
        "heading_margin_bottom",
        # Layout / spacing constants (not theme-cascaded)
        "list_indent",
        "list_item_spacing",
        "code_block_padding",
        "code_block_overflow",
        # Code size as a fraction of its surrounding text. mdsvg owns the ratio;
        # the theme sets no code font size, so this is what actually sizes code.
        # Promote to a theme token only if a theme needs a different ratio —
        # style.text.code.font.size is already the per-board absolute escape hatch.
        "code_font_scale",
        # Inline code chip geometry — not in the dbt charts theme yet; mdsvg owns
        # the defaults (3px padding, 3px radius). Add to the mapper when dct
        # exposes style.text.code.inline_padding / .inline_radius theme tokens.
        "inline_code_padding",
        "inline_code_border_radius",
        "blockquote_padding",
        "table_cell_padding",
        "hr_height",
        # Image layout constants
        "image_width",
        "image_height",
        "image_fallback_aspect_ratio",
        "image_enforce_aspect_ratio",
    }
)


def get_compact_style(
    resolved_style: ResolvedStyle | None = None,
    text_align: Literal["left", "center", "right"] = "left",
    heading_font_weight: int | str | None = None,
    font_family: str | None = None,
    h1_size: float | None = None,
    heading_margin_scale: Literal["prose", "chrome"] = "prose",
) -> Style:
    """Get a compact mdsvg Style with reduced heading margins (theme-derived colors).

    Thin wrapper over :func:`compact_style_kwargs` (the dct→mdsvg color/size mapper);
    the contract test reads the fed-field set from that helper directly.
    """
    from mdsvg import Style

    return Style(
        **compact_style_kwargs(
            resolved_style,
            text_align,
            heading_font_weight,
            font_family,
            h1_size,
            heading_margin_scale,
        )
    )


def compact_style_kwargs(
    resolved_style: ResolvedStyle | None = None,
    text_align: Literal["left", "center", "right"] = "left",
    heading_font_weight: int | str | None = None,
    font_family: str | None = None,
    h1_size: float | None = None,
    heading_margin_scale: Literal["prose", "chrome"] = "prose",
) -> dict[str, Any]:  # type-state: explicit_any — heterogeneous Style kwargs
    """Build the ``mdsvg.Style`` kwargs from the resolved theme — the dct→mdsvg mapper.

    The default mdsvg style has large heading margins (1.5em top, 0.5em bottom)
    which produces ~2x more vertical space than HTML. This compact style
    reduces margins for tighter dashboard/UI layouts.

    Heading sizes (h1–h6) are pinned to absolute pixel values from
    ``style.fonts.heading.sizes`` rather than computed as multiples of the body
    font size. Heading weight defaults to ``style.title.font.weight``; heading
    color comes from ``style.title.font.color``.

    All prose colors derive from resolved theme tokens — no hardcoded table.
    Paragraph spacing is derived from the body rhythm:
    ``font_size * line_height * 0.5``.

    Args:
        resolved_style: Optional ResolvedStyle for color resolution. Falls back to
            the global config default when None.
        heading_font_weight: Override the heading font weight. Board titles pass
            the tier-specific weight (600 narrow, 500 medium/wide).
        font_family: Override the body font family used by mdsvg.
        h1_size: Override the h1 pixel size. Board titles pass the
            level-resolved pixel size from ``board_title_spec`` so the rendered
            h1 lands exactly on the semantic-level target. When ``None``, h1
            uses ``style.fonts.heading.sizes[0]`` like the other levels.

    Returns:
        A dict of ``mdsvg.Style`` keyword arguments. Every key is an
        ``mdsvg.Style`` field; the dct<->mdsvg contract test asserts each
        ``Style`` field is fed here or listed in ``MDSVG_STRUCTURAL_FIELDS``.
    """
    _rs = resolved_style or resolve_style(get_theme_style())

    _text_font_size = _rs.text.font.size
    assert _text_font_size is not None, "style.text.font.size must be configured"
    # Heading margins are in line-height units, so they need a font size to resolve
    # against. "prose" is right for a markdown heading inside a text block — the gap
    # under it should equal one line of the prose it introduces. "chrome" is right for
    # a board title, which labels a card that may hold a chart, a table, or nothing
    # textual at all: its spacing must not move when prose is resized. Chart card
    # titles already work this way (size varies by width tier, gap does not).
    _chrome_font_size = _rs.font.size
    assert _chrome_font_size is not None, "style.font.size must be configured"
    _chrome_line_height = _rs.font.line_height
    assert _chrome_line_height is not None, "style.font.line_height must be configured"
    _text_font_weight = _rs.text.font.weight
    assert _text_font_weight is not None, "style.text.font.weight must be configured"
    _text_line_height = _rs.text.font.line_height
    assert _text_line_height is not None, (
        "style.text.font.line_height must be configured"
    )
    # One line of the chosen scale, in px. A heading margin of 1.5 means "one and a
    # half lines of the text this heading belongs to", so size and leading must come
    # from the same scale — mixing prose size with chrome leading (or vice versa)
    # silently produces a unit that matches neither.
    _heading_margin_unit = (
        float(_text_font_size) * float(_text_line_height)
        if heading_margin_scale == "prose"
        else float(_chrome_font_size) * float(_chrome_line_height)
    )
    _title_line_height = _rs.title.font.line_height
    assert _title_line_height is not None, (
        "style.title.font.line_height must be configured"
    )

    _text_font_family = _rs.text.font.family
    assert _text_font_family is not None, "style.text.font.family must be configured"
    _default_family = apply_emoji_to_family(_text_font_family, _rs.emoji_mode)

    _heading_font_family = _rs.title.font.family
    assert _heading_font_family is not None, (
        "style.title.font.family must be configured"
    )
    _heading_family = apply_emoji_to_family(_heading_font_family, _rs.emoji_mode)

    _text_font_color = _rs.text.font.color
    assert _text_font_color is not None, "style.text.font.color must be configured"

    title_sizes = _rs.chart_defaults.heading_sizes
    if len(title_sizes) != 6:
        raise ValueError(
            "style.fonts.heading.sizes must have 6 entries (H1-H6); "
            f"got {len(title_sizes)}"
        )

    title_weight = _rs.title.font.weight or 500  # type-state: silent_fallback — default
    effective_weight = (
        heading_font_weight
        if heading_font_weight is not None
        else _coerce_weight(title_weight)
    )
    text_weight: int | str | None = _coerce_weight(_text_font_weight)
    if str(text_weight).lower() in {"400", "normal"}:
        text_weight = None

    # Code box tokens from theme cascade
    _code = _rs.text.code
    _bq = _rs.text.blockquote
    _bold = _rs.text.bold
    # code_color: prefer code font color if set; fall back to board chrome ink
    _code_color: str = _code.font.color or _rs.font.color

    # table header background: prefer table.header.background, fall back to canvas
    _table_header_bg: str = _rs.chart_defaults.table.header.background or _rs.background

    kwargs: dict[str, Any] = {  # type-state: explicit_any — heterogeneous Style kwargs
        "font_family": font_family if font_family is not None else _default_family,
        # Headings (board titles and markdown H1-H6 alike) read the title
        # family, independent of whichever family `font_family` above resolved
        # to for body/paragraph text — the same split the size, color, and
        # weight keys below already make for `heading_*`.
        "heading_font_family": _heading_family,
        "base_font_size": float(_text_font_size),
        "font_weight": text_weight,
        "line_height": float(_text_line_height),
        # Block margins from theme: font_size × line_height × margin_* (in lh units).
        # mdsvg ≥ 1.0 skips paragraph_spacing on heading-adjacent transitions and
        # collapses H↔H margins to max — so heading_margin_*_px are the literal
        # visible space above / below a heading.
        "heading_margin_top_px": _heading_margin_unit
        * float(_rs.text.heading.margin_top),
        "heading_margin_bottom_px": _heading_margin_unit
        * float(_rs.text.heading.margin_bottom),
        "paragraph_spacing": float(_text_font_size)
        * float(_text_line_height)
        * float(max(_rs.text.paragraph.margin_top, _rs.text.paragraph.margin_bottom)),
        "text_color": _text_font_color,
        "heading_color": _rs.title.font.color,
        "link_color": _rs.accent,
        "code_color": _code_color,
        "code_background": _code.background,
        "code_block_border_color": _code.border.color,
        "code_block_border_width": float(_code.border.width),
        "code_block_border_radius": float(_code.border.radius),
        "code_highlight": _code.highlight,
        "code_theme": _code.theme,
        # Code font axes
        "code_font_family": _code.font.family
        or "",  # type-state: silent_fallback — unset theme font fields map to the empty string mdsvg expects
        "code_font_weight": (
            font_weight_as_css(_code.font.weight)
            if _code.font.weight is not None
            else ""
        ),
        "code_font_style": _code.font.style
        or "",  # type-state: silent_fallback — unset theme font fields map to the empty string mdsvg expects
        "code_font_size": (
            float(_code.font.size) if _code.font.size is not None else 0.0
        ),
        "code_font_decoration": _code.font.decoration
        or "",  # type-state: silent_fallback — unset theme font fields map to the empty string mdsvg expects
        "code_font_case": _mdsvg_case(_code.font.case, "code"),
        "blockquote_color": _bq.font.color or _rs.font.color,
        "blockquote_border_color": _bq.border.color,
        "blockquote_border_width": float(_bq.border.width),
        "blockquote_border_radius": float(_bq.border.radius),
        "blockquote_background": _bq.background,
        # Blockquote font axes
        "blockquote_font_family": _bq.font.family
        or "",  # type-state: silent_fallback — unset theme font fields map to the empty string mdsvg expects
        "blockquote_font_weight": (
            font_weight_as_css(_bq.font.weight) if _bq.font.weight is not None else ""
        ),
        "blockquote_font_style": _bq.font.style
        or "",  # type-state: silent_fallback — unset theme font fields map to the empty string mdsvg expects
        "blockquote_font_size": (
            float(_bq.font.size) if _bq.font.size is not None else 0.0
        ),
        "blockquote_font_decoration": _bq.font.decoration
        or "",  # type-state: silent_fallback — unset theme font fields map to the empty string mdsvg expects
        "blockquote_font_case": _mdsvg_case(_bq.font.case, "blockquote"),
        "table_border_color": _rs.text.rule.color,
        "table_header_background": _table_header_bg,
        "hr_color": _rs.text.rule.color,
        "text_align": text_align,
        # Top-align letterboxed markdown images (default xMidYMid centers them).
        "image_preserve_aspect_ratio": "xMidYMin meet",
        "h1_size": float(h1_size) if h1_size is not None else float(title_sizes[0]),
        "h2_size": float(title_sizes[1]),
        "h3_size": float(title_sizes[2]),
        "h4_size": float(title_sizes[3]),
        "h5_size": float(title_sizes[4]),
        "h6_size": float(title_sizes[5]),
        "heading_font_weight": effective_weight,
        "heading_line_height": float(_title_line_height),
        "bold_font_weight": font_weight_as_css(_bold.weight),
        # dbt charts opts in; mdsvg leaves it off for other consumers.
        "avoid_runts": True,
    }

    return kwargs


def body_text_font_family(resolved_style: ResolvedStyle) -> str:
    """Return the resolved body markdown family."""
    family = resolved_style.text.font.family
    assert family is not None, "style.text.font.family must be configured"
    return family


def title_font_family(resolved_style: ResolvedStyle) -> str:
    """The family a board title is drawn in.

    Every board title — prose or not — is drawn as a heading
    (``board_title_markdown`` always emits ``# {title}``), and
    ``style.title.font.family`` is the one token that names a heading's
    family; a board title never draws in the body family.

    Measuring and drawing have to ask this once and get the same answer —
    reserving a column in one family and painting the title in another is the
    defect this exists to prevent.
    """
    family = resolved_style.title.font.family
    assert family is not None, "style.title.font.family must be configured"
    return family


def prose_max_content_width(
    text: str,
    resolved_style: ResolvedStyle,
    variable_values: VariableValues,
    base_dir: str | None,
) -> float:
    """Width ``text`` needs to draw with no line wrapped, in the body typography,
    measured on the blocks the paint pass draws: images resolved against
    *base_dir*, a bad one degraded to its error message."""
    from dbt_charts.core.font_measure import markdown_font_faces
    from mdsvg.renderer import SVGRenderer

    resolved = resolve_jinja_template(text, variable_values, strict=False)
    blocks = rewrite_board_markdown_images(
        list(parse_markdown(resolved)), text, resolved, base_dir, None
    )
    style = get_compact_style(resolved_style)
    # A compile never fetches over the network; mdsvg measures a data: URI regardless.
    renderer = SVGRenderer(
        style=style,
        fonts=markdown_font_faces(body_text_font_family(resolved_style), style),
        fetch_image_sizes=False,
    )
    return renderer.max_content_width(blocks)


def title_max_content_width(
    title: str,
    level: int,
    resolved_style: ResolvedStyle,
    variable_values: VariableValues,
    base_dir: str | None,
) -> float:
    """Width a board title needs to draw on one line, asked of the renderer and
    blocks that paint it."""
    renderer, _ = title_renderer(resolved_style, level)
    blocks = title_blocks(title, variable_values, level, resolved_style, base_dir, None)
    return renderer.max_content_width(blocks)


def title_renderer(
    resolved_style: ResolvedStyle,
    level: int,
    text_align: Literal["left", "center", "right"] = "left",
) -> tuple[SVGRenderer, str]:
    """The mdsvg renderer a board title is drawn with, and the family it resolves to.

    One construction shared by every question asked about a title — what it looks
    like, where its baseline lands, which part of its block is text — because they
    have to be asked of the same renderer. Built separately they can disagree
    about the typeface or the heading size, and the answers then describe a title
    nobody drew.

    ``font_family`` is always the title family, unconditionally: the title's own
    ink is always a heading, which paints via ``Style.heading_font_family``
    regardless of ``font_family`` — but a title's markdown can still carry a
    non-heading span (an emphasis run, or a second block on a multi-line
    title), and that span paints through mdsvg's ``.md-*-text`` rule, which
    ``font_family`` does control. Leaving it unset there let a measure and a
    paint of the SAME non-heading span disagree on family whenever the
    caller's own choice of ``font_family`` differed from the title family —
    the same defect this whole module exists to prevent, just one rule over.
    """
    from dbt_charts.core.font_measure import markdown_font_faces
    from mdsvg.renderer import SVGRenderer

    font_size, heading_weight = board_title_spec(
        level=level, resolved_style=resolved_style
    )
    family = title_font_family(resolved_style)
    style = get_compact_style(
        resolved_style,
        text_align=text_align,
        h1_size=font_size,
        heading_font_weight=heading_weight,
        font_family=family,
        # Title spacing is chrome: it must not grow when body prose is resized.
        heading_margin_scale="chrome",
    )
    # fetch_image_sizes=False: a render never fetches over the network. A local
    # image is an embedded data: URI by now, which mdsvg measures regardless.
    renderer = SVGRenderer(
        style=style,
        fonts=markdown_font_faces(family, style),
        fetch_image_sizes=False,
    )
    return renderer, family


def title_blocks(
    title: str,
    variable_values: VariableValues | None,
    level: int,
    resolved_style: ResolvedStyle,
    base_dir: str | None,
    error_collector: list[Diagnostic] | None,
) -> list[AnyBlock]:
    """The markdown blocks a board title is drawn as, asked by the measure and
    the paint alike: Jinja resolved, formatted as the title heading,
    ``style.title.font.case`` applied to its words, and its images resolved
    against *base_dir*, the directory of the file the title is written in.
    """
    resolved_title = resolve_jinja_template(title, variable_values, strict=False)
    markdown_title, _, _ = board_title_markdown(
        resolved_title, level=level, resolved_style=resolved_style
    )
    blocks = list(parse_markdown(markdown_title))
    title_case = resolved_style.title.font.case
    if title_case is not None:
        blocks = map_span_runs(
            blocks, lambda run: _case_words(run, title_case), lambda image: image
        )
    return rewrite_board_markdown_images(
        blocks, title, resolved_title, base_dir, error_collector
    )


def _case_words(spans: SpanRun, case: CaseValue) -> SpanRun:
    """Apply *case* to the words of one run of spans as one string
    (``apply_case_to_runs``), however inline formatting splits them.

    Code spans and images keep their text: code is literal, and an image's
    URL is data (upper-casing ``logo.svg`` names a different file).
    """
    words = [s for s in spans if s.span_type not in _UNCASED_SPANS]
    cased = iter(apply_case_to_runs([s.text for s in words], case))
    return tuple(
        s if s.span_type in _UNCASED_SPANS else replace(s, text=next(cased))
        for s in spans
    )


_UNCASED_SPANS = frozenset({SpanType.CODE, SpanType.IMAGE})
