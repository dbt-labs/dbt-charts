"""SVG renderer for parsed Markdown AST."""

from __future__ import annotations

import hashlib
import html as _html
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

import nh3

# Precise text measurement
from .fonts import (
    FontFace,
    FontFaces,
    FontMeasurer,
    WrapPiece,
    _cached_measurer,
    get_default_measurer,
    get_system_mono_font,
    split_token_precise,
    wrap_measured_pieces,
)
from .highlight import CodeSegment, highlight_code
from .images import ImageSize, ImageUrlMapper, get_image_size
from .style import Style
from .types import (
    AnyBlock,
    Block,
    Blockquote,
    CodeBlock,
    Document,
    Heading,
    HorizontalRule,
    ImageBlock,
    ListItem,
    OrderedList,
    Paragraph,
    RawHtmlBlock,
    Span,
    SpanType,
    Table,
    TableCell,
    TableRow,
    UnorderedList,
)
from .utils import escape_svg_text, escape_xml, format_number

# --- Raw HTML sanitization ---
# _SAFE_LINK_SCHEMES enforces the same URL-scheme policy for both code paths:
# _safe_href (markdown-syntax links, below) and _sanitize_html (raw-HTML blocks).
# One constant, both paths — whoever extends this list updates both automatically.
_SAFE_LINK_SCHEMES = frozenset({"http", "https", "mailto"})

# Parser-based allowlist enforced by nh3 (Rust/ammonia). A regex over tag soup
# cannot enumerate every DOM-parsing quirk a browser honors — notably unquoted
# event handlers, javascript: hrefs on non-blocked tags, and SVG <use> vectors.
# All three are closed by the parser walk nh3 performs.
_HTML_ALLOWED_TAGS: set[str] = {
    # structural
    "div",
    "span",
    "section",
    "article",
    "aside",
    "header",
    "footer",
    "main",
    "nav",
    "details",
    "summary",
    # headings (authors may embed sub-headings inside raw HTML)
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    # inline
    "p",
    "a",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "s",
    "del",
    "ins",
    "mark",
    "small",
    "sub",
    "sup",
    "abbr",
    "cite",
    "code",
    "kbd",
    "samp",
    "var",
    "time",
    "q",
    "br",
    "wbr",
    # lists
    "ul",
    "ol",
    "li",
    "dl",
    "dt",
    "dd",
    # tables
    "table",
    "caption",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "th",
    "td",
    "colgroup",
    "col",
    # media — src validated via url_schemes
    "img",
    "figure",
    "figcaption",
    # preformatted
    "pre",
    "blockquote",
    "hr",
}

# Per-tag attribute allowlist. "*" is the nh3 wildcard for universal attributes.
# `style` is intentionally included: authoring formatted HTML without inline style
# would be unnecessarily restrictive. ammonia does not parse CSS, so style content
# passes through verbatim — authors can set arbitrary layout (e.g. position:fixed).
# That is consistent with "trusted-raw" tier semantics: the HTML is sanitized, not
# sandboxed. The `safe-subset` tier (future: html_policy) should drop `style` or
# proxy through a CSS allowlist.
_HTML_ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "*": {"class", "id", "style", "title", "lang", "dir"},
    # rel is handled by nh3's link_rel parameter, not here — ammonia panics if
    # rel appears in both the attributes dict and link_rel simultaneously.
    "a": {"href", "target"},
    "img": {"src", "alt", "width", "height", "loading"},
    "th": {"scope", "colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
    "col": {"span"},
    "colgroup": {"span"},
    "time": {"datetime"},
    "abbr": {"title"},
    "blockquote": {"cite"},
    "q": {"cite"},
}

# HTML5 void elements that are in _HTML_ALLOWED_TAGS and therefore may appear
# in nh3's output. When the allowlist gains a new void element, add it here.
_XHTML_VOID_ELEMENTS: frozenset[str] = frozenset({"br", "wbr", "hr", "img", "col"})


class _XhtmlSerializer(HTMLParser):
    """Re-serializes nh3's HTML5 output as well-formed XHTML for <foreignObject>.

    nh3 emits HTML5 which has two XML-incompatibilities this class corrects:
    - Void elements lack self-closing slashes (<br> -> <br/>).
    - Named HTML entities like &nbsp; are undefined in XML; convert_charrefs=True
      makes HTMLParser decode them to their Unicode code points, which are valid
      XML text and don't need re-encoding unless they are one of the five XML
      predefined entities (&amp; &lt; &gt; &quot; &apos;).

    Applied only to already-sanitized markup from nh3.clean().
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        parts = [f"<{tag}"]
        for name, value in attrs:
            # HTMLParser types value as str | None; nh3 always emits quoted values
            # so None is unreachable here, but the branch keeps mypy happy.
            raw_value = name if value is None else value
            parts.append(f' {name}="{escape_xml(raw_value)}"')
        parts.append("/>" if tag in _XHTML_VOID_ELEMENTS else ">")
        self._out.append("".join(parts))

    def handle_endtag(self, tag: str) -> None:
        if tag not in _XHTML_VOID_ELEMENTS:
            self._out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        # html.escape encodes & < > so the text is valid XML; > is optional
        # but included because ET.fromstring is strict.
        self._out.append(_html.escape(data, quote=False))

    def result(self) -> str:
        return "".join(self._out)


def _sanitize_html(raw: str) -> str:
    """Strip disallowed tags, attributes, and URL schemes from HTML.

    Uses nh3 (Rust/ammonia) to walk the parsed DOM tree rather than applying
    regexes to tag soup — the only approach that reliably blocks javascript:
    hrefs, unquoted event handlers, and SVG vectors like <use href="javascript:">.

    Re-serializes nh3's HTML5 output as XHTML so it is valid XML inside a
    <foreignObject>: self-closing void elements, XML-defined entity references only.
    """
    sanitized = nh3.clean(
        raw,
        tags=_HTML_ALLOWED_TAGS,
        attributes=_HTML_ALLOWED_ATTRIBUTES,
        url_schemes=set(_SAFE_LINK_SCHEMES),
        # Force noopener noreferrer on every <a> nh3 emits — the default.
        # Spelled out explicitly so it can't be silently changed by a nh3 upgrade.
        link_rel="noopener noreferrer",
    )
    serializer = _XhtmlSerializer()
    serializer.feed(sanitized)
    serializer.close()
    return serializer.result()


# Vertical chip geometry constants.
# WHY: SVG text positioning uses baseline coordinates; glyphs extend up to
# cap height above and down to descender depth below the baseline. Using the
# full em-square (font_size) for the chip top wastes ~28% above cap height.
# These ratios center the chip on the glyph's visual body (cap/x-height region)
# at typical proportions for common serif and sans-serif fonts (measured against
# Georgia, Source Serif 4, Inter, and system sans).
_CHIP_CAP_HEIGHT_RATIO = 0.72  # cap height / em — top of glyph body above baseline
_CHIP_DESCENDER_RATIO = 0.20  # descender depth / em — glyph body below baseline


def _safe_href(url: str) -> str:
    """Return url if its scheme is in the allowlist, otherwise '#'.

    SVG ``<a>`` elements are clickable when the SVG is inlined into an HTML
    page.  ``javascript:``, ``data:``, ``vbscript:``, and ``blob:`` hrefs are
    active XSS vectors; relative URLs (/, #, ., ?) are safe.
    """
    if url.startswith(("/", "#", ".", "?")) or ":" not in url:
        return url
    scheme = url.split(":", 1)[0].lower()
    return url if scheme in _SAFE_LINK_SCHEMES else "#"


@dataclass
class RenderResult:
    """Result of rendering markdown content.

    Contains the rendered SVG content (without wrapper), along with dimensions.
    This allows callers to compose multiple mdsvg outputs into a larger SVG
    without needing to regex-strip wrappers.

    Attributes:
        elements: SVG elements without wrapper or style block.
        style_block: The <style> block with CSS classes for the rendered content.
        width: Width of the rendered content in pixels.
        height: Height of the rendered content in pixels.
        max_content_width: The widest laid-out line actually emitted (paragraphs,
            headings, list items, table cells, code block lines), in the same
            coordinate space as ``width``. A value greater than ``width`` means
            something painted outside its box — an unwrappable token, or an
            engine bug. ``0.0`` for empty content.

    Example:
        >>> from mdsvg import render_content
        >>> result = render_content("# Hello", width=400)
        >>> result.elements     # Just SVG elements (rects, text, etc.)
        >>> result.style_block  # The <style>...</style> CSS block
        >>> result.content      # Combined style_block + elements (backwards compatible)
        >>> result.width        # 400.0
        >>> result.height       # Actual rendered height
        >>> result.to_svg()     # Full SVG with wrapper

        # Compose multiple sections with single style block:
        >>> left = render_content("# Left", width=350)
        >>> right = render_content("# Right", width=350)
        >>> combined = f'''
        ... <svg xmlns="http://www.w3.org/2000/svg" width="750" height="{max(left.height, right.height)}">
        ...   {left.style_block}
        ...   <g transform="translate(0, 0)">{left.elements}</g>
        ...   <g transform="translate(400, 0)">{right.elements}</g>
        ... </svg>
        ... '''
    """

    elements: str
    style_block: str
    width: float
    height: float
    max_content_width: float = 0.0

    @property
    def content(self) -> str:
        """Combined style block and elements for backwards compatibility.

        Returns:
            String containing the style block followed by SVG elements.
        """
        return self.style_block + "\n" + self.elements

    def to_svg(self) -> str:
        """Wrap content in a complete SVG element.

        Returns:
            Complete SVG string with xmlns, width, height, and viewBox attributes.

        Example:
            >>> result = render_content("# Hello", width=400)
            >>> svg = result.to_svg()
            >>> with open("output.svg", "w") as f:
            ...     f.write(svg)
        """
        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{format_number(self.width)}" height="{format_number(self.height)}" '
            f'viewBox="0 0 {format_number(self.width)} {format_number(self.height)}">',
            self.content,
            "</svg>",
        ]
        return "\n".join(svg_parts)


@dataclass
class RenderContext:
    """Context passed through rendering for tracking state."""

    x: float
    y: float
    width: float
    style: Style
    indent: float = 0.0

    def with_offset(self, dx: float = 0, dy: float = 0) -> RenderContext:
        """Create a new context with offset position."""
        return RenderContext(
            x=self.x + dx,
            y=self.y + dy,
            width=self.width,
            style=self.style,
            indent=self.indent,
        )

    def with_indent(self, additional_indent: float) -> RenderContext:
        """Create a new context with additional indentation."""
        return RenderContext(
            x=self.x + additional_indent,
            y=self.y,
            width=self.width - additional_indent,
            style=self.style,
            indent=self.indent + additional_indent,
        )


@dataclass(frozen=True)
class BlockMetrics:
    """Line-level measurement of one markdown block at a given width.

    Enough for a caller to decide where to break a document across boxes of
    limited height -- columns, pages, slides -- without mdsvg knowing what a
    box is. ``line_advances`` is one height per line, from the same line
    layout render draws -- so a caller summing a slice of it gets exactly
    what :meth:`render_block_window` draws for that slice, even when an
    inline image makes one line taller than the rest. An unsplittable block
    reports a single-element tuple, its whole height, so a packer can treat
    every block the same way.
    """

    line_advances: tuple[float, ...]
    space_before: float
    splittable: bool
    keep_with_next: bool
    leading_margin: float = 0.0
    """Whitespace the block draws above its own content (headings).

    Reported separately so a caller placing this block at the top of a box can
    collapse it -- a heading opening a column should sit at the top, not below
    the space that would separate it from preceding text.
    """


@dataclass(frozen=True)
class TableRowLayout:
    """Measured layout for one rendered table row: each cell's own lines."""

    cell_lines: tuple[tuple["_Line", ...], ...]
    row_height: float


@dataclass(frozen=True)
class Size:
    """Rendered width and height."""

    width: float
    height: float


def _apply_case(text: str, case: str) -> str:
    """Apply a case transform to text. 'upper' → upper(), 'lower' → lower(), else identity."""
    if case == "upper":
        return text.upper()
    if case == "lower":
        return text.lower()
    return text


def _round_half(size: float) -> float:
    """Snap a font size to the nearest half-pixel."""
    return round(size * 2) / 2


def _wrap_colored_line(
    segments: List[CodeSegment], max_chars: int
) -> List[List[CodeSegment]]:
    """Wrap one code line's (text, color) segments into visual lines of at most
    `max_chars` characters, cutting at fixed character intervals (mirroring the
    plain-text `line[:max_chars]` slicing code blocks already use for wrapping).
    A token is split only when a cut boundary happens to land inside it.
    """
    visual_lines: List[List[CodeSegment]] = []
    current: List[CodeSegment] = []
    current_len = 0
    for text, color in segments:
        while text:
            remaining = max_chars - current_len
            if remaining <= 0:
                visual_lines.append(current)
                current = []
                current_len = 0
                remaining = max_chars
            chunk, text = text[:remaining], text[remaining:]
            current.append((chunk, color))
            current_len += len(chunk)
    visual_lines.append(current)
    return visual_lines


def _truncate_segments(segments: List[CodeSegment], max_len: int) -> List[CodeSegment]:
    """Truncate a line's (text, color) segments to at most `max_len` characters,
    dropping trailing segments/characters beyond the limit."""
    out: List[CodeSegment] = []
    remaining = max_len
    for text, color in segments:
        if remaining <= 0:
            break
        chunk = text[:remaining]
        if chunk:
            out.append((chunk, color))
        remaining -= len(chunk)
    return out


_CSS_WEIGHT_KEYWORDS = {"normal": 400.0, "bold": 700.0}


def _numeric_weight(value: "str | int | float | None") -> float | None:
    """A CSS ``font-weight`` as a number, or None when it names no specific weight.

    ``None`` means the caller did not ask for a weight. Keywords resolve to their
    CSS numeric equivalents; anything unrecognized returns None so measurement
    keeps the font face it already had rather than inventing a weight.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    keyword = _CSS_WEIGHT_KEYWORDS.get(value.strip().lower())
    if keyword is not None:
        return keyword
    try:
        return float(value)
    except ValueError:
        return None


class SVGRenderer:
    """
    Renderer that converts Markdown AST to SVG.

    Uses fonttools-backed precise text measurement.

    Example:
        >>> renderer = SVGRenderer(style=Style())
        >>> blocks = parse("# Hello\\n\\nWorld")
        >>> svg = renderer.render(blocks, width=400)
    """

    def __init__(
        self,
        style: Optional[Style] = None,
        font_path: Optional[str] = None,
        mono_font_path: Optional[str] = None,
        fonts: Optional[FontFaces] = None,
        # Image options
        fetch_image_sizes: bool = True,
        image_base_path: Optional[str] = None,
        image_url_mapper: Optional[ImageUrlMapper] = None,
        image_timeout: float = 10.0,
        # Raw HTML passthrough
        allow_raw_html: bool = False,
    ) -> None:
        """
        Initialize the renderer.

        Args:
            style: Style configuration. Uses default style if None.
            font_path: Path to a TTF/OTF font file for precise measurement.
                      If None, uses system default font. Mutually exclusive
                      with `fonts`.
            mono_font_path: Path to a monospace TTF/OTF font file for measuring
                      inline code. If None, uses the detected system mono font.
                      Mutually exclusive with `fonts`.
            fonts: The full set of font files to measure bold/italic/mono runs
                      against their real font files instead of a ratio-scaled
                      guess from the regular font file. Mutually exclusive with
                      `font_path`/`mono_font_path`.
            fetch_image_sizes: If True (default), fetch image dimensions from
                      local files or remote URLs. Required for accurate layout.
            image_base_path: Base directory for resolving relative image paths.
                      Used when fetching local image dimensions.
            image_url_mapper: Optional function to transform image URLs before
                      embedding in SVG. Useful for mapping local paths to CDN URLs.
                      Example: create_prefix_mapper({"/assets/": "https://cdn.example.com/"})
            image_timeout: Timeout in seconds for fetching remote images (default 10).

        Raises:
            ValueError: If both `fonts` and `font_path`/`mono_font_path` are given.
        """
        if fonts is not None and (font_path is not None or mono_font_path is not None):
            raise ValueError(
                "SVGRenderer accepts either 'fonts' or 'font_path'/'mono_font_path', "
                "not both."
            )

        self.style = style or Style()
        self._class_prefix = self._compute_class_prefix()
        self._allow_raw_html = allow_raw_html
        self._measurer: FontMeasurer
        self._mono_char_width: Optional[float] = None
        self._mono_font_path = mono_font_path
        self._mono_face: FontFace | None = None
        self._bold_measurer: FontMeasurer | None = None
        self._italic_measurer: FontMeasurer | None = None
        self._bold_italic_measurer: FontMeasurer | None = None
        self._heading_measurer: FontMeasurer | None = None
        self._regular_face: FontFace | None = None
        self._italic_face: FontFace | None = None
        self._heading_face: FontFace | None = None
        self._max_content_width: float = 0.0
        # Recorded where a font face is chosen to measure with, not where one is
        # requested: a bold or italic run whose family ships no such font face is
        # ratio-estimated from the regular one, and it is the regular file the
        # browser will paint it from too. Read via the `used_faces` property.
        self._used_faces: set[str] = set()

        # Image handling
        self._fetch_image_sizes = fetch_image_sizes
        self._image_base_path = image_base_path
        self._image_url_mapper = image_url_mapper
        self._image_timeout = image_timeout
        self._image_size_cache: Dict[str, Optional[ImageSize]] = {}

        if fonts is not None:
            self._measurer = self._measurer_for_face(fonts.regular)
            self._regular_face = fonts.regular
            self._italic_face = fonts.italic
            if fonts.bold is not None:
                self._bold_measurer = self._measurer_for_face(fonts.bold)
            if fonts.italic is not None:
                self._italic_measurer = self._measurer_for_face(fonts.italic)
            if fonts.bold_italic is not None:
                self._bold_italic_measurer = self._measurer_for_face(fonts.bold_italic)
            if fonts.heading is not None:
                self._heading_measurer = self._measurer_for_face(fonts.heading)
                self._heading_face = fonts.heading
            self._mono_face = fonts.mono
        elif font_path:
            self._measurer = _cached_measurer(font_path)
            if not self._measurer.is_available:
                raise RuntimeError(
                    "Precise text measurement is required, but no FontMeasurer is available"
                )
        else:
            default = get_default_measurer()
            if default is None or not default.is_available:
                raise RuntimeError(
                    "Precise text measurement is required, but no FontMeasurer is available"
                )
            self._measurer = default

    def _measurer_for_face(self, font_face: FontFace) -> FontMeasurer:
        """Resolve a `FontFace` to a cached, available `FontMeasurer` or raise."""
        measurer = _cached_measurer(
            font_face.path, font_face.font_number, font_face.weight
        )
        if not measurer.is_available:
            raise RuntimeError(
                f"Precise text measurement is required, but font {font_face.path!r} "
                "is unavailable"
            )
        return measurer

    def _measurer_at_weight(
        self, font_face: FontFace | None, weight: float
    ) -> FontMeasurer | None:
        """A measurer for *font_face* re-weighted to *weight*, or None if not possible.

        Returns None when no font_face is known or the file has no weight axis to move —
        the caller then measures with whatever font_face it already had, which is the
        best available answer rather than a guess.
        """
        if font_face is None:
            return None
        base = _cached_measurer(font_face.path, font_face.font_number, font_face.weight)
        if not base.supports_weight:
            return None
        measurer = _cached_measurer(font_face.path, font_face.font_number, weight)
        return measurer if measurer.is_available else None

    def _ensure_mono_char_width(self) -> float:
        """Load precise monospace measurement on first actual code use."""
        if self._mono_char_width is not None:
            return self._mono_char_width

        if self._mono_face is not None:
            mono_measurer = self._measurer_for_face(self._mono_face)
            self._mono_char_width = mono_measurer.measure("M", 1.0)
            return self._mono_char_width

        mono_font_path = self._mono_font_path or get_system_mono_font()
        if not mono_font_path:
            raise RuntimeError(
                "Precise monospace measurement is required, but no monospace FontMeasurer is available"
            )

        mono_measurer = _cached_measurer(mono_font_path)
        if not mono_measurer.is_available:
            raise RuntimeError(
                "Precise monospace measurement is required, but no monospace FontMeasurer is available"
            )

        self._mono_char_width = mono_measurer.measure("M", 1.0)
        return self._mono_char_width

    @property
    def used_faces(self) -> frozenset[str]:
        """Which of the supplied ``FontFaces`` this renderer has reached so far.

        Any of ``regular``, ``bold``, ``italic``, ``bold_italic``, ``mono``,
        ``heading``; empty before anything is rendered. Accumulates across calls,
        so a caller rendering several columns or blocks through one renderer reads
        the union.

        A renderer measuring from a bare ``font_path`` still reports ``regular`` (and
        ``mono`` for code), since that is the font face it measured against — it simply
        never reports ``italic`` or ``bold``, having no such font face to reach for.

        For callers that ship the fonts alongside the SVG: which files a render
        needs cannot be known beforehand, because italic is reached through markdown
        emphasis inside the text rather than through anything in the style.
        """
        return frozenset(self._used_faces)

    def _measure_text(
        self,
        text: str,
        font_size: float,
        is_bold: bool = False,
        is_italic: bool = False,
        is_mono: bool = False,
        font_weight: str | int | None = None,
        is_heading: bool = False,
    ) -> float:
        """Measure text width against the font face that will actually be painted.

        When `SVGRenderer(fonts=...)` supplied a matching font face, measures
        against it directly. `(bold, italic)` prefers `bold_italic`, falling
        back to `italic`, then `bold`. A single bold/italic font face falls back
        to that font face alone. Only when no matching font face was supplied does
        this fall back to ratio-scaling the regular measurement by
        `Style.bold_char_width_ratio` / `italic_char_width_ratio` — a
        last-resort estimate, documented on those fields.

        `font_weight` is the weight the block will be painted at — headings carry
        `Style.heading_font_weight`, which is not the body weight. Measuring a
        heading at the body weight makes it narrower than it will be drawn, which
        is the same mistake as measuring the wrong file. It applies to upright and
        italic runs; a bold run inside a heading already paints at
        `bold_font_weight`, so it keeps the bold font face.

        `is_heading` prefers `fonts.heading` (re-weighted to `font_weight` when a
        variable font is supplied) over `fonts.regular` — the same
        "measure what gets painted" reasoning as `font_weight` above, for
        `Style.heading_font_family` instead of the weight axis. Only the plain
        (non-bold, non-italic) run of a heading gets the heading face: `fonts`
        carries no heading-bold/heading-italic combination to measure an
        emphasized span inside a heading against, so that span falls through to
        the body bold/italic face instead — a real gap, not a claimed match, kept
        narrow because an emphasized run inside a heading is rare.
        """
        if is_mono:
            # Inline code paints at its own size, derived from the size of the
            # text it sits in, so that is the size it must be measured at.
            mono_size = self._inline_code_size(font_size)
            self._used_faces.add("mono")
            return len(text) * self._ensure_mono_char_width() * mono_size

        weight = _numeric_weight(font_weight)

        if (
            is_heading
            and not is_bold
            and not is_italic
            and self._heading_face is not None
        ):
            heading_measurer = None
            if weight is not None:
                heading_measurer = self._measurer_at_weight(self._heading_face, weight)
            if heading_measurer is None:
                # No variable weight axis to re-instance (or none requested) —
                # the un-reweighted heading face is still the right file,
                # never the body's.
                heading_measurer = self._heading_measurer
            if heading_measurer is not None:
                self._used_faces.add("heading")
                return heading_measurer.measure(text, font_size)

        if weight is not None and not is_bold:
            font_face = self._italic_face if is_italic else self._regular_face
            weighted = self._measurer_at_weight(font_face, weight)
            if weighted is not None:
                self._used_faces.add("italic" if is_italic else "regular")
                return weighted.measure(text, font_size)

        if is_bold and is_italic:
            measurer = (
                self._bold_italic_measurer
                or self._italic_measurer
                or self._bold_measurer
            )
            if measurer is not None:
                self._used_faces.add(
                    "bold_italic"
                    if measurer is self._bold_italic_measurer
                    else "italic"
                    if measurer is self._italic_measurer
                    else "bold"
                )
                return measurer.measure(text, font_size)
            self._used_faces.add("regular")
            width = self._measurer.measure(text, font_size)
            bold_ratio = self.style.bold_char_width_ratio / self.style.char_width_ratio
            italic_ratio = (
                self.style.italic_char_width_ratio / self.style.char_width_ratio
            )
            return width * bold_ratio * italic_ratio

        if is_bold:
            if self._bold_measurer is not None:
                self._used_faces.add("bold")
                return self._bold_measurer.measure(text, font_size)
            self._used_faces.add("regular")
            width = self._measurer.measure(text, font_size)
            return width * (
                self.style.bold_char_width_ratio / self.style.char_width_ratio
            )

        if is_italic:
            if self._italic_measurer is not None:
                self._used_faces.add("italic")
                return self._italic_measurer.measure(text, font_size)
            self._used_faces.add("regular")
            width = self._measurer.measure(text, font_size)
            return width * (
                self.style.italic_char_width_ratio / self.style.char_width_ratio
            )

        self._used_faces.add("regular")
        return self._measurer.measure(text, font_size)

    def _code_content_width(self, lines: Sequence[str], char_width: float) -> float:
        """Widest line's raw text width (no padding) — the real painted extent,
        which 'show' overflow mode lets exceed the box."""
        return max((len(line) for line in lines), default=0) * char_width

    def _code_block_width(
        self,
        content_width: float,
        *,
        padding: float,
        max_width: float,
    ) -> float:
        return min(max_width, content_width + (padding * 2))

    def _code_block_padding(self) -> tuple[float, float]:
        padding_x = self.style.code_block_padding
        # WHY: code blocks need enough side padding for the border but less
        # vertical padding so tight prose rhythm does not clip surrounding lines.
        padding_y = padding_x * (2 / 3)
        return padding_x, padding_y

    def _logical_code_lines(self, code: CodeBlock) -> List[List[CodeSegment]]:
        """Split a code block into one (text, color) segment list per source line.

        Segments carry an empty color string ("") for plain (unhighlighted)
        rendering — the render step reads that as "use the block's default
        fill". Highlighting kicks in only when `style.code_highlight` is set,
        a fence language is present, and Pygments resolves both the lexer and
        the `code_theme`. A bad `code_theme` propagates uncaught (config
        error); an unresolvable language falls back to plain segments.
        """
        if self.style.code_highlight and code.language:
            highlighted = highlight_code(
                code.code, code.language, self.style.code_theme
            )
            if highlighted is not None:
                return highlighted
        return [[(line, "")] if line else [] for line in code.code.split("\n")]

    def _render_code_line_element(
        self,
        segments: List[CodeSegment],
        x: float,
        y: float,
        code_attrs: str,
        default_fill: str,
        clip_attr: str = "",
    ) -> Optional[str]:
        """Render one code-block source line as a single <text>.

        When no segment carries a highlight color (the default, unhighlighted
        path — every segment's color is ""), emits the plain `fill=` form
        exactly as before this feature, so non-highlighted dashboards see no
        SVG diff. Only when a segment carries a real Pygments color does the
        line switch to one <tspan fill=...> per segment. Returns None for a
        blank line — the caller still advances y but emits no element.
        """
        text = "".join(t for t, _ in segments)
        if not text:
            return None

        if not any(color for _, color in segments):
            escaped = escape_xml(_apply_case(text, self.style.code_font_case))
            return (
                f'  <text x="{format_number(x)}" '
                f'y="{format_number(y)}" '
                f'{code_attrs} xml:space="preserve" '
                f'fill="{escape_xml(default_fill)}"{clip_attr}>{escaped}</text>'
            )

        tspans = []
        for seg_text, color in segments:
            if not seg_text:
                continue
            escaped = escape_xml(_apply_case(seg_text, self.style.code_font_case))
            tspans.append(
                f'<tspan fill="{escape_xml(color or default_fill)}">{escaped}</tspan>'
            )
        return (
            f'  <text x="{format_number(x)}" y="{format_number(y)}" '
            f'{code_attrs} xml:space="preserve"{clip_attr}>{"".join(tspans)}</text>'
        )

    def _render_blocks_to_elements(
        self,
        blocks: Document,
        width: float,
        padding: float,
    ) -> Tuple[List[str], float]:
        """
        Render blocks to SVG elements and return total height.

        This is the core rendering logic shared by render() and render_content().

        Args:
            blocks: Document AST to render.
            width: Width of the SVG in pixels.
            padding: Padding inside the SVG.

        Returns:
            Tuple of (svg_elements, total_height).
        """
        self._max_content_width = 0.0
        content_width = width - (padding * 2)

        ctx = RenderContext(
            x=padding,
            y=padding,
            width=content_width,
            style=self.style,
        )

        svg_elements: List[str] = []
        current_y = padding
        prev_block: Block | None = None

        for block in blocks:
            if prev_block is not None:
                current_y += self._inter_block_gap(prev_block, block)
            # The block that opens the box draws no leading margin: that
            # whitespace separates a heading from the text above it, and here
            # there is none. Same rule the column packer applies to a block
            # opening a column, via BlockMetrics.leading_margin.
            lift = self._heading_margins(block)[0] if prev_block is None else 0.0
            elements, height = self._render_block(
                block, ctx.with_offset(dy=current_y - ctx.y - lift)
            )
            svg_elements.extend(elements)
            current_y += height - lift
            prev_block = block

        total_height = current_y + padding
        return svg_elements, total_height

    def measure_blocks(
        self,
        blocks: Document,
        width: float = 400,
    ) -> tuple[BlockMetrics, ...]:
        """Measure each block's lines at ``width``.

        The measure half of a measure-then-place pair, mirroring the table
        path's ``TableRowLayout``. A caller that needs to fill fixed-height
        boxes measures once here, decides its own breaks, then draws runs of
        lines with :meth:`render_block_window`.
        """
        from .types import Paragraph

        ctx = RenderContext(x=0.0, y=0.0, width=width, style=self.style)
        metrics: list[BlockMetrics] = []
        prev: Block | None = None

        for block in blocks:
            gap = self._inter_block_gap(prev, block) if prev is not None else 0.0
            if isinstance(block, Paragraph):
                # "normal" is what _render_text_block defaults to for paragraphs,
                # and the pair's contract is that a caller may place a slice of
                # what it measured. Wrapping the two halves at different weights
                # makes the slice short and drops the tail of the paragraph.
                lines = self._text_lines(
                    block.spans,
                    width,
                    self.style.base_font_size,
                    "normal",
                    self.style.line_height,
                )
                metrics.append(
                    BlockMetrics(
                        # An empty paragraph still occupies one blank line.
                        line_advances=tuple(line.height for line in lines)
                        or (self.style.base_font_size * self.style.line_height,),
                        space_before=gap,
                        splittable=True,
                        keep_with_next=False,
                        leading_margin=0.0,
                    )
                )
            else:
                _, height = self._render_block(block, ctx)
                metrics.append(
                    BlockMetrics(
                        line_advances=(height,),
                        space_before=gap,
                        splittable=False,
                        keep_with_next=isinstance(block, Heading),
                        leading_margin=self._heading_margins(block)[0],
                    )
                )
            prev = block

        return tuple(metrics)

    def min_content_width(self, blocks: Document) -> float:
        """Narrowest width at which no word is split mid-glyph.

        The widest unbreakable token of any block, in the font it paints in.
        A table draws equal columns, so its floor is every column at the widest
        cell word plus padding. Blocks that wrap nothing (rules, images) have no
        floor.
        """
        # Whole pixels, rounded up: a caller subtracts padding from a sum of
        # widths, and a float a hair under the token's width splits it.
        return float(
            math.ceil(
                max((self._block_min_width(block) for block in blocks), default=0.0)
            )
        )

    def _widest_token(
        self,
        spans: Sequence[Span],
        font_size: float,
        *,
        is_header: bool = False,
        font_weight: str | int | None = None,
        is_heading: bool = False,
    ) -> float:
        runs = (
            self._build_text_runs(spans, font_size)
            if not is_header
            else self._table_cell_runs(TableCell(spans=tuple(spans)), True, 0.0)
        )
        return max(
            (
                self._measure_text(
                    token,
                    font_size,
                    is_bold=run.is_bold,
                    is_italic=run.is_italic,
                    is_mono=run.is_code,
                    font_weight=font_weight,
                    is_heading=is_heading,
                )
                for run in runs
                for token in run.text.split()
            ),
            default=0.0,
        )

    def _block_min_width(self, block: Block) -> float:
        size = self.style.base_font_size
        if isinstance(block, Paragraph):
            return self._widest_token(block.spans, size)
        if isinstance(block, Heading):
            return self._widest_token(
                block.spans,
                self.style.get_heading_size(block.level),
                font_weight=self.style.heading_font_weight,
                is_heading=True,
            )
        if isinstance(block, (UnorderedList, OrderedList)):
            return max(
                (
                    self.style.list_indent
                    + max(
                        [
                            self._widest_token(item.spans, size),
                            *(self._block_min_width(child) for child in item.children),
                        ]
                    )
                    for item in block.items
                ),
                default=0.0,
            )
        if isinstance(block, Blockquote):
            return self.style.blockquote_padding + max(
                (self._block_min_width(child) for child in block.blocks), default=0.0
            )
        if isinstance(block, Table):
            cells = [
                (cell, is_header)
                for row, is_header in (
                    [(block.header, True)] + [(row, False) for row in block.rows]
                )
                for cell in row.cells
            ]
            widest = max(
                (
                    self._widest_token(cell.spans, size, is_header=is_header)
                    for cell, is_header in cells
                ),
                default=0.0,
            )
            return len(block.header.cells) * (
                widest + 2 * self.style.table_cell_padding
            )
        return 0.0

    def render_block_window(
        self,
        block: Block,
        width: float = 400,
        start: int = 0,
        count: int | None = None,
    ) -> RenderResult:
        """Render ``count`` lines of ``block``, beginning at line ``start``.

        The placement half of the pair. The window is drawn at the top of its
        own coordinate box, so a continuation does not inherit the offset of
        the lines preceding it. An unsplittable block ignores the window and
        renders whole -- asking for a slice of something indivisible is a
        caller error the caller can already avoid by reading
        :attr:`BlockMetrics.splittable`.
        """
        from .types import Paragraph

        ctx = RenderContext(x=0.0, y=0.0, width=width, style=self.style)
        if isinstance(block, Paragraph):
            elements, height = self._render_text_block(
                block.spans,
                ctx,
                font_size=self.style.base_font_size,
                css_class=self._scoped_class("text"),
                line_window=(start, count),
            )
        else:
            elements, height = self._render_block(block, ctx)

        return RenderResult(
            elements="\n".join(elements),
            style_block=self._get_style_block(),
            width=width,
            height=height,
        )

    def render(
        self,
        blocks: Document,
        width: float = 400,
        padding: float = 0,
    ) -> str:
        """
        Render blocks to an SVG string.

        Args:
            blocks: Document AST to render.
            width: Width of the SVG in pixels.
            padding: Padding inside the SVG.

        Returns:
            SVG string.
        """
        svg_elements, total_height = self._render_blocks_to_elements(
            blocks, width, padding
        )
        svg = self._build_svg(svg_elements, width, total_height)
        return svg

    def render_content(
        self,
        blocks: Document,
        width: float = 400,
        padding: float = 0,
    ) -> RenderResult:
        """
        Render blocks and return structured result with content and dimensions.

        Unlike render(), this returns the SVG content without the <svg> wrapper,
        along with the actual dimensions. This is useful when composing multiple
        mdsvg outputs into a larger SVG.

        Args:
            blocks: Document AST to render.
            width: Width of the SVG in pixels.
            padding: Padding inside the SVG.

        Returns:
            RenderResult with content (SVG elements without wrapper),
            width, and height.

        Example:
            >>> renderer = SVGRenderer()
            >>> blocks = parse("# Hello")
            >>> result = renderer.render_content(blocks, width=400)
            >>> result.content  # SVG elements without wrapper
            >>> result.width    # 400.0
            >>> result.height   # Actual rendered height
            >>> result.to_svg() # Full SVG with wrapper
        """
        svg_elements, total_height = self._render_blocks_to_elements(
            blocks, width, padding
        )

        return RenderResult(
            elements="\n".join(svg_elements),
            style_block=self._get_style_block(),
            width=width,
            height=total_height,
            max_content_width=self._max_content_width,
        )

    def measure(
        self,
        blocks: Document,
        width: float = 400,
        padding: float = 0,
    ) -> Size:
        """
        Measure the size needed to render blocks.

        Args:
            blocks: Document AST to measure.
            width: Width constraint.
            padding: Padding inside the SVG.

        Returns:
            Size with width and height.
        """
        # Measured by rendering: a second loop over the same blocks is a second
        # copy of the placement rules, free to disagree with the one that draws.
        _, height = self._render_blocks_to_elements(blocks, width, padding)
        return Size(width=width, height=height)

    def max_content_width(self, blocks: Sequence[Block]) -> float:
        """Narrowest width at which ``blocks`` lay out with no line wrapped.

        CSS calls this max-content: every paragraph, heading, list item and
        table cell on a single line, plus the indents and padding the renderer
        puts around them. Runs are built with no available width, so an inline
        image keeps its natural size rather than shrinking to fit. A block
        image asks for its set width or ``style.image_width``; with neither it
        fills the container, so like rules and raw HTML it asks for none.
        Rounded up to 0.01px: a width that round-trips through layout
        arithmetic a hair short would wrap its last character.
        """
        return math.ceil(self._blocks_max_content(blocks) * 100) / 100

    def _blocks_max_content(self, blocks: Sequence[Block]) -> float:
        return max((self._block_max_content(block) for block in blocks), default=0.0)

    def _one_line_width(
        self,
        runs: List[TextRun],
        font_size: float,
        font_weight: str | int | None = None,
        is_heading: bool = False,
    ) -> float:
        """Width of ``runs`` on one line: the pieces wrapping would lay out."""
        pieces = self._run_pieces(runs, math.inf, font_size, font_weight, is_heading)
        return sum(piece.width for piece in pieces) + sum(
            piece.separator_width for piece in pieces[1:]
        )

    def _table_max_content(self, table: Table) -> float:
        def row_widths(row: TableRow, is_header: bool) -> list[float]:
            return [
                self._one_line_width(
                    self._table_cell_runs(cell, is_header, 0.0),
                    self.style.base_font_size,
                )
                for cell in row.cells
            ]

        # Columns are drawn equal, so the widest cell sets every column's width.
        cells = row_widths(table.header, True) + [
            width for row in table.rows for width in row_widths(row, False)
        ]
        column_width = max(cells) + 2 * self.style.table_cell_padding
        return column_width * len(table.header.cells)

    def _block_max_content(self, block: Block) -> float:
        style = self.style
        if isinstance(block, Paragraph):
            return self._one_line_width(
                self._build_text_runs(block.spans, 0.0),
                style.base_font_size,
                "normal",
            )
        if isinstance(block, Heading):
            size = style.get_heading_size(block.level)
            return self._one_line_width(
                self._build_text_runs(block.spans, 0.0),
                size,
                style.heading_font_weight,
                True,
            )
        if isinstance(block, (UnorderedList, OrderedList)):
            return style.list_indent + max(
                (
                    max(
                        self._one_line_width(
                            self._build_text_runs(item.spans, 0.0),
                            style.base_font_size,
                            "normal",
                        ),
                        self._blocks_max_content(item.children),
                    )
                    for item in block.items
                ),
                default=0.0,
            )
        if isinstance(block, Blockquote):
            return style.blockquote_padding + max(
                (
                    self._blockquote_paragraph_width(inner)
                    if isinstance(inner, Paragraph)
                    else self._block_max_content(inner)
                    for inner in block.blocks
                ),
                default=0.0,
            )
        if isinstance(block, Table):
            return self._table_max_content(block)
        if isinstance(block, CodeBlock):
            char_width = self._block_code_size() * self._ensure_mono_char_width()
            content = self._code_content_width(block.code.splitlines(), char_width)
            return content + 2 * self._code_block_padding()[0]
        if isinstance(block, ImageBlock):
            width = block.width if block.width is not None else style.image_width
            return width or 0.0
        return 0.0

    def _blockquote_paragraph_width(self, para: Paragraph) -> float:
        spans, size = self._blockquote_text(para)
        return self._one_line_width(self._build_text_runs(spans, 0.0), size, "normal")

    # Fixed order: also the order the rules are hashed and rendered in, so
    # changing it would churn every class prefix for no visual reason.
    _STYLE_RULE_NAMES: Tuple[str, ...] = (
        "text",
        "mono",
        "heading",
        "code",
        "link",
        "blockquote",
    )

    def _style_rule_bodies(self) -> Dict[str, str]:
        """CSS declarations for each `.md-*` rule, keyed by its bare name.

        Shared by `_get_style_block` (which renders them) and
        `_compute_class_prefix` (which hashes them), so the two can never
        drift out of sync.
        """
        text_weight = (
            f" font-weight: {escape_xml(str(self.style.font_weight))};"
            if self.style.font_weight is not None
            else ""
        )
        # code font family: prefer code_font_family, fall back to mono_font_family
        code_family = self.style.code_font_family or self.style.mono_font_family
        code_extra = ""
        if self.style.code_font_weight:
            code_extra += f" font-weight: {escape_xml(self.style.code_font_weight)};"
        if self.style.code_font_style:
            code_extra += f" font-style: {escape_xml(self.style.code_font_style)};"
        if self.style.code_font_size:
            code_extra += f" font-size: {format_number(self.style.code_font_size)}px;"
        if self.style.code_font_decoration:
            code_extra += (
                f" text-decoration: {escape_xml(self.style.code_font_decoration)};"
            )

        # heading font override (empty = inherits body font, mirrors blockquote below)
        heading_family = self.style.heading_font_family or self.style.font_family

        # blockquote font overrides
        bq_family = self.style.blockquote_font_family or self.style.font_family
        bq_extra = f" font-family: {escape_xml(bq_family)};"
        if self.style.blockquote_font_weight:
            bq_extra += (
                f" font-weight: {escape_xml(self.style.blockquote_font_weight)};"
            )
        if self.style.blockquote_font_style:
            bq_extra += f" font-style: {escape_xml(self.style.blockquote_font_style)};"
        if self.style.blockquote_font_size:
            bq_extra += (
                f" font-size: {format_number(self.style.blockquote_font_size)}px;"
            )
        if self.style.blockquote_font_decoration:
            bq_extra += (
                f" text-decoration: "
                f"{escape_xml(self.style.blockquote_font_decoration)};"
            )

        return {
            "text": (
                f"font-family: {escape_xml(self.style.font_family)}; "
                f"fill: {escape_xml(self.style.text_color)};" + text_weight
            ),
            "mono": f"font-family: {escape_xml(self.style.mono_font_family)};",
            "heading": (
                f"font-family: {escape_xml(heading_family)}; "
                f"fill: {escape_xml(self.style.get_heading_color())}; "
                f"font-weight: {escape_xml(str(self.style.heading_font_weight))};"
            ),
            "code": (
                f"font-family: {escape_xml(code_family)}; "
                f"fill: {escape_xml(self.style.code_color)};" + code_extra
            ),
            "link": f"fill: {escape_xml(self.style.link_color)};",
            "blockquote": f"fill: {escape_xml(self.style.blockquote_color)};"
            + bq_extra,
        }

    def _compute_class_prefix(self) -> str:
        """Deterministic scope for this renderer's `.md-*` classes.

        Inline SVG has no style scope in an HTML page: two boards sharing a
        page would have the *last* `.md-heading` rule win for both. Hashing
        the rule bodies means identical styles correctly share a scope
        (their rules are identical anyway) while distinct styles never
        collide — with no uuid or counter to churn goldens between runs.
        """
        bodies = self._style_rule_bodies()
        css = "\x00".join(bodies[name] for name in self._STYLE_RULE_NAMES)
        return hashlib.sha256(css.encode(), usedforsecurity=False).hexdigest()[:8]

    def _scoped_class(self, name: str) -> str:
        """The scoped class name for bare rule `name` (e.g. "heading")."""
        return f"md-{self._class_prefix}-{name}"

    def _get_style_block(self) -> str:
        """Generate the CSS style block for SVG rendering.

        Returns:
            A <style> block string with CSS classes for text, headings, code, etc.
        """
        bodies = self._style_rule_bodies()
        rules = "\n".join(
            f"    .{self._scoped_class(name)} {{ {bodies[name]} }}"
            for name in self._STYLE_RULE_NAMES
        )
        return f"""  <style>
{rules}
  </style>"""

    def _inline_code_size(self, host_font_size: float) -> float:
        """Painted size of an inline-code run sitting in text of `host_font_size`.

        Inline code is measured against the text around it, so a heading scales
        its code with the heading rather than dropping to the body size. Both
        the measurement and the paint must call this — a second copy of the
        arithmetic is how the chip rect and the glyphs drift apart.
        """
        if self.style.code_font_size:
            return self.style.code_font_size
        return _round_half(host_font_size * self.style.code_font_scale)

    def _block_code_size(self) -> float:
        """Painted size of a fenced code block.

        Scales off `base_font_size` rather than any host: a block sits inside
        nothing, so tying it to a surrounding size would make it jump around.
        """
        if self.style.code_font_size:
            return self.style.code_font_size
        return _round_half(self.style.base_font_size * self.style.code_font_scale)

    def _code_text_attrs(self, font_size: float) -> str:
        """SVG presentation attrs for a fenced code <text>, honoring the full code
        font overlay (family overrides mono; weight/style/decoration).

        `font_size` is already resolved by `_block_code_size`; re-resolving the
        override here would be a second copy of that decision.
        """
        s = self.style
        attrs = [
            f'font-family="{escape_xml(s.code_font_family or s.mono_font_family)}"',
            f'font-size="{format_number(font_size)}"',
            f'font-weight="{escape_xml(s.code_font_weight or "400")}"',
        ]
        if s.code_font_style:
            attrs.append(f'font-style="{escape_xml(s.code_font_style)}"')
        if s.code_font_decoration:
            attrs.append(f'text-decoration="{escape_xml(s.code_font_decoration)}"')
        return " ".join(attrs)

    def _code_inline_style_parts(self, host_font_size: float) -> list[str]:
        """CSS declarations for an inline-code <tspan>, honoring the full code font
        overlay. Shared by prose inline code and table-cell code so they stay in sync.

        The size is emitted per-tspan because it depends on `host_font_size` —
        the same run is one size in prose and another in a heading, so no single
        document-level declaration could carry it.
        """
        s = self.style
        parts = [
            f"font-family: {s.code_font_family or s.mono_font_family}",
            f"fill: {s.code_color}",
            f"font-size: {format_number(self._inline_code_size(host_font_size))}px",
        ]
        if s.code_font_weight:
            parts.append(f"font-weight: {s.code_font_weight}")
        if s.code_font_style:
            parts.append(f"font-style: {s.code_font_style}")
        if s.code_font_decoration:
            parts.append(f"text-decoration: {s.code_font_decoration}")
        return parts

    def _link_text_decoration_style(self) -> list[str]:
        if not self.style.link_underline:
            return []
        return [
            "text-decoration: underline",
            "text-decoration-style: solid",
            "text-decoration-thickness: 1px",
            "text-underline-offset: 1px",
        ]

    def _build_svg(
        self,
        elements: List[str],
        width: float,
        height: float,
    ) -> str:
        """Build the complete SVG document."""
        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{format_number(width)}" height="{format_number(height)}" '
            f'viewBox="0 0 {format_number(width)} {format_number(height)}">',
        ]

        # Add a style block for fonts
        svg_parts.append(self._get_style_block())

        svg_parts.extend(elements)
        svg_parts.append("</svg>")

        return "\n".join(svg_parts)

    def _heading_margins(self, block: Block) -> Tuple[float, float]:
        """Return ``(margin_top, margin_bottom)`` if block is a Heading, else ``(0, 0)``.

        Mirrors the px-override-first / em-fallback selection used by
        ``_render_heading``. Used by the inter-block spacing logic so the
        loop can margin-collapse on heading-adjacent transitions without
        re-rendering the heading.
        """
        if not isinstance(block, Heading):
            return 0.0, 0.0
        return self._heading_margins_at(self.style.get_heading_size(block.level))

    def _heading_margins_at(self, font_size: float) -> Tuple[float, float]:
        """``(margin_top, margin_bottom)`` for a heading set at ``font_size``.

        Px overrides (set by callers wanting consistent pixel margins across
        heading levels) take precedence over the em scaling.
        """
        margin_top = (
            self.style.heading_margin_top_px
            if self.style.heading_margin_top_px is not None
            else font_size * self.style.heading_margin_top
        )
        margin_bottom = (
            self.style.heading_margin_bottom_px
            if self.style.heading_margin_bottom_px is not None
            else font_size * self.style.heading_margin_bottom
        )
        return margin_top, margin_bottom

    def first_baseline(self, blocks: Document, width: float) -> float:
        """How far below a document's top edge its first baseline sits.

        The number the first line was actually drawn with, at ``width``: a
        heading opening the document collapses its top margin, and an inline
        image taller than the text pushes the baseline down with it. Callers
        aligning something else to that line -- a row of controls beside a
        board title -- need this exact number; approximating it as a fraction
        of the block height holds only for the one font size it was measured
        at. The first block must be a heading or a paragraph.
        """
        first = blocks[0]
        if isinstance(first, Heading):
            font_size = self.style.get_heading_size(first.level)
            font_weight: str | int = self.style.heading_font_weight
            # `is not None`, matching _render_heading -- with `or`, a configured
            # heading_line_height of 0.0 would report a baseline the heading
            # was not drawn on.
            multiplier = (
                self.style.heading_line_height
                if self.style.heading_line_height is not None
                else self.style.line_height
            )
        elif isinstance(first, Paragraph):
            font_size = self.style.base_font_size
            font_weight = "normal"
            multiplier = self.style.line_height
        else:
            raise ValueError(
                f"first_baseline needs a heading or paragraph, got {first.block_type}"
            )
        lines = self._text_lines(
            first.spans,
            width,
            font_size,
            font_weight,
            multiplier,
            is_heading=isinstance(first, Heading),
        )
        # An empty heading paints no text; its line box still has a baseline.
        if not lines:
            return self._baseline_offset(font_size, multiplier)
        return lines[0].ascent

    def heading_line_box(self, level: int, block_height: float) -> Tuple[float, float]:
        """The ``(top, height)`` of the text's line box inside a heading block.

        A heading opening its document is line boxes + ``margin_bottom``: the top
        margin collapsed against the top of the box, the bottom one did not, so
        the block is not centered on the text it holds and anything drawn on the
        block sits low by that difference. This is the part of it that is text.

        ``block_height`` is the height this renderer reported for the block, so a
        heading that wrapped needs no line count: every line is above the margin.
        """
        _, margin_bottom = self._heading_margins_at(self.style.get_heading_size(level))
        return 0.0, block_height - margin_bottom

    def _inter_block_gap(self, prev: Block, curr: Block) -> float:
        """Compute the inter-block gap between ``prev`` and ``curr``.

        Three cases:
          * Both headings: subtract ``prev.margin_bottom`` so the visible
            joint reduces to ``curr.margin_top``. Approximates CSS margin
            collapse only when ``curr.margin_top >= prev.margin_bottom``
            (always true for the px-override path used by dct, where both
            margins are constant pixel values driven by body rhythm). The
            em-fallback path can produce ``curr.margin_top < prev.margin_bottom``
            on a steep H1→H6 ramp; in that edge case this under-spaces the
            joint relative to true ``max(prev.margin_bottom, curr.margin_top)``.
          * Either is a heading: 0. The heading's own ``margin_top`` /
            ``margin_bottom`` IS the rhythm; adding ``paragraph_spacing``
            would double-stack.
          * Neither is a heading: ``paragraph_spacing``.
        """
        prev_is_h = isinstance(prev, Heading)
        curr_is_h = isinstance(curr, Heading)
        if prev_is_h and curr_is_h:
            _, prev_bottom = self._heading_margins(prev)
            return -prev_bottom
        if prev_is_h or curr_is_h:
            return 0.0
        return self.style.paragraph_spacing

    def _render_block(
        self,
        block: Block,
        ctx: RenderContext,
    ) -> Tuple[List[str], float]:
        """Render a block and return SVG elements and height used."""
        if isinstance(block, Paragraph):
            return self._render_paragraph(block, ctx)
        elif isinstance(block, Heading):
            return self._render_heading(block, ctx)
        elif isinstance(block, CodeBlock):
            return self._render_code_block(block, ctx)
        elif isinstance(block, Blockquote):
            return self._render_blockquote(block, ctx)
        elif isinstance(block, UnorderedList):
            return self._render_unordered_list(block, ctx)
        elif isinstance(block, OrderedList):
            return self._render_ordered_list(block, ctx)
        elif isinstance(block, HorizontalRule):
            return self._render_horizontal_rule(ctx)
        elif isinstance(block, Table):
            return self._render_table(block, ctx)
        elif isinstance(block, ImageBlock):
            return self._render_image_block(block, ctx)
        elif isinstance(block, RawHtmlBlock):
            return self._render_raw_html_block(block, ctx)
        else:
            # Unknown block type
            return [], 0

    def _render_paragraph(
        self,
        para: Paragraph,
        ctx: RenderContext,
    ) -> Tuple[List[str], float]:
        """Render a paragraph."""
        return self._render_text_block(
            para.spans,
            ctx,
            font_size=self.style.base_font_size,
            css_class=self._scoped_class("text"),
        )

    def _render_raw_html_block(
        self,
        block: RawHtmlBlock,
        ctx: RenderContext,
    ) -> Tuple[List[str], float]:
        """Render a raw HTML block.

        When allow_raw_html is True, wraps in <foreignObject> with XHTML namespace.
        When False, escapes HTML and renders as a text paragraph.
        """
        if not self._allow_raw_html:
            # Escape and render as plain text paragraph
            from .types import Paragraph, Span

            escaped = Paragraph(spans=(Span(text=block.html),))
            return self._render_paragraph(escaped, ctx)

        sanitized = _sanitize_html(block.html)
        # Estimate height: count lines, use line_height heuristic
        line_count = max(1, sanitized.count("\n") + 1)
        line_h = self.style.base_font_size * self.style.line_height
        fo_height = line_count * line_h + 16  # 16px padding
        fo_width = ctx.width

        fo = (
            f'<foreignObject x="{format_number(ctx.x)}" y="{format_number(ctx.y)}" '
            f'width="{format_number(fo_width)}" height="{format_number(fo_height)}">'
            f'<div xmlns="http://www.w3.org/1999/xhtml" '
            f'style="font-family: {escape_xml(self.style.font_family)}; '
            f"font-size: {format_number(self.style.base_font_size)}px; "
            f'color: {escape_xml(self.style.text_color)};">'
            f"{sanitized}"
            f"</div>"
            f"</foreignObject>"
        )
        return [fo], fo_height

    def _render_heading(
        self,
        heading: Heading,
        ctx: RenderContext,
    ) -> Tuple[List[str], float]:
        """Render a heading."""
        font_size = self.style.get_heading_size(heading.level)
        margin_top, margin_bottom = self._heading_margins_at(font_size)

        elements, text_height = self._render_text_block(
            heading.spans,
            ctx.with_offset(dy=margin_top),
            font_size=font_size,
            css_class=self._scoped_class("heading"),
            font_weight=self.style.heading_font_weight,
            line_height_multiplier=self.style.heading_line_height,
            is_heading=True,
        )

        return elements, margin_top + text_height + margin_bottom

    def _render_code_block(
        self,
        code: CodeBlock,
        ctx: RenderContext,
    ) -> Tuple[List[str], float]:
        """Render a code block with background."""
        overflow = self.style.code_block_overflow

        if overflow == "wrap":
            return self._render_code_block_wrapped(code, ctx)
        elif overflow in {"show", "hide", "ellipsis"}:
            return self._render_code_block_simple(code, ctx, overflow)
        else:
            # Defensive fallback (should be unreachable due to typing)
            return self._render_code_block_wrapped(code, ctx)

    def _render_code_block_background(
        self,
        ctx: RenderContext,
        block_width: float,
        total_height: float,
    ) -> str:
        """The fenced-code-block background rect, with optional border stroke."""
        border_color = self.style.code_block_border_color
        stroke_attrs = (
            f' stroke="{escape_xml(border_color)}" stroke-width="{format_number(self.style.code_block_border_width)}"'
            if border_color
            else ""
        )
        return (
            f'  <rect x="{format_number(ctx.x)}" y="{format_number(ctx.y)}" '
            f'width="{format_number(block_width)}" height="{format_number(total_height)}" '
            f'fill="{escape_xml(self.style.code_background)}" '
            f'rx="{format_number(self.style.code_block_border_radius)}"{stroke_attrs}/>'
        )

    def _render_code_block_simple(
        self,
        code: CodeBlock,
        ctx: RenderContext,
        overflow: str,
    ) -> Tuple[List[str], float]:
        """Render code block with show/hide/ellipsis overflow."""
        elements: List[str] = []

        padding_x, padding_y = self._code_block_padding()
        font_size = self._block_code_size()
        line_height = font_size * 1.4
        char_width = font_size * self._ensure_mono_char_width()
        max_chars = int((ctx.width - padding_x * 2) / char_width)

        lines = self._logical_code_lines(code)

        # Process lines for ellipsis mode
        if overflow == "ellipsis":
            processed_lines = []
            for segments in lines:
                line_len = sum(len(text) for text, _ in segments)
                if line_len > max_chars and max_chars > 3:
                    segments = _truncate_segments(segments, max_chars - 3) + [
                        ("...", "")
                    ]
                processed_lines.append(segments)
            lines = processed_lines

        plain_lines = ["".join(text for text, _ in segments) for segments in lines]

        text_height = len(lines) * line_height
        total_height = text_height + (padding_y * 2)
        content_width = self._code_content_width(plain_lines, char_width)
        self._max_content_width = max(
            self._max_content_width, ctx.x + padding_x + content_width
        )
        block_width = self._code_block_width(
            content_width,
            padding=padding_x,
            max_width=ctx.width,
        )

        # For hide mode, add a clipPath
        clip_id = None
        if overflow == "hide":
            # Content + geometry, not id() — a memory address is nondeterministic
            # between runs and can collide when two renders share one document
            # (address reuse). Geometry disambiguates two identical code blocks
            # rendered at different positions in the same document.
            clip_hash = hashlib.sha256(
                "\x00".join(
                    [
                        code.code,
                        code.language or "",
                        format_number(ctx.x),
                        format_number(ctx.y),
                        format_number(block_width),
                        format_number(total_height),
                    ]
                ).encode(),
                usedforsecurity=False,
            ).hexdigest()[:8]
            clip_id = f"code-clip-{clip_hash}"
            elements.append(
                f'  <defs><clipPath id="{escape_xml(clip_id)}">'
                f'<rect x="{format_number(ctx.x)}" y="{format_number(ctx.y)}" '
                f'width="{format_number(block_width)}" height="{format_number(total_height)}"/>'
                f"</clipPath></defs>"
            )

        elements.append(
            self._render_code_block_background(ctx, block_width, total_height)
        )

        # Code lines (optionally clipped)
        clip_attr = f' clip-path="url(#{escape_xml(clip_id)})"' if clip_id else ""
        code_fill = self.style.code_color or self.style.text_color
        code_attrs = self._code_text_attrs(font_size)
        y_offset = ctx.y + padding_y + font_size
        for segments in lines:
            rendered = self._render_code_line_element(
                segments,
                ctx.x + padding_x,
                y_offset,
                code_attrs,
                code_fill,
                clip_attr,
            )
            if rendered:
                elements.append(rendered)
            y_offset += line_height

        return elements, total_height

    def _render_code_block_wrapped(
        self,
        code: CodeBlock,
        ctx: RenderContext,
    ) -> Tuple[List[str], float]:
        """Render code block with wrapped lines."""
        elements: List[str] = []

        padding_x, padding_y = self._code_block_padding()
        font_size = self._block_code_size()
        line_height = font_size * 1.4
        char_width = font_size * self._ensure_mono_char_width()
        max_chars = max(10, int((ctx.width - padding_x * 2) / char_width))

        wrapped_lines: List[List[CodeSegment]] = []
        for segments in self._logical_code_lines(code):
            wrapped_lines.extend(_wrap_colored_line(segments, max_chars))

        plain_lines = [
            "".join(text for text, _ in segments) for segments in wrapped_lines
        ]

        text_height = len(wrapped_lines) * line_height
        total_height = text_height + (padding_y * 2)
        content_width = self._code_content_width(plain_lines, char_width)
        self._max_content_width = max(
            self._max_content_width, ctx.x + padding_x + content_width
        )
        block_width = self._code_block_width(
            content_width,
            padding=padding_x,
            max_width=ctx.width,
        )

        elements.append(
            self._render_code_block_background(ctx, block_width, total_height)
        )

        # Code lines
        code_fill = self.style.code_color or self.style.text_color
        code_attrs = self._code_text_attrs(font_size)
        y_offset = ctx.y + padding_y + font_size
        for segments in wrapped_lines:
            rendered = self._render_code_line_element(
                segments, ctx.x + padding_x, y_offset, code_attrs, code_fill
            )
            if rendered:
                elements.append(rendered)
            y_offset += line_height

        return elements, total_height

    def _render_blockquote_paragraph(
        self,
        para: Paragraph,
        ctx: RenderContext,
    ) -> Tuple[List[str], float]:
        """Render a blockquote paragraph using the md-blockquote CSS class.

        Case transform is applied here so blockquote_font_case is honored.
        """
        spans, font_size = self._blockquote_text(para)
        return self._render_text_block(
            spans,
            ctx,
            font_size=font_size,
            css_class=self._scoped_class("blockquote"),
        )

    def _blockquote_text(self, para: Paragraph) -> Tuple[Tuple[Span, ...], float]:
        bq_case = self.style.blockquote_font_case
        spans = (
            tuple(replace(s, text=_apply_case(s.text, bq_case)) for s in para.spans)
            if bq_case
            else para.spans
        )
        return spans, self.style.blockquote_font_size or self.style.base_font_size

    def _render_blockquote(
        self,
        bq: Blockquote,
        ctx: RenderContext,
    ) -> Tuple[List[str], float]:
        """Render a blockquote with left border."""
        elements: List[str] = []

        # Create indented context for content
        indent = self.style.blockquote_padding
        inner_ctx = ctx.with_indent(indent)

        # Render inner blocks
        current_y = 0.0
        inner_elements: List[str] = []
        prev_block: Block | None = None

        for block in bq.blocks:
            if prev_block is not None:
                current_y += self._inter_block_gap(prev_block, block)
            # A blockquote is a box like any other, so the block that opens it
            # collapses its leading margin the same way — see
            # _render_blocks_to_elements, which states the rule.
            lift = self._heading_margins(block)[0] if prev_block is None else 0.0
            if isinstance(block, Paragraph):
                block_elements, height = self._render_blockquote_paragraph(
                    block,
                    inner_ctx.with_offset(dy=current_y - lift),
                )
            else:
                block_elements, height = self._render_block(
                    block,
                    inner_ctx.with_offset(dy=current_y - lift),
                )
            inner_elements.extend(block_elements)
            current_y += height - lift
            prev_block = block

        total_height = current_y

        # Background rect (when blockquote_background is set)
        bg_color = self.style.blockquote_background
        if bg_color:
            rx = self.style.blockquote_border_radius
            rx_attr = f' rx="{format_number(rx)}"' if rx else ""
            elements.append(
                f'  <rect x="{format_number(ctx.x)}" y="{format_number(ctx.y)}" '
                f'width="{format_number(ctx.width)}" height="{format_number(total_height)}" '
                f'fill="{escape_xml(bg_color)}"{rx_attr}/>'
            )

        # Left border rule
        elements.append(
            f'  <rect x="{format_number(ctx.x)}" y="{format_number(ctx.y)}" '
            f'width="{format_number(self.style.blockquote_border_width)}" '
            f'height="{format_number(total_height)}" '
            f'fill="{escape_xml(self.style.blockquote_border_color)}"/>'
        )

        elements.extend(inner_elements)

        return elements, total_height

    def _render_item_children(
        self,
        item_ctx: RenderContext,
        item_height: float,
        children: tuple[AnyBlock, ...],
    ) -> Tuple[List[str], float]:
        """Render nested child blocks (sub-lists, etc.) below an item's own text.

        Returns (elements, total_height) where total_height includes item_height
        plus any children. A childless item returns ([], 0.0) so flat lists are
        byte-identical to the pre-children implementation.
        """
        if not children:
            return [], 0.0
        child_elements: List[str] = []
        child_y = item_height
        for child_block in children:
            c_elements, c_height = self._render_block(
                child_block, item_ctx.with_offset(dy=child_y)
            )
            child_elements.extend(c_elements)
            child_y += c_height
        return child_elements, child_y - item_height

    def _item_lines(self, item: ListItem, width: float) -> Tuple[List[_Line], float]:
        """A list item's lines, and how far its first baseline sits below a
        plain text line's -- an inline image makes that line taller, and the
        item's marker has to move down with the words it labels."""
        font_size = self.style.base_font_size
        lines = self._text_lines(
            item.spans, width, font_size, "normal", self.style.line_height
        )
        if not lines:
            return lines, 0.0
        return lines, lines[0].ascent - self._baseline_offset(
            font_size, self.style.line_height
        )

    def _render_unordered_list(
        self,
        ul: UnorderedList,
        ctx: RenderContext,
    ) -> Tuple[List[str], float]:
        """Render an unordered list."""
        elements: List[str] = []
        current_y = 0.0

        bullet_indent = self.style.list_indent

        for item in ul.items:
            # WHY: journalism-style prose lists read quieter with a small marker
            # and a tight, stable gap from marker edge to text.
            bullet_radius = 1.5
            bullet_gap = 6.5
            bullet_x = ctx.x + bullet_indent - bullet_gap - bullet_radius
            item_ctx = ctx.with_indent(bullet_indent).with_offset(dy=current_y)
            lines, drop = self._item_lines(item, item_ctx.width)
            # Half a line box down is also the center of the line's text: the
            # half-leading above the ascent and below the descent are equal, so
            # the two midpoints coincide. Pinned by
            # test_a_bullet_is_centered_on_its_item_s_text.
            bullet_y = (
                ctx.y
                + current_y
                + (self.style.base_font_size * self.style.line_height / 2)
                + drop
            )

            elements.append(
                f'  <circle cx="{format_number(bullet_x)}" '
                f'cy="{format_number(bullet_y)}" r="{format_number(bullet_radius)}" '
                f'fill="{escape_xml(self.style.text_color)}"/>'
            )

            item_elements, item_height = self._paint_text_lines(
                lines,
                item_ctx,
                font_size=self.style.base_font_size,
                css_class=self._scoped_class("text"),
            )
            elements.extend(item_elements)

            child_elements, child_height = self._render_item_children(
                item_ctx, item_height, item.children
            )
            elements.extend(child_elements)

            current_y += item_height + child_height + self.style.list_item_spacing

        if ul.items:
            current_y -= self.style.list_item_spacing

        return elements, current_y

    def _render_ordered_list(
        self,
        ol: OrderedList,
        ctx: RenderContext,
    ) -> Tuple[List[str], float]:
        """Render an ordered list."""
        elements: List[str] = []
        current_y = 0.0

        bullet_indent = self.style.list_indent

        for idx, item in enumerate(ol.items):
            number = ol.start + idx

            number_text = f"{number}."
            number_x = ctx.x + bullet_indent - 8
            item_ctx = ctx.with_indent(bullet_indent).with_offset(dy=current_y)
            lines, drop = self._item_lines(item, item_ctx.width)
            # The same expression `_render_text_block` uses for the item's own
            # first baseline, so a marker and the words it labels sit on one line
            # by construction rather than by two formulas agreeing.
            number_y = (
                ctx.y
                + current_y
                + self._baseline_offset(
                    self.style.base_font_size, self.style.line_height
                )
                + drop
            )

            elements.append(
                f'  <text x="{format_number(number_x)}" '
                f'y="{format_number(number_y)}" '
                f'class="{escape_xml(self._scoped_class("text"))}" '
                f'font-size="{format_number(self.style.base_font_size)}" '
                f'text-anchor="end">{number_text}</text>'
            )

            item_elements, item_height = self._paint_text_lines(
                lines,
                item_ctx,
                font_size=self.style.base_font_size,
                css_class=self._scoped_class("text"),
            )
            elements.extend(item_elements)

            child_elements, child_height = self._render_item_children(
                item_ctx, item_height, item.children
            )
            elements.extend(child_elements)

            current_y += item_height + child_height + self.style.list_item_spacing

        if ol.items:
            current_y -= self.style.list_item_spacing

        return elements, current_y

    def _render_horizontal_rule(
        self,
        ctx: RenderContext,
    ) -> Tuple[List[str], float]:
        """Render a horizontal rule."""
        height = self.style.hr_height
        margin = self.style.paragraph_spacing

        y_pos = ctx.y + margin

        if not self.style.hr_visible:
            return [], margin + height + margin
        if self.style.hr_dasharray:
            mid = y_pos + height / 2
            element = (
                f'  <line x1="{format_number(ctx.x)}" y1="{format_number(mid)}" '
                f'x2="{format_number(ctx.x + ctx.width)}" y2="{format_number(mid)}" '
                f'stroke="{escape_xml(self.style.hr_color)}" '
                f'stroke-width="{format_number(height)}" '
                f'stroke-dasharray="{escape_xml(self.style.hr_dasharray)}"/>'
            )
        else:
            element = (
                f'  <rect x="{format_number(ctx.x)}" '
                f'y="{format_number(y_pos)}" '
                f'width="{format_number(ctx.width)}" '
                f'height="{format_number(height)}" '
                f'fill="{escape_xml(self.style.hr_color)}"/>'
            )

        return [element], margin + height + margin

    def _render_table(
        self,
        table: Table,
        ctx: RenderContext,
    ) -> Tuple[List[str], float]:
        """Render a table."""
        elements: List[str] = []

        padding = self.style.table_cell_padding
        font_size = self.style.base_font_size

        # Calculate column widths (equal distribution for now)
        num_cols = len(table.header.cells)
        col_width = ctx.width / num_cols if num_cols > 0 else ctx.width

        header_layout = self._measure_table_row_layout(
            table.header,
            col_width=col_width,
            padding=padding,
            font_size=font_size,
            is_header=True,
        )
        body_layouts = [
            self._measure_table_row_layout(
                row,
                col_width=col_width,
                padding=padding,
                font_size=font_size,
                is_header=False,
            )
            for row in table.rows
        ]

        current_y = ctx.y

        # Render header background
        elements.append(
            f'  <rect x="{format_number(ctx.x)}" y="{format_number(current_y)}" '
            f'width="{format_number(ctx.width)}" height="{format_number(header_layout.row_height)}" '
            f'fill="{escape_xml(self.style.table_header_background)}"/>'
        )

        # Render header row
        self._render_table_row(
            elements,
            table.header,
            header_layout,
            ctx.x,
            current_y,
            col_width,
            padding,
            font_size,
            is_header=True,
        )
        current_y += header_layout.row_height

        # Render body rows
        for row, row_layout in zip(table.rows, body_layouts):
            self._render_table_row(
                elements,
                row,
                row_layout,
                ctx.x,
                current_y,
                col_width,
                padding,
                font_size,
                is_header=False,
            )
            current_y += row_layout.row_height

        total_height = current_y - ctx.y
        if not self.style.table_border_visible:
            return elements, total_height
        stroke = self._table_border_stroke()

        elements.append(
            f'  <rect x="{format_number(ctx.x)}" y="{format_number(ctx.y)}" '
            f'width="{format_number(ctx.width)}" height="{format_number(total_height)}" '
            f'fill="none" {stroke}/>'
        )

        # Column separators
        col_x = ctx.x
        for _ in range(num_cols - 1):
            col_x += col_width
            elements.append(
                f'  <line x1="{format_number(col_x)}" y1="{format_number(ctx.y)}" '
                f'x2="{format_number(col_x)}" y2="{format_number(current_y)}" '
                f"{stroke}/>"
            )

        # Row separators
        row_y = ctx.y
        row_heights = [
            header_layout.row_height,
            *[layout.row_height for layout in body_layouts],
        ]
        for row_height in row_heights:
            row_y += row_height
            if row_y < current_y:
                elements.append(
                    f'  <line x1="{format_number(ctx.x)}" y1="{format_number(row_y)}" '
                    f'x2="{format_number(ctx.x + ctx.width)}" y2="{format_number(row_y)}" '
                    f"{stroke}/>"
                )

        return elements, total_height

    def _table_border_stroke(self) -> str:
        """Stroke attributes shared by every table gridline."""
        stroke = f'stroke="{escape_xml(self.style.table_border_color)}"'
        if self.style.table_border_width != 1.0:
            stroke += f' stroke-width="{format_number(self.style.table_border_width)}"'
        if self.style.table_border_dasharray:
            stroke += (
                f' stroke-dasharray="{escape_xml(self.style.table_border_dasharray)}"'
            )
        return stroke

    def _table_cell_runs(
        self,
        cell: TableCell,
        is_header: bool,
        max_width: float,
    ) -> List[TextRun]:
        """Build text runs for a table cell, forcing bold widths for headers."""
        runs = self._build_text_runs(cell.spans, max_width)
        if not is_header:
            return runs
        return [replace(run, is_bold=True) for run in runs]

    def _measure_table_row_layout(
        self,
        row: TableRow,
        col_width: float,
        padding: float,
        font_size: float,
        is_header: bool,
    ) -> TableRowLayout:
        """Lay out each cell on its own lines; the row is its tallest cell."""
        max_width = max(col_width - (padding * 2), 1.0)
        line_height = font_size * self.style.line_height
        # A cell's text sits one font size below its line's top, not on the
        # half-leading baseline prose uses. An empty cell still paints one
        # (empty) line, so every cell holds at least one.
        cell_lines = tuple(
            tuple(
                self._layout_lines(
                    self._table_cell_runs(cell, is_header, max_width),
                    max_width,
                    font_size,
                    font_size,
                    line_height,
                )
            )
            or (_Line((), font_size, line_height),)
            for cell in row.cells
        )
        content_height = max(
            (sum(line.height for line in lines) for lines in cell_lines),
            default=line_height,
        )
        return TableRowLayout(
            cell_lines=cell_lines, row_height=content_height + (padding * 2)
        )

    def _render_table_row(
        self,
        elements: List[str],
        row: TableRow,
        layout: TableRowLayout,
        start_x: float,
        y: float,
        col_width: float,
        padding: float,
        font_size: float,
        is_header: bool,
    ) -> None:
        """Render a single table row."""
        weight = self.style.bold_font_weight if is_header else "normal"
        text_attrs = (
            f'class="{escape_xml(self._scoped_class("text"))}" '
            f'font-size="{format_number(font_size)}" '
            f'font-weight="{escape_xml(str(weight))}"'
        )
        x = start_x
        for cell, lines in zip(row.cells, layout.cell_lines):
            if cell.align == "center":
                anchor_x, anchor = x + col_width / 2, "middle"
            elif cell.align == "right":
                anchor_x, anchor = x + col_width - padding, "end"
            else:
                anchor_x, anchor = x + padding, "start"
            # Measured at no particular weight, the same as the cell was wrapped.
            self._emit_lines(
                elements,
                lines,
                anchor_x,
                anchor,
                y + padding,
                font_size,
                None,
                False,
                text_attrs,
                chips=False,
            )
            x += col_width

    def _strip_segment_leading_space(
        self,
        run: TextRun,
        segment_x: float,
        font_size: float,
        font_weight: str | int | None,
        is_heading: bool,
    ) -> Tuple[TextRun, float]:
        """Strip a run's leading whitespace and advance segment_x past it.

        A text run opening a new <text> segment right after an inline image
        (or opening a line that starts with one) can carry the wrap
        algorithm's separator as a literal leading space (_wrap_runs
        prepends it via TextRun.append when merging same-style runs). SVG
        collapses a <text> element's leading whitespace for rendering but
        not for layout, which already measured it as part of the run's
        width -- left in, the space vanishes on screen and the visible text
        sits one space-width off from where it was measured to be.
        """
        stripped = run.text.lstrip(" ")
        if stripped == run.text:
            return run, segment_x
        leading_ws = run.text[: len(run.text) - len(stripped)]
        ws_width = self._measure_text(
            leading_ws,
            font_size,
            is_bold=run.is_bold,
            is_italic=run.is_italic,
            is_mono=run.is_code,
            font_weight=font_weight,
            is_heading=is_heading,
        )
        return run.with_text(stripped), segment_x + ws_width

    def _get_image_size(self, url: str) -> Optional[ImageSize]:
        """Get image dimensions, using cache to avoid re-fetching."""
        if url in self._image_size_cache:
            return self._image_size_cache[url]

        # Skip fetching if enforce_aspect_ratio is set (speed optimization)
        if self.style.image_enforce_aspect_ratio:
            return None

        # A data: URI is measured by decoding its own payload — no I/O, so
        # this always runs regardless of fetch_image_sizes (which gates
        # local-file and remote-network fetches only). Scheme match is
        # case-insensitive (RFC 3986 schemes are; get_image_size's own
        # urlparse already lowercases before dispatch).
        if not self._fetch_image_sizes and not url.lower().startswith("data:"):
            return None

        size = get_image_size(
            url,
            base_path=self._image_base_path,
            timeout=self._image_timeout,
        )
        self._image_size_cache[url] = size
        return size

    def _map_image_url(self, url: str) -> str:
        """Apply URL mapper if configured."""
        if self._image_url_mapper:
            return self._image_url_mapper(url)
        return url

    def _render_image_block(
        self,
        img: ImageBlock,
        ctx: RenderContext,
    ) -> Tuple[List[str], float]:
        """Render an image block.

        Image sizing priority:
        1. Explicit dimensions from markdown: ![alt](url){width=X height=Y}
        2. Measured dimensions from the actual image -- a data: URI is always
           measured (decoding its own payload needs no I/O); a local file or
           remote http(s) URL is measured only when fetch_image_sizes=True
        3. Style defaults (image_width, image_height)
        4. Fallback: full width with image_fallback_aspect_ratio

        The preserveAspectRatio attribute ensures the actual image
        scales proportionally within the allocated space.
        """
        # Try to get actual image dimensions
        actual_size = self._get_image_size(img.url)

        # Determine dimensions using priority order
        explicit_width = img.width
        explicit_height = img.height

        # Calculate final width
        if explicit_width is not None:
            # Explicit width from markdown
            img_width = min(ctx.width, explicit_width)
        elif self.style.image_width is not None:
            # Style default width
            img_width = min(ctx.width, self.style.image_width)
        else:
            # Full container width
            img_width = ctx.width

        # Calculate final height
        if explicit_height is not None:
            # Explicit height from markdown
            img_height = explicit_height
        elif explicit_width is not None and actual_size is not None:
            # Scale height based on actual aspect ratio
            img_height = img_width / actual_size.aspect_ratio
        elif self.style.image_height is not None:
            # Style default height
            img_height = self.style.image_height
        elif actual_size is not None:
            # Use actual image aspect ratio
            img_height = img_width / actual_size.aspect_ratio
        else:
            # Fallback to configured aspect ratio
            img_height = img_width / self.style.image_fallback_aspect_ratio

        elements = self._image_elements(
            ctx.x, ctx.y, img_width, img_height, img.url, img.alt
        )
        return elements, img_height

    def _inline_image(self, span: Span, available_width: float) -> InlineImage:
        """Resolve the on-page box for an inline image span.

        Priority: explicit ``{width=/height=}`` -> the image's own measured
        intrinsic size (rendered near its natural pixel size, like a
        browser's ``<img>`` mid-sentence -- NOT stretched to the container,
        which is what a standalone image block does) -> style defaults ->
        the configured fallback aspect ratio, the same last-resort tier
        ``_render_image_block`` falls back to for an unmeasurable standalone
        image.

        The box is then scaled down, preserving aspect ratio, if it would not
        fit *available_width* -- an inline image never forces its line wider
        than the container, the same way a long unbreakable word doesn't.
        """
        assert span.url is not None, "Span.__post_init__ requires an IMAGE url"
        actual_size = self._get_image_size(span.url)

        if span.width is not None:
            img_width = span.width
        elif span.height is not None:
            aspect = (
                actual_size.aspect_ratio
                if actual_size is not None
                else self.style.image_fallback_aspect_ratio
            )
            img_width = span.height * aspect
        elif actual_size is not None:
            img_width = actual_size.width
        elif self.style.image_width is not None:
            img_width = self.style.image_width
        else:
            img_width = available_width

        if span.height is not None:
            img_height = span.height
        elif actual_size is not None:
            img_height = img_width / actual_size.aspect_ratio
        elif self.style.image_height is not None:
            img_height = self.style.image_height
        else:
            img_height = img_width / self.style.image_fallback_aspect_ratio

        if img_width > available_width > 0:
            scale = available_width / img_width
            img_width *= scale
            img_height *= scale

        return InlineImage(
            url=span.url, width=img_width, height=img_height, alt=span.text
        )

    def _image_elements(
        self, x: float, y: float, width: float, height: float, url: str, alt: str
    ) -> List[str]:
        """One ``<image>`` and its alt text as a ``<title>``, for accessibility --
        standalone block or inline in a line alike."""
        elements = [
            f'  <image x="{format_number(x)}" y="{format_number(y)}" '
            f'width="{format_number(width)}" height="{format_number(height)}" '
            f'href="{escape_svg_text(self._map_image_url(url))}" '
            f'preserveAspectRatio="{escape_xml(self.style.image_preserve_aspect_ratio)}"/>'
        ]
        if alt:
            elements.append(f"  <title>{escape_svg_text(alt)}</title>")
        return elements

    def _baseline_offset(
        self, font_size: float, line_height_multiplier: float
    ) -> float:
        """How far below a line box's top edge that line's baseline sits.

        The CSS model: the face's content box is ``ascent + descent``, the line
        box is ``font_size * multiplier``, and the difference is leading, split
        evenly above and below. The baseline then falls one half-leading plus one
        ascent down.

        Assuming an ascent of exactly one em and putting the whole leading under
        the baseline — which is what this used to do — sets text low in its own
        line box by ``ascent - font_size + half_leading``, growing with the font
        size and worst on headings, whose tighter line heights make the leading
        negative.
        """
        ascent = self._measurer.ascent_em * font_size
        descent = self._measurer.descent_em * font_size
        half_leading = (font_size * line_height_multiplier - ascent - descent) / 2
        return half_leading + ascent

    def _text_lines(
        self,
        spans: Sequence[Span],
        width: float,
        font_size: float,
        font_weight: str | int,
        line_height_multiplier: float,
        is_heading: bool = False,
    ) -> List[_Line]:
        """Prose lines -- paragraphs, headings, list items -- at *width*."""
        return self._layout_lines(
            self._build_text_runs(spans, width),
            width,
            font_size,
            self._baseline_offset(font_size, line_height_multiplier),
            font_size * line_height_multiplier,
            font_weight,
            is_heading,
        )

    def _layout_lines(
        self,
        runs: List[TextRun],
        max_width: float,
        font_size: float,
        text_ascent: float,
        line_height: float,
        font_weight: str | int | None = None,
        is_heading: bool = False,
    ) -> List[_Line]:
        """Wrap *runs* into lines and size each line's box.

        The one line layout: prose and table cells read their lines from here,
        and ``measure_blocks`` reports the same heights render draws. A line
        is ``line_height`` tall with its baseline ``text_ascent`` below its
        top, unless an inline image on it is taller than ``text_ascent`` --
        the image's bottom sits on the baseline, so the box grows upward by
        the difference.
        """
        lines: List[_Line] = []
        for line_runs in self._wrap_runs(
            runs, max_width, font_size, font_weight, is_heading
        ):
            ascent = max(
                [text_ascent]
                + [run.image.height for run in line_runs if run.image is not None]
            )
            lines.append(
                _Line(tuple(line_runs), ascent, line_height + (ascent - text_ascent))
            )
        return lines

    def _render_text_block(
        self,
        spans: Sequence[Span],
        ctx: RenderContext,
        font_size: float,
        css_class: str,
        font_weight: str | int = "normal",
        line_height_multiplier: Optional[float] = None,
        line_window: tuple[int, int | None] | None = None,
        is_heading: bool = False,
    ) -> Tuple[List[str], float]:
        """Render a sequence of spans as wrapped text using tspan for proper spacing.

        ``line_window`` restricts output to ``(start, count)`` of the wrapped
        lines, drawn at the top of the box rather than at their original
        offsets. Wrapping is unaffected -- the same break positions are chosen
        either way, so a windowed render is a slice of the whole, not a reflow.
        """
        if not spans:
            return [], 0

        multiplier = (
            line_height_multiplier
            if line_height_multiplier is not None
            else self.style.line_height
        )
        lines = self._text_lines(
            spans, ctx.width, font_size, font_weight, multiplier, is_heading
        )
        if line_window is not None:
            start, count = line_window
            lines = lines[start:] if count is None else lines[start : start + count]

        return self._paint_text_lines(
            lines, ctx, font_size, css_class, font_weight, is_heading
        )

    def _paint_text_lines(
        self,
        lines: Sequence[_Line],
        ctx: RenderContext,
        font_size: float,
        css_class: str,
        font_weight: str | int = "normal",
        is_heading: bool = False,
    ) -> Tuple[List[str], float]:
        """Paint laid-out prose lines at the block's own text alignment."""
        anchor = self.style.get_text_anchor()
        if self.style.text_align == "center":
            anchor_x = ctx.x + ctx.width / 2
        elif self.style.text_align == "right":
            anchor_x = ctx.x + ctx.width
        else:
            anchor_x = ctx.x

        elements: List[str] = []
        height = self._emit_lines(
            elements,
            lines,
            anchor_x,
            anchor,
            ctx.y,
            font_size,
            font_weight,
            is_heading,
            f'font-size="{format_number(font_size)}" class="{escape_xml(css_class)}"',
            chips=True,
        )
        return elements, height

    def _emit_lines(
        self,
        elements: List[str],
        lines: Sequence[_Line],
        anchor_x: float,
        anchor: str,
        top: float,
        font_size: float,
        font_weight: str | int | None,
        is_heading: bool,
        text_attrs: str,
        chips: bool,
    ) -> float:
        """Paint *lines* downward from *top*; return the height they took."""
        line_top = top
        for line in lines:
            self._emit_line(
                elements,
                line.runs,
                anchor_x,
                anchor,
                line_top + line.ascent,
                font_size,
                font_weight,
                is_heading,
                text_attrs,
                chips,
            )
            line_top += line.height
        return sum(line.height for line in lines)

    def _emit_line(
        self,
        elements: List[str],
        runs: Sequence[TextRun],
        anchor_x: float,
        anchor: str,
        baseline: float,
        font_size: float,
        font_weight: str | int | None,
        is_heading: bool,
        text_attrs: str,
        chips: bool,
    ) -> None:
        """Paint one line's runs on *baseline*, aligned at *anchor_x*.

        ``text_attrs`` is the caller's own ``<text>`` attributes between ``y``
        and ``text-anchor``. ``chips`` draws inline-code background boxes.
        """
        # Widths are measured on the display text (after case transform)
        # because that's what the font engine advances over.
        widths = [
            run.image.width
            if run.image is not None
            else self._measure_text(
                _apply_case(run.text, self.style.code_font_case)
                if (run.is_code and self.style.code_font_case)
                else run.text,
                font_size,
                is_bold=run.is_bold,
                is_italic=run.is_italic,
                is_mono=run.is_code,
                font_weight=font_weight,
                is_heading=is_heading,
            )
            for run in runs
        ]
        line_width = sum(widths)
        if anchor == "middle":
            left = anchor_x - line_width / 2
        elif anchor == "end":
            left = anchor_x - line_width
        else:
            left = anchor_x
        self._max_content_width = max(self._max_content_width, left + line_width)

        if chips:
            self._emit_code_chips(elements, runs, widths, left, baseline, font_size)

        if all(run.image is None for run in runs):
            content = self._tspan_content(runs, font_size, font_weight)
            elements.append(
                f'  <text x="{format_number(anchor_x)}" y="{format_number(baseline)}" '
                f'{text_attrs} text-anchor="{escape_xml(anchor)}">{content}</text>'
            )
            return

        # SVG has no inline <image> inside <text>: walk the line left to right,
        # emitting one start-anchored <text> per stretch of text runs and a
        # sibling <image> per image run, bottom-aligned to the baseline.
        segment: List[TextRun] = []
        segment_x = run_x = left

        def flush_segment() -> None:
            if segment:
                content = self._tspan_content(segment, font_size, font_weight)
                elements.append(
                    f'  <text x="{format_number(segment_x)}" '
                    f'y="{format_number(baseline)}" {text_attrs} '
                    f'text-anchor="start">{content}</text>'
                )
                segment.clear()

        for run, width in zip(runs, widths):
            if run.image is not None:
                flush_segment()
                elements.extend(
                    self._image_elements(
                        run_x,
                        baseline - run.image.height,
                        run.image.width,
                        run.image.height,
                        run.image.url,
                        run.image.alt,
                    )
                )
            else:
                if not segment:
                    run, segment_x = self._strip_segment_leading_space(
                        run, run_x, font_size, font_weight, is_heading
                    )
                segment.append(run)
            run_x += width
        flush_segment()

    def _emit_code_chips(
        self,
        elements: List[str],
        runs: Sequence[TextRun],
        widths: Sequence[float],
        left: float,
        baseline: float,
        font_size: float,
    ) -> None:
        """Background boxes behind a line's inline-code runs, before its text."""
        chip_padding = self.style.inline_code_padding
        chip_radius = self.style.inline_code_border_radius
        code_font_size = self._inline_code_size(font_size)
        # Chip spans cap-height above baseline to descender below, plus padding on
        # each side.  This centers the box on the glyph's visual body rather than
        # the full em-square, which over-shoots the cap top by ~28% of font_size.
        chip_height = (
            _CHIP_CAP_HEIGHT_RATIO + _CHIP_DESCENDER_RATIO
        ) * code_font_size + 2 * chip_padding
        rx_attr = f' rx="{format_number(chip_radius)}"' if chip_radius else ""
        run_x = left
        for run, rw in zip(runs, widths):
            if run.is_code and rw > 0:
                rect_x = run_x - chip_padding
                # Place rect top at cap height above the baseline (not the full
                # em-square), so the chip hugs the glyph's visual body rather
                # than floating above it.
                rect_y = (
                    baseline - _CHIP_CAP_HEIGHT_RATIO * code_font_size - chip_padding
                )
                elements.append(
                    f'  <rect x="{format_number(rect_x)}" y="{format_number(rect_y)}" '
                    f'width="{format_number(rw + 2 * chip_padding)}" '
                    f'height="{format_number(chip_height)}" '
                    f'fill="{escape_xml(self.style.code_background)}"{rx_attr}/>'
                )
            run_x += rw

    def _tspan_content(
        self,
        runs: Sequence[TextRun],
        font_size: float,
        font_weight: str | int | None,
    ) -> str:
        """Join a run of non-image text into <tspan>/<a> inner markup."""
        tspan_parts: List[str] = []
        for run in runs:
            if not run.text:
                continue

            # Case transform must run on raw text BEFORE escaping — uppercasing
            # an escaped string would corrupt entities (&amp; -> &AMP;).
            raw_text = (
                _apply_case(run.text, self.style.code_font_case)
                if (run.is_code and self.style.code_font_case)
                else run.text
            )
            escaped = escape_svg_text(raw_text)

            style_parts: List[str] = []

            if run.is_bold:
                style_parts.append(f"font-weight: {self.style.bold_font_weight}")
            elif font_weight not in (None, "normal"):
                style_parts.append(f"font-weight: {font_weight}")

            if run.is_italic:
                style_parts.append("font-style: italic")

            if run.is_code:
                style_parts.extend(self._code_inline_style_parts(font_size))

            if run.is_link:
                style_parts.append(f"fill: {self.style.link_color}")
                style_parts.extend(self._link_text_decoration_style())

            style_attr = (
                f' style="{escape_xml("; ".join(style_parts))}"' if style_parts else ""
            )

            if run.is_link and run.url:
                tspan_parts.append(
                    f'<a href="{escape_svg_text(_safe_href(run.url))}">'
                    f"<tspan{style_attr}>{escaped}</tspan></a>"
                )
            elif style_parts:
                tspan_parts.append(f"<tspan{style_attr}>{escaped}</tspan>")
            else:
                tspan_parts.append(escaped)

        return "".join(tspan_parts)

    def _build_text_runs(
        self,
        spans: Sequence[Span],
        max_width: float,
    ) -> List[TextRun]:
        """Convert spans to text runs, sizing each inline image for *max_width*."""
        return [
            TextRun(
                text=span.text,
                is_bold=span.span_type in (SpanType.BOLD, SpanType.BOLD_ITALIC),
                is_italic=span.span_type in (SpanType.ITALIC, SpanType.BOLD_ITALIC),
                is_code=span.span_type == SpanType.CODE,
                is_link=span.span_type == SpanType.LINK,
                url=span.url if span.span_type == SpanType.LINK else None,
                image=(
                    self._inline_image(span, max_width)
                    if span.span_type == SpanType.IMAGE
                    else None
                ),
            )
            for span in spans
        ]

    def _run_pieces(
        self,
        runs: List[TextRun],
        max_width: float,
        font_size: float,
        font_weight: str | int | None,
        is_heading: bool,
    ) -> List[WrapPiece]:
        """Break runs into measured pieces, each carrying the separator before it.

        Shared by wrapping and `max_content_width`, so the width a line is
        asked for is the width it is laid out at.
        """
        pieces: List[WrapPiece] = []
        separator = ""
        for run in runs:
            is_bold = run.is_bold
            is_italic = run.is_italic
            is_mono = run.is_code

            def measure(
                text: str,
                *,
                _is_bold: bool = is_bold,
                _is_italic: bool = is_italic,
                _is_mono: bool = is_mono,
            ) -> float:
                return self._measure_text(
                    text,
                    font_size,
                    is_bold=_is_bold,
                    is_italic=_is_italic,
                    is_mono=_is_mono,
                    font_weight=font_weight,
                    is_heading=is_heading,
                )

            # An inline image is a single unbreakable box in the line, like a
            # very long word.
            if run.image is not None:
                pieces.append(
                    WrapPiece(
                        text=run.text,
                        width=run.image.width,
                        separator=separator,
                        separator_width=measure(separator) if separator else 0.0,
                        meta=run,
                    )
                )
                separator = ""
                continue

            # A code span is one thing to read, and it paints one background box.
            # Splitting `width: "25%"` at its space puts half the span on each line
            # with a chip behind each half, which reads as two settings rather than
            # one. Keep it whole whenever it fits a line on its own; a span too long
            # for any line still falls through to token splitting below, since the
            # alternative is painting past the edge.
            if run.is_code and run.text.strip() and measure(run.text) <= max_width:
                pieces.append(
                    WrapPiece(
                        text=run.text,
                        width=measure(run.text),
                        separator=separator,
                        separator_width=measure(separator) if separator else 0.0,
                        meta=run,
                    )
                )
                separator = ""
                continue

            for token in re.findall(r"\S+|\s+", run.text):
                if token.isspace():
                    separator = " "
                    continue
                separator_width = measure(separator) if separator else 0.0
                for chunk_index, chunk in enumerate(
                    split_token_precise(token, max_width, measure)
                ):
                    pieces.append(
                        WrapPiece(
                            text=chunk,
                            width=measure(chunk),
                            separator=separator if chunk_index == 0 else "",
                            separator_width=(
                                separator_width if chunk_index == 0 else 0.0
                            ),
                            meta=run,
                        )
                    )
                    separator = ""
        return pieces

    def _wrap_runs(
        self,
        runs: List[TextRun],
        max_width: float,
        font_size: float,
        font_weight: str | int | None = None,
        is_heading: bool = False,
    ) -> List[List[TextRun]]:
        """Wrap text runs to fit within max_width."""
        if not runs:
            return []

        pieces = self._run_pieces(runs, max_width, font_size, font_weight, is_heading)

        wrapped = wrap_measured_pieces(
            pieces, max_width, avoid_runts=self.style.avoid_runts
        )
        lines: List[List[TextRun]] = []
        for line in wrapped:
            line_runs: List[TextRun] = []
            for index, piece in enumerate(line):
                run = piece.meta
                assert isinstance(run, TextRun)
                if index > 0 and piece.separator:
                    previous_run = line[index - 1].meta
                    if isinstance(previous_run, TextRun) and previous_run.same_style(
                        run
                    ):
                        text = f"{piece.separator}{piece.text}"
                    else:
                        separator_run = TextRun(piece.separator)
                        if line_runs and line_runs[-1].same_style(separator_run):
                            line_runs[-1] = line_runs[-1].append(piece.separator)
                        else:
                            line_runs.append(separator_run)
                        text = piece.text
                else:
                    text = piece.text
                if line_runs and line_runs[-1].same_style(run):
                    line_runs[-1] = line_runs[-1].append(text)
                else:
                    line_runs.append(run.with_text(text))
            if line_runs:
                lines.append(line_runs)
        return lines


@dataclass(frozen=True)
class InlineImage:
    """An inline image's resolved on-page box."""

    url: str
    alt: str
    width: float
    height: float


@dataclass
class TextRun:
    """A run of text with consistent styling, or one inline image."""

    text: str
    is_bold: bool = False
    is_italic: bool = False
    is_code: bool = False
    is_link: bool = False
    url: Optional[str] = None
    image: Optional[InlineImage] = None

    def same_style(self, other: TextRun) -> bool:
        """Check if another run has the same styling.

        An image run never merges with anything, including another image --
        each is its own ``<image>`` element.
        """
        return (
            self.image is None
            and other.image is None
            and self.is_bold == other.is_bold
            and self.is_italic == other.is_italic
            and self.is_code == other.is_code
            and self.is_link == other.is_link
            and self.url == other.url
        )

    def with_text(self, text: str) -> TextRun:
        """Create a copy with different text."""
        return replace(self, text=text)

    def append(self, text: str) -> TextRun:
        """Create a copy with appended text."""
        return self.with_text(self.text + text)


@dataclass(frozen=True)
class _Line:
    """One laid-out line: its runs, its baseline below its top, its height."""

    runs: tuple[TextRun, ...]
    ascent: float
    height: float


# Convenience functions


def render(
    markdown: str,
    width: float = 400,
    padding: float = 20,
    style: Optional[Style] = None,
    allow_raw_html: bool = False,
    font_path: str | None = None,
    mono_font_path: str | None = None,
    fonts: Optional[FontFaces] = None,
) -> str:
    """
    Render Markdown text to SVG.

    This is the main entry point for the library.

    Args:
        markdown: Markdown text to render.
        width: Width of the SVG in pixels.
        padding: Padding inside the SVG.
        style: Style configuration. Uses default if None.
        allow_raw_html: If True, render raw HTML blocks via foreignObject.
        font_path: Path to TTF/OTF font for precise text measurement.
        mono_font_path: Path to monospace TTF/OTF font for code measurement.
        fonts: The full set of font files to measure bold/italic/mono runs
                  against. Mutually exclusive with font_path/mono_font_path.

    Returns:
        SVG string.

    Example:
        >>> svg = render("# Hello World\\n\\nThis is **bold** text.")
        >>> with open("output.svg", "w") as f:
        ...     f.write(svg)
    """
    from .parser import parse

    blocks = parse(markdown)
    renderer = SVGRenderer(
        style=style,
        allow_raw_html=allow_raw_html,
        font_path=font_path,
        mono_font_path=mono_font_path,
        fonts=fonts,
    )
    return renderer.render(blocks, width=width, padding=padding)


def render_blocks(
    blocks: Document,
    width: float = 400,
    padding: float = 20,
    style: Optional[Style] = None,
    allow_raw_html: bool = False,
    font_path: str | None = None,
    mono_font_path: str | None = None,
    fonts: Optional[FontFaces] = None,
) -> str:
    """
    Render pre-parsed blocks to SVG.

    Args:
        blocks: Document AST to render.
        width: Width of the SVG in pixels.
        padding: Padding inside the SVG.
        style: Style configuration.
        allow_raw_html: If True, render raw HTML blocks via foreignObject.
        font_path: Path to TTF/OTF font for precise text measurement.
        mono_font_path: Path to monospace TTF/OTF font for code measurement.
        fonts: The full set of font files to measure bold/italic/mono runs
                  against. Mutually exclusive with font_path/mono_font_path.

    Returns:
        SVG string.
    """
    renderer = SVGRenderer(
        style=style,
        allow_raw_html=allow_raw_html,
        font_path=font_path,
        mono_font_path=mono_font_path,
        fonts=fonts,
    )
    return renderer.render(blocks, width=width, padding=padding)


def measure(
    markdown: str,
    width: float = 400,
    padding: float = 20,
    style: Optional[Style] = None,
    font_path: str | None = None,
    mono_font_path: str | None = None,
    fonts: Optional[FontFaces] = None,
) -> Size:
    """
    Measure the dimensions needed to render Markdown.

    Args:
        markdown: Markdown text to measure.
        width: Width constraint.
        padding: Padding inside the SVG.
        style: Style configuration.
        font_path: Path to TTF/OTF font for precise text measurement.
        mono_font_path: Path to monospace TTF/OTF font for code measurement.
        fonts: The full set of font files to measure bold/italic/mono runs
                  against. Mutually exclusive with font_path/mono_font_path.

    Returns:
        Size with width and height.

    Example:
        >>> size = measure("# Hello\\n\\nLong paragraph...")
        >>> print(f"Height needed: {size.height}px")
    """
    from .parser import parse

    blocks = parse(markdown)
    renderer = SVGRenderer(
        style=style,
        font_path=font_path,
        mono_font_path=mono_font_path,
        fonts=fonts,
    )
    return renderer.measure(blocks, width=width, padding=padding)


def render_content(
    markdown: str,
    width: float = 400,
    padding: float = 20,
    style: Optional[Style] = None,
    allow_raw_html: bool = False,
    font_path: str | None = None,
    mono_font_path: str | None = None,
    fonts: Optional[FontFaces] = None,
) -> RenderResult:
    """
    Render Markdown and return structured result with content and dimensions.

    Unlike render(), this returns the SVG content without the <svg> wrapper,
    along with the actual dimensions. This is useful when composing multiple
    mdsvg outputs into a larger SVG without needing regex-based extraction.

    Args:
        markdown: Markdown text to render.
        width: Width of the SVG in pixels.
        padding: Padding inside the SVG.
        style: Style configuration. Uses default if None.
        font_path: Path to TTF/OTF font for precise text measurement.
        mono_font_path: Path to monospace TTF/OTF font for code measurement.
        fonts: The full set of font files to measure bold/italic/mono runs
                  against. Mutually exclusive with font_path/mono_font_path.

    Returns:
        RenderResult with content (SVG elements without wrapper),
        width, and height.

    Example:
        >>> from mdsvg import render_content
        >>> result = render_content("# Hello World", width=400)
        >>> result.content  # SVG elements without <svg> wrapper
        >>> result.width    # 400.0
        >>> result.height   # Actual rendered height
        >>> result.to_svg() # Full SVG with wrapper (convenience method)

        # Embed in a larger SVG:
        >>> large_svg = f'''
        ... <svg xmlns="http://www.w3.org/2000/svg" width="800" height="600">
        ...   <g transform="translate(50, 100)">
        ...     {result.content}
        ...   </g>
        ... </svg>
        ... '''
    """
    from .parser import parse

    blocks = parse(markdown)
    renderer = SVGRenderer(
        style=style,
        allow_raw_html=allow_raw_html,
        font_path=font_path,
        mono_font_path=mono_font_path,
        fonts=fonts,
    )
    return renderer.render_content(blocks, width=width, padding=padding)
