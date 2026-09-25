"""SVG utility functions for rendering.

Stage: RENDER
Purpose: Provide SVG-specific utilities for dbt charts rendering.

This module handles:
- Grid pattern generation (loaded from template file)
- SVG styles generation (loaded from template file)
- Title rendering (using mdsvg)
- Error message rendering (using template file)
- SVG content extraction and dimension parsing

Dependencies:
    - .template_loader (for Jinja2 template loading)
    - .themes (for theme colors)
    - mdsvg (required, for markdown rendering and text wrapping)
"""

import html
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

# ---------------------------------------------------------------------------
# Pixel snapping
# ---------------------------------------------------------------------------


def format_svg_numeric(value: float) -> str:
    """Render integer-valued floats without a trailing .0."""
    numeric = float(value)
    return str(int(numeric)) if numeric.is_integer() else str(numeric)


def px(v: float) -> int:
    """Snap a float coordinate to the nearest integer pixel.

    SVG transforms are applied before pixel rasterization: a sub-pixel ancestor
    translate pushes a 1px-tall rule onto fractional rows and the rasterizer
    splits it across two pixels at reduced opacity — reading visually as "thin"
    or "faded". Rounding each emitted translate keeps every cumulative transform
    on an integer boundary, so rules, rects, and other 1px structural marks stay
    crisp.

    Uses Python's built-in ``round()``, which applies banker's rounding
    (round-half-to-even): ``px(24.5) == 24``, ``px(25.5) == 26``. Values
    that fall exactly on a .5 boundary may round either direction; this is
    non-monotonic but produces the least cumulative drift over long sequences.
    """
    return int(round(v))


def translate_group(x: int, y: int, content: str, extra_attrs: str = "") -> str:
    """Wrap ``content`` in a translate ``<g>`` — unless the translate is a
    no-op and there is nothing else to carry, in which case ``content`` is
    returned unwrapped.

    An untranslated, attribute-free ``<g>`` still counts against a PDF
    writer's hard limit on graphics-state nesting even though usvg would
    otherwise flatten a truly bare group away — the cost only shows up once
    such groups sit between real clipping levels, which is exactly what a
    deeply nested board does. ``x``/``y`` are pixel-snapped coordinates (the
    caller's own ``px(...)`` output, or a literal ``0``) — this only decides
    whether to open the wrapper, it does not do any snapping itself.
    ``extra_attrs``, like ``authored_kind_attr``'s return value, must carry
    its own leading space when non-empty.
    """
    if x == 0 and y == 0 and not extra_attrs:
        return content
    return f'<g transform="translate({x}, {y})"{extra_attrs}>{content}</g>'


def authored_attrs(path: str, kind: str) -> str:
    """The ``data-authored-path``/``data-authored-kind`` pair every authoring-tagged
    *block* carries, so every emission site (chart wrapper, prose header pieces)
    stays in sync on its shape. Leaves take ``authored_kind_attr`` instead — the
    two together are where this vocabulary is built, and nowhere else."""
    return (
        f' data-authored-path="{html.escape(path, quote=True)}"'
        f' data-authored-kind="{html.escape(kind, quote=True)}"'
    )


def authored_kind_attr(kind: str) -> str:
    """The bare ``data-authored-kind`` a *leaf* text run carries — no path.

    A leaf sits inside a block that already carries the path (a chart's title
    text run inside its ``data-authored-path="charts.rev"`` group, a nested
    board's heading inside its combined ``header`` handle); repeating the path
    on the leaf would just be the same identity twice. Kind alone says which
    authored key the leaf is — see ``authored_attrs`` for the block-level pair.
    """
    return f' data-authored-kind="{html.escape(kind, quote=True)}"'


def selection_boxes(
    outer_width: float,
    outer_height: float,
    padding: dict[str, float],
    mark: tuple[float, float] | None = None,
) -> str:
    """Two non-painting rects marking an authored block's selection boundary,
    in the tagged group's own coordinate frame. ``outer_width``/``outer_height``
    are the padded box's own size — the tagged group's size.

    A tagged group must always contain the block's padding, never sit inside
    it: the padded (outer) box is what a host mark traces, and the unpadded
    (inner) box is what it hit-tests against. One rect can't serve both —
    a ``<g>``'s client rect is the union of its children, so if the only rect
    is the inner one, the outer box is nowhere in the DOM for the mark to
    read.

    ``.dbt-box-outer`` — padding included, inert to the pointer (a host rule). It sits
    over the block's own ink, so intercepting events there would break
    drag-to-select on board prose (a live feature); tracing it is purely
    geometric.
    ``.dbt-box-inner`` — padding excluded, the pointer target (a host rule). Insetting
    the pointer target by the same padding turns the gutter between two
    adjacent blocks into dead space instead of belonging to whichever block
    happens to abut it.

    ``mark`` is ``(y, height)`` for the outer rect alone, for a block that reserved
    more height than its text fills — a heading, whose margin below the words is
    rhythm rather than text. The inner rect deliberately does not follow it:
    these are a mark and a hit target, and there is no reason the rhythm after a
    heading should stop being clickable because the mark no longer covers it. On
    such a block the "outer" rect is the shorter of the two on this axis; outer
    names padding, not containment.
    """
    left, right = padding["left"], padding["right"]
    top, bottom = padding["top"], padding["bottom"]
    inner_width = max(outer_width - left - right, 0.0)
    inner_height = max(outer_height - top - bottom, 0.0)
    # ``mark`` is measured in the content's own frame, which the inner box's
    # padding offsets from this one. Adding ``top`` converts it rather than
    # relying on prose padding staying vertically zero.
    mark_y, mark_height = (
        (mark[0] + top, mark[1]) if mark is not None else (0.0, outer_height)
    )
    return (
        f'<rect class="dbt-box-outer" x="0" y="{px(mark_y)}"'
        f' width="{px(outer_width)}" height="{px(mark_height)}"'
        f' fill="transparent"/>'
        f'<rect class="dbt-box-inner" x="{px(left)}" y="{px(top)}"'
        f' width="{px(inner_width)}" height="{px(inner_height)}"'
        f' fill="transparent"/>'
    )


def padded_authoring_content(
    content: str,
    inner_width: float,
    inner_height: float,
    padding: dict[str, float],
    mark: tuple[float, float] | None = None,
) -> str:
    """The selection-boundary rects plus ``content`` translated inward by
    ``padding`` — for a family whose content is rendered at its un-padded
    (inner) size and needs wrapping to sit inside the padded (outer) box the
    tagged group now carries. Vega families bake padding into the spec
    instead (content already renders at outer size, ink pre-inset) and call
    ``selection_boxes`` directly, without this wrap.
    """
    left, right = padding["left"], padding["right"]
    top, bottom = padding["top"], padding["bottom"]
    outer_width = inner_width + left + right
    outer_height = inner_height + top + bottom
    return f"{selection_boxes(outer_width, outer_height, padding, mark)}" + (
        translate_group(px(left), px(top), content)
    )


def card_box(
    width: float, height: float, inset: dict[str, float] | None
) -> tuple[float, float, float, float]:
    """``(x, y, width, height)`` of a card rect for content rendered at the
    inner ``width`` × ``height``: with ``inset``, the rect reaches back out over
    the padding ``padded_authoring_content`` will translate the content in by,
    so the card fills the outer box the way a Vega chart's background does.
    """
    if inset is None:
        return 0.0, 0.0, width, height
    left, top = inset["left"], inset["top"]
    return -left, -top, width + left + inset["right"], height + top + inset["bottom"]


def border_dash_attrs(border: "BorderStyle") -> str:
    """SVG stroke-dasharray/-linecap/-dashoffset attributes for a dashed border.

    Returns "" (solid border, the default) when ``border.dash_array`` is unset.
    Otherwise a leading-space-separated attribute string ready to interpolate
    directly after a rect/path's existing ``stroke-width="..."`` attribute.
    """
    if border.dash_array is None:
        return ""
    attrs = f' stroke-dasharray="{",".join(f"{v:g}" for v in border.dash_array)}"'
    if border.line_cap is not None:
        attrs += f' stroke-linecap="{border.line_cap}"'
    if border.dash_offset is not None:
        attrs += f' stroke-dashoffset="{border.dash_offset:g}"'
    return attrs


if TYPE_CHECKING:
    from dbt_charts.core.compile.models.primitives import BorderStyle
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle

from dbt_charts.core.render.template_loader import render_template

# Compiled regex patterns for SVG parsing - avoids recompilation on each call
_SVG_CONTENT_RE = re.compile(r"<svg[^>]*>(.*)</svg>", re.DOTALL)
_SVG_WIDTH_RE = re.compile(r'width=["\'](\d+(?:\.\d+)?)["\']')
_SVG_HEIGHT_RE = re.compile(r'height=["\'](\d+(?:\.\d+)?)["\']')
_SVG_VIEWBOX_RE = re.compile(
    r'viewBox=["\']0\s+0\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)["\']'
)


@dataclass
class SVGDimensions:
    """Dimensions extracted from an SVG string."""

    width: float
    height: float


def extract_svg_inner_content(svg: str) -> str:
    """Extract inner content from an SVG string (strip the <svg> wrapper).

    Args:
        svg: Full SVG string with wrapper

    Returns:
        Inner content without <svg>...</svg> wrapper

    Raises:
        ValueError: If SVG content cannot be extracted

    TODO(mdsvg#10): Replace with structured RenderResult when available.
    See: https://github.com/davefowler/markdown-svg/issues/10
    """
    match = _SVG_CONTENT_RE.search(svg)
    if not match:
        raise ValueError(f"Failed to extract SVG content from: {svg[:100]}...")
    return match.group(1)


def extract_svg_dimensions(
    svg: str, default_width: float = 0.0, default_height: float = 0.0
) -> SVGDimensions:
    """Extract width and height from an SVG string.

    Checks viewBox first (more reliable for Vega-Lite output), then falls back
    to width/height attributes.

    Args:
        svg: SVG string to parse
        default_width: Default width if not found
        default_height: Default height if not found

    Returns:
        SVGDimensions with extracted or default values
    """
    # Prefer viewBox dimensions (more reliable for Vega-Lite output)
    viewbox_match = _SVG_VIEWBOX_RE.search(svg)
    if viewbox_match:
        return SVGDimensions(
            width=float(viewbox_match.group(1)),
            height=float(viewbox_match.group(2)),
        )

    # Fall back to explicit width/height attributes
    width = default_width
    height = default_height

    width_match = _SVG_WIDTH_RE.search(svg)
    if width_match:
        width = float(width_match.group(1))

    height_match = _SVG_HEIGHT_RE.search(svg)
    if height_match:
        height = float(height_match.group(1))

    return SVGDimensions(width=width, height=height)


def create_grid_pattern() -> str:
    """Create an SVG pattern definition for a 100x100 grid.

    Returns:
        SVG <pattern> element for 100x100 grid overlay.
    """
    return render_template("svg/grid_pattern.svg")


def generate_svg_styles(
    emoji_mode: Literal["monochrome", "system-default", "disabled"],
    font_face_css: str,
) -> str:
    """Generate CSS styles for SVG charts and interactions.

    Args:
        emoji_mode: One of monochrome / system-default / disabled. Gates the
            font-variant-emoji rule that holds a monochrome face to text
            presentation.
        font_face_css: The board's @font-face declarations, already rendered —
            see render/font_selection.py. Passed in rather than generated here
            because which faces a board needs is only known once its text is
            painted.

    Returns:
        SVG <style> element with CSS rules
    """
    return render_template(
        "svg/styles.css", emoji_mode=emoji_mode, font_face_css=font_face_css
    )


def render_title(
    title: str,
    width: float,
    text_align: Literal["left", "center", "right"] = "left",
    *,
    level: int = 1,
    resolved_style: "ResolvedStyle",
) -> str:
    """Render board title using mdsvg for proper text handling.

    Font size depends only on ``level`` (semantic heading). ``width`` is the
    layout/wrap width passed to mdsvg so the title text wraps inside the
    column — it does not influence the title's font size.

    The title always renders in ``style.title.font.family`` — a board title
    is always drawn as a heading (``board_title_markdown`` emits ``# {title}``
    unconditionally), so there is no separate "prose" vs. "chart-title
    narrow-card" family for it to fall back to.

    Args:
        title: Title text to render
        width: Layout width in pixels (passed to mdsvg for text wrap).
        text_align: Text alignment ("left", "center", "right")

    Returns:
        SVG string for the title
    """
    from dbt_charts.core.compile.resolve.style.typography import board_title_markdown
    from dbt_charts.core.render.font_selection import record_painted_faces
    from dbt_charts.core.render.sizing import title_renderer
    from mdsvg import parse as parse_markdown

    markdown_title, _, _ = board_title_markdown(
        title, level=level, resolved_style=resolved_style
    )
    # Driven through SVGRenderer rather than mdsvg's one-shot `render()` so the
    # faces this title reached can be read back off the renderer: a title carries
    # markdown, so it can be the only thing on a board that paints italic.
    renderer, font_path_family = title_renderer(resolved_style, level, text_align)
    svg = renderer.render(parse_markdown(markdown_title), width=width, padding=0.0)
    record_painted_faces(font_path_family, renderer.used_faces)
    return svg
