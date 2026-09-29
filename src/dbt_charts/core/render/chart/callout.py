"""Wrapped SVG renderer for callout charts (type: callout)."""

from __future__ import annotations

import hashlib
import html
from dataclasses import dataclass

from dbt_charts.core.compile.models.chart.resolved.callout import ResolvedCalloutChart
from dbt_charts.core.compile.models.style.resolved import ResolvedCalloutStyle
from dbt_charts.core.diagnostics.registry import build_doc_url
from dbt_charts.core.font_measure import get_font_measurer
from dbt_charts.core.fonts import get_font_path, get_mono_font_path
from dbt_charts.core.render.chart.text_truncation import record_text_truncation
from dbt_charts.core.render.svg_utils import (
    authored_kind_attr,
    border_dash_attrs,
    escape_attr,
)
from mdsvg import Style as MdsvgStyle, parse as parse_md
from mdsvg.fonts import wrap_text_precise
from mdsvg.renderer import SVGRenderer as MdsvgRenderer

_MIN_HEIGHT = 60.0
_LINE_HEIGHT_RATIO = 1.35
# Keep cards readable without letting a long stack trace consume the full board.
_MAX_MESSAGE_LINES = 12
_MAX_TITLE_LINES = 3


@dataclass
class _ContentBlock:
    """Rendered title or message fragment ready for SVG assembly."""

    elements: str  # SVG element strings (relative — inside a translate <g>)
    style: str  # CSS style block (empty for plain-text blocks)
    height: float  # measured content height in pixels


def _plain_block(
    text: str,
    content_width: float,
    font_size: float,
    font_family: str,
    fill: str,
    max_lines: int,
) -> _ContentBlock:
    """Wrap ``text`` with ``wrap_text_precise`` and emit plain ``<text>`` nodes.

    Used for ``markdown=False`` (runtime error messages) so that identifiers
    like ``_variable_name_`` and ``*args`` never get parsed as italic/bold.
    No truncation is recorded here: every ``markdown=False`` caller renders a
    machine-generated error placard with no chart identity, so there is no
    authored YAML line for a warning to point at.
    """
    measurer = get_font_measurer(font_family)
    wrapped_lines, _ = wrap_text_precise(
        text,
        content_width,
        font_size,
        measurer,
        max_lines=max_lines,
        ellipsis=True,
    )
    lines = wrapped_lines or [text]
    line_height = font_size * _LINE_HEIGHT_RATIO
    parts: list[str] = []
    for i, line in enumerate(lines):
        y = font_size + i * line_height
        parts.append(
            f'<text x="0" y="{escape_attr(y)}" font-size="{escape_attr(font_size)}" '
            f'font-family="{escape_attr(font_family)}" '
            f'fill="{escape_attr(fill)}" text-anchor="start">{html.escape(line)}</text>'
        )
    return _ContentBlock(
        elements="\n  ".join(parts),
        style="",
        height=len(lines) * line_height,
    )


def _callout_md_renderer(
    font_family: str,
    font_size: float,
    font_weight: str | float,
    bold_font_weight: str,
    text_color: str,
    link_color: str,
) -> MdsvgRenderer:
    """Build an mdsvg renderer configured for callout card text.

    ``font_path`` is resolved from ``font_family`` so mdsvg measures line
    breaks and heights with the same font that is rendered in the SVG.
    Using the system default measurer (no font_path) produces wrong wrap
    points whenever the theme font differs from the system default.
    """
    # mdsvg Style.font_weight=None omits the CSS declaration so per-element inline
    # styles (bold spans) override cleanly; pass None for normal weight.
    # float (e.g. 400.0) → int; "normal"/"400" → None; other str → str.
    coerced_fw = int(font_weight) if isinstance(font_weight, float) else font_weight
    style = MdsvgStyle(
        font_family=font_family,
        base_font_size=font_size,
        font_weight=(
            None if str(coerced_fw).lower() in {"400", "normal"} else coerced_fw
        ),
        bold_font_weight=bold_font_weight,
        line_height=_LINE_HEIGHT_RATIO,
        text_color=text_color,
        heading_color=text_color,
        link_color=link_color,
        link_underline=True,
        code_color=text_color,
        code_background="rgba(0,0,0,0.07)",
        paragraph_spacing=font_size * 0.5,
        list_item_spacing=2.0,
    )
    return MdsvgRenderer(
        style=style,
        font_path=get_font_path(font_family),
        mono_font_path=str(get_mono_font_path()),
        fetch_image_sizes=False,
    )


def render_callout_svg(
    *,
    message: str,
    width: float,
    height: float | None = None,
    title: str | None = None,
    callout_style: ResolvedCalloutStyle,
    code: str | None = None,
    hint: str | None = None,
    doc_url: str | None = None,
    markdown: bool = True,
    chart_id: str = "",
) -> str:
    """Render a wrapped inline callout card as SVG.

    When ``markdown=True`` (the default, for authored ``type: callout`` charts),
    title and message are parsed as Markdown — bold, italic, inline code, and
    links render as styled SVG elements.

    When ``markdown=False`` (for runtime error fallbacks), Markdown inline
    specials are escaped before parsing so that ``_variable_name_`` and
    ``*args`` render as literal text instead of italic/bold.

    Hint remains plain text; doc_url renders as a short link label so long URLs
    do not dominate compact error cards.

    Background/border/title/message colors are already resolved onto
    ``callout_style`` from its tone's ``{tone}.*`` palette roles (bg, border,
    solid, text) at chart-resolve time — see ``_build_resolved_callout_chart``.
    """
    inline = callout_style
    pad_left = inline.padding.left
    pad_right = inline.padding.right
    pad_top = inline.padding.top
    pad_bottom = inline.padding.bottom
    section_gap = inline.section_gap

    background = inline.background
    border_color = inline.border.color

    w = max(width, 120.0)
    content_width = max(w - pad_left - pad_right, 40.0)

    title_rf = inline.title.font  # ResolvedFontStyle — all fields concrete
    message_rf = inline.message.font
    title_color = title_rf.color
    message_color = message_rf.color

    # Per-callout unique hash: ensures clip-path ids don't collide when
    # multiple callouts are composited into one SVG root (mdsvg scopes each
    # renderer's own CSS classes to a hash of its style, so class collisions
    # are handled there instead). Includes all resolved style inputs that
    # affect clip geometry so callouts with the same content but different
    # themes don't collide. Over-keyed rather than minimal: tone and
    # the resolved colors do not move the clip rects (padding does — it sets
    # content_width above), and keying on more than necessary only costs a
    # different id for a same-shaped clip.
    callout_hash = hashlib.md5(  # noqa: S324 — non-cryptographic hash for stable SVG ids
        "\x00".join(
            [
                title or "",
                message,
                inline.tone,
                str(w),
                str(pad_left),
                str(pad_right),
                str(pad_top),
                str(pad_bottom),
                str(section_gap),
                title_rf.family,
                str(title_rf.size),
                str(title_rf.weight),
                message_rf.family,
                str(message_rf.size),
                str(message_rf.weight),
                title_color,
                message_color,
            ]
        ).encode(),
        usedforsecurity=False,
    ).hexdigest()[:8]

    # Markdown renderers — title uses solid color (accent); message uses text color.
    # Link color uses solid for both so links stand out against the tone background.
    title_renderer = _callout_md_renderer(
        font_family=title_rf.family,
        font_size=title_rf.size,
        font_weight=title_rf.weight,
        bold_font_weight=inline.bold_font_weight,
        text_color=title_color,
        link_color=title_color,
    )
    message_renderer = _callout_md_renderer(
        font_family=message_rf.family,
        font_size=message_rf.size,
        font_weight=message_rf.weight,
        bold_font_weight=inline.bold_font_weight,
        text_color=message_color,
        link_color=title_color,
    )

    title_block = None
    if title:  # skip None and empty-string titles
        if markdown:
            _tr = title_renderer.render_content(
                list(parse_md(title)), width=content_width, padding=0.0
            )
            title_block = _ContentBlock(_tr.elements, _tr.style_block, _tr.height)
        else:
            title_block = _plain_block(
                title,
                content_width,
                title_rf.size,
                title_rf.family,
                title_color,
                _MAX_TITLE_LINES,
            )

    if markdown:
        _mr = message_renderer.render_content(
            list(parse_md(message)), width=content_width, padding=0.0
        )
        message_block = _ContentBlock(_mr.elements, _mr.style_block, _mr.height)
    else:
        message_block = _plain_block(
            message,
            content_width,
            message_rf.size,
            message_rf.family,
            message_color,
            _MAX_MESSAGE_LINES,
        )

    # Hint and doc link: plain SVG text with special typographic roles. Not
    # parsed as markdown.
    message_measurer = get_font_measurer(message_rf.family)
    hint_lines: list[str] = []
    if hint:
        hint_wrapped, _ = wrap_text_precise(
            hint,
            content_width,
            message_rf.size,
            message_measurer,
            max_lines=3,
            ellipsis=True,
        )
        hint_lines = hint_wrapped or [hint]

    doc_link_label = "See the docs" if doc_url else ""

    # Height accounting.
    max_title_height = _MAX_TITLE_LINES * title_rf.size * _LINE_HEIGHT_RATIO
    max_message_height = _MAX_MESSAGE_LINES * message_rf.size * _LINE_HEIGHT_RATIO

    # Markdown-rendered blocks are capped visually — detect overflow after the
    # height constants are known but before clamping to block_height.
    if markdown and chart_id:
        if title and title_block is not None and title_block.height > max_title_height:
            record_text_truncation(chart_id, "callout_text", title, "title")
        if message_block.height > max_message_height:
            record_text_truncation(chart_id, "callout_text", message, "message")
    title_block_height = (
        min(title_block.height, max_title_height) if title_block else 0.0
    )
    message_block_height = min(message_block.height, max_message_height)
    message_line_height = message_rf.size * _LINE_HEIGHT_RATIO
    doc_url_line_height = message_rf.size * 0.85 * _LINE_HEIGHT_RATIO

    hint_block_height = len(hint_lines) * message_line_height if hint_lines else 0.0
    doc_url_block_height = doc_url_line_height if doc_link_label else 0.0

    # Code badge height (small monospace text above title)
    code_badge_height = (title_rf.size * 0.75 * _LINE_HEIGHT_RATIO) if code else 0.0
    title_gap = section_gap if title_block else 0.0

    natural_height = (
        pad_top
        + pad_bottom
        + code_badge_height
        + (section_gap if code else 0.0)
        + title_block_height
        + title_gap
        + message_block_height
        + (section_gap + hint_block_height if hint_lines else 0.0)
        + (section_gap + doc_url_block_height if doc_link_label else 0.0)
    )
    h = max(_MIN_HEIGHT, height or 0.0, natural_height)

    current_y = pad_top

    # Code badge (monospace, no markdown — error codes like "ERR-001")
    code_svg = ""
    if code:
        code_font_size = title_rf.size * 0.75
        code_y = current_y + code_font_size
        code_text_svg = (
            f'<text x="{escape_attr(pad_left)}" y="{escape_attr(code_y)}" '
            f'font-size="{escape_attr(code_font_size)}" font-weight="normal" '
            f'font-family="monospace" '
            f'fill="{escape_attr(message_color)}">{html.escape(code)}</text>'
        )
        code_svg = (
            f'<a href="{escape_attr(build_doc_url(code))}" target="_blank">'
            f"{code_text_svg}</a>"
        )
        current_y += code_badge_height + section_gap

    # title_renderer and message_renderer each scope their own `.md-*` classes
    # to a hash of their style (mdsvg), so title and message never collide
    # even when composited into the same SVG root.
    title_elements = title_block.elements if title_block else ""
    title_style_block = title_block.style if title_block else ""
    message_elements, message_style_block = message_block.elements, message_block.style
    style_block = f"{title_style_block}{message_style_block}"

    # Title markdown block — clipped if content exceeds _MAX_TITLE_LINES.
    title_svg = ""
    if title_block:
        translate_y = current_y + inline.title.y_offset
        if title_block.height > max_title_height:
            title_clip_id = f"title-clip-{callout_hash}"
            title_clip_def = (
                f'<defs><clipPath id="{escape_attr(title_clip_id)}">'
                f'<rect x="0" y="0" width="{escape_attr(content_width)}" height="{escape_attr(title_block_height)}"/>'
                f"</clipPath></defs>"
            )
            title_svg = (
                f"{title_clip_def}"
                f'<g transform="translate({escape_attr(pad_left)}, {escape_attr(translate_y)})" clip-path="url(#{escape_attr(title_clip_id)})"'
                f"{authored_kind_attr('title')}>"
                f"{title_elements}"
                f"</g>"
            )
        else:
            title_svg = (
                f'<g transform="translate({escape_attr(pad_left)}, {escape_attr(translate_y)})"{authored_kind_attr("title")}>'
                f"{title_elements}"
                f"</g>"
            )
        current_y += title_block_height + section_gap

    # Message markdown block — clipped if content exceeds _MAX_MESSAGE_LINES.
    translate_y = current_y + inline.message.y_offset
    if message_block.height > max_message_height:
        msg_clip_id = f"msg-clip-{callout_hash}"
        clip_def = (
            f'<defs><clipPath id="{escape_attr(msg_clip_id)}">'
            f'<rect x="0" y="0" width="{escape_attr(content_width)}" height="{escape_attr(message_block_height)}"/>'
            f"</clipPath></defs>"
        )
        message_svg = (
            f"{clip_def}"
            f'<g transform="translate({escape_attr(pad_left)}, {escape_attr(translate_y)})" clip-path="url(#{escape_attr(msg_clip_id)})">'
            f"{message_elements}"
            f"</g>"
        )
    else:
        message_svg = (
            f'<g transform="translate({escape_attr(pad_left)}, {escape_attr(translate_y)})">'
            f"{message_elements}"
            f"</g>"
        )
    current_y += message_block_height

    # Hint (plain italic text — short annotation below the message body)
    hint_svg = ""
    if hint_lines:
        hint_start_y = current_y + section_gap
        hint_svg = "".join(
            f'<text x="{escape_attr(pad_left)}" y="{escape_attr(hint_start_y + (i * message_line_height))}" '
            f'font-size="{escape_attr(message_rf.size)}" font-weight="normal" font-style="italic" '
            f'font-family="{escape_attr(message_rf.family)}" '
            f'fill="{escape_attr(message_color)}">{html.escape(line)}</text>'
            for i, line in enumerate(hint_lines)
        )

    # Doc link (plain small text)
    doc_svg = ""
    if doc_url:
        doc_url_font_size = message_rf.size * 0.85
        after_hint_y = current_y + (
            section_gap + hint_block_height if hint_lines else 0.0
        )
        doc_start_y = after_hint_y + section_gap
        doc_text_svg = (
            f'<text x="{escape_attr(pad_left)}" y="{escape_attr(doc_start_y)}" '
            f'font-size="{escape_attr(doc_url_font_size)}" font-weight="normal" '
            f'font-family="{escape_attr(message_rf.family)}" '
            f'fill="{escape_attr(title_color)}">{doc_link_label}</text>'
        )
        doc_svg = f'<a href="{escape_attr(doc_url)}" target="_blank">{doc_text_svg}</a>'

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" class="dbt-chart-callout" '
        f'width="{escape_attr(w)}" height="{escape_attr(h)}" viewBox="0 0 {escape_attr(w)} {escape_attr(h)}">'
        f'<rect x="0" y="0" width="{escape_attr(w)}" height="{escape_attr(h)}" '
        f'fill="{escape_attr(background)}" stroke="{escape_attr(border_color)}" '
        f'stroke-width="{escape_attr(inline.border.width)}"{border_dash_attrs(inline.border)} '
        f'rx="{escape_attr(inline.border.radius)}"/>'
        f"{style_block}"
        f"{code_svg}{title_svg}{message_svg}{hint_svg}{doc_svg}"
        f"</svg>"
    )


def render_callout_chart_svg(
    chart: ResolvedCalloutChart,
    data: list[dict[str, object]],
    width: float | None = None,
    height: float | None = None,
    is_placeholder: bool = False,
) -> str:
    """Render-v2 wrapper for an authored ``type: callout`` chart.

    Byte-identical to ``render_callout_chart_svg`` for the same authored
    input — parity is pinned by test_v2_callout_svg_parity.py.
    """
    _ = data, is_placeholder
    preferred_width = chart.style.preferred_width if width is None else width
    return render_callout_svg(
        message=chart.message,
        width=preferred_width,
        height=height,
        title=chart.title,
        callout_style=chart.style,
        chart_id=chart.id,
    )
