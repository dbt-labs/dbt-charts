"""Style configuration for SVG rendering."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal, Optional

# Code block overflow options
CodeBlockOverflow = Literal["wrap", "show", "hide", "ellipsis"]

# Text alignment options
TextAlign = Literal["left", "center", "right"]


@dataclass(frozen=True)
class Style:
    """
    Configuration for styling rendered SVG output.

    All style properties are immutable. Use `with_updates()` to create
    a modified copy of a style.

    Attributes:
        font_family: Primary font stack for body text.
        mono_font_family: Font stack for code elements.
        base_font_size: Base font size in pixels.
        font_weight: Optional font weight for body text. ``None`` omits the
            CSS declaration and leaves SVG/browser default weight in effect.
        bold_font_weight: CSS font-weight applied to bold text runs (``**bold**``
            markdown spans and bold table cells) — independent of
            ``heading_font_weight``. Only the drawn weight; width measurement
            for bold runs still uses the fixed ``bold_char_width_ratio``
            regardless of this value, so a much lighter override (e.g. 300)
            keeps bold-weight column widths and wrap points.
        line_height: Line height multiplier.
        text_color: Default text color (CSS color value).
        heading_color: Color for headings (defaults to text_color if None).
        link_color: Color for links.
        link_underline: Whether to underline links.
        code_color: Color for inline code text.
        code_background: Background color for code elements.
        code_font_family: Font family for code text. Overrides ``mono_font_family`` when
            set; falls back to ``mono_font_family`` when empty.
        code_font_weight: CSS font-weight for code text (e.g. ``"700"``). Empty = default.
        code_font_style: CSS font-style for code text (``"italic"`` | ``"normal"``). Empty = default.
        code_font_size: Absolute font size in px for code text. ``0.0`` = size from
            ``code_font_scale`` instead.
        code_font_scale: Code size as a fraction of the text it sits in, used when
            ``code_font_size`` is unset. Inline code scales off its host block, so code
            in a heading grows with the heading; fenced blocks scale off
            ``base_font_size``. Rounded to the nearest half-pixel.
        code_font_decoration: CSS text-decoration for code text (``"underline"`` | ``"line-through"`` | ``"none"``). Empty = default.
        code_font_case: String transform for code text (``"upper"`` | ``"lower"``). Applied to the
            text string before emitting, NOT via CSS text-transform (unreliable in resvg/vl-convert).
        inline_code_padding: Padding in pixels around inline code chips (applied equally on all sides).
        inline_code_border_radius: Corner radius in pixels for inline code chip background rects.
        blockquote_color: Color for blockquote text.
        blockquote_border_color: Color for blockquote left border.
        blockquote_font_family: Font family for blockquote text. Empty = inherits body font.
        blockquote_font_weight: CSS font-weight for blockquote text. Empty = default.
        blockquote_font_style: CSS font-style for blockquote text. Empty = default.
        blockquote_font_size: Absolute font size in px for blockquote text. ``0.0`` = use ``base_font_size``.
        blockquote_font_decoration: CSS text-decoration for blockquote text. Empty = default.
        blockquote_font_case: String transform for blockquote text (``"upper"`` | ``"lower"``).
            Applied to the text string before emitting, NOT via CSS text-transform.
        h1_scale: Font size multiplier for h1 (used when h1_size is None).
        h2_scale: Font size multiplier for h2 (used when h2_size is None).
        h3_scale: Font size multiplier for h3 (used when h3_size is None).
        h4_scale: Font size multiplier for h4 (used when h4_size is None).
        h5_scale: Font size multiplier for h5 (used when h5_size is None).
        h6_scale: Font size multiplier for h6 (used when h6_size is None).
        h1_size: Absolute h1 font size in px. Overrides h1_scale × base_font_size.
        h2_size: Absolute h2 font size in px. Overrides h2_scale × base_font_size.
        h3_size: Absolute h3 font size in px. Overrides h3_scale × base_font_size.
        h4_size: Absolute h4 font size in px. Overrides h4_scale × base_font_size.
        h5_size: Absolute h5 font size in px. Overrides h5_scale × base_font_size.
        h6_size: Absolute h6 font size in px. Overrides h6_scale × base_font_size.
        heading_font_family: Font family for heading text. Empty = inherits body font.
        heading_font_weight: Font weight for headings.
        heading_line_height: Line height multiplier for headings; falls back
            to ``line_height`` when None. Headings typically want a tighter
            multiplier than body text (~1.1-1.25 vs 1.5-1.6).
        heading_margin_top: Top margin for headings (em units, scaled by the
            heading's own font size). Used when ``heading_margin_top_px`` is None.
        heading_margin_bottom: Bottom margin for headings (em units, scaled by
            the heading's own font size). Used when ``heading_margin_bottom_px``
            is None.
        heading_margin_top_px: Optional absolute-px top margin for headings.
            When set, overrides the em scaling. Useful for callers that want
            consistent pixel margins across heading levels (e.g. body-rhythm-
            derived journalism spacing where the after-heading gap is the same
            regardless of H level).
        heading_margin_bottom_px: Optional absolute-px bottom margin for headings.
            When set, overrides the em scaling.
        paragraph_spacing: Space between paragraphs in pixels.
        list_indent: Indentation for list items in pixels.
        list_item_spacing: Space between list items in pixels.
        code_block_padding: Padding inside code blocks in pixels.
        code_block_border_radius: Border radius for code blocks in pixels.
        code_highlight: Enable Pygments-driven per-token syntax highlighting for
            fenced code blocks. Opt-in; silently falls back to
            the single-fill renderer when Pygments isn't installed or the
            fence's language isn't recognized.
        code_theme: Pygments theme name used to color tokens when
            ``code_highlight`` is True (e.g. ``"monokai"``, ``"default"``,
            ``"github-dark"``). An unknown theme name raises
            ``pygments.util.ClassNotFound`` — that's a config error, not a
            fallback case.
        blockquote_padding: Left padding for blockquotes in pixels.
        blockquote_border_width: Width of blockquote left border in pixels.
        table_border_color: Color for table borders.
        table_header_background: Background color for table headers.
        table_cell_padding: Padding inside table cells in pixels.
        hr_color: Color for horizontal rules.
        hr_height: Height of horizontal rules in pixels.
        image_width: Fixed image width in pixels (None = full container width).
        image_height: Fixed image height in pixels (None = auto from aspect ratio).
        image_fallback_aspect_ratio: Aspect ratio when dimensions unknown (default 16:9).
        image_enforce_aspect_ratio: Skip fetching dimensions, always use fallback ratio.
        image_preserve_aspect_ratio: SVG preserveAspectRatio attribute (default "xMidYMid meet").
        char_width_ratio: Reference regular-text ratio used to normalize
            bold/italic scaling. Last-resort estimate: only used by
            ``SVGRenderer`` when the caller supplied a single ``font_path``
            (no ``fonts=FontFaces(...)``) and there is no real bold/italic
            font face to measure against.
        bold_char_width_ratio: Character width ratio for bold text.
            Last-resort estimate — see ``char_width_ratio``. Real bold font faces
            measure much closer to regular (~2-3% wider in the shipped Inter
            and Source Serif 4 variable fonts) than this ratio implies.
        italic_char_width_ratio: Character width ratio for italic text.
            Last-resort estimate — see ``char_width_ratio``. Deliberately
            *below* ``char_width_ratio``: italic glyphs are narrower than
            upright ones, not wider. Measured from the shipped variable
            fonts, italic is -13.85% for Source Serif 4 and +0.20% for
            Inter; the default here is their midpoint (~-6.8%).
        text_align: Horizontal text alignment ("left", "center", "right").
    """

    # Fonts
    font_family: str = (
        "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    )
    mono_font_family: str = "ui-monospace, 'SF Mono', Menlo, Consolas, monospace"
    base_font_size: float = 14.0
    font_weight: str | int | None = None
    bold_font_weight: str | int = "bold"
    avoid_runts: bool = False
    """Stop a paragraph ending on a lone short word by pulling one down.

    Off by default: changing where lines break changes every rendered output,
    and this wrapper is shared by every consumer of the package.
    """
    line_height: float = 1.5

    # Colors
    text_color: str = "#1a1a1a"
    heading_color: Optional[str] = None  # Falls back to text_color
    link_color: str = "#2563eb"
    link_underline: bool = True
    code_color: str = "#be185d"
    code_background: str = "#f3f4f6"
    code_block_border_color: str = (
        ""  # Empty = no stroke; set to CSS color to draw border
    )
    code_block_border_width: float = 1.0
    blockquote_color: str = "#6b7280"
    blockquote_border_color: str = "#d1d5db"
    blockquote_background: str = (
        ""  # Empty = no background rect; set to CSS color to fill
    )
    blockquote_border_radius: float = 0.0  # Corner radius of the background rect

    # Code font overrides (empty string = no override; falls back to mono_font_family / Style defaults)
    code_font_family: str = ""
    code_font_weight: str = ""
    code_font_style: str = ""  # "" | "italic" | "normal"
    code_font_size: float = 0.0  # 0.0 = no override; size from code_font_scale
    code_font_scale: float = 0.9
    code_font_decoration: str = ""  # "" | "underline" | "line-through" | "none"
    code_font_case: str = (
        ""  # "" | "upper" | "lower" (applied via string transform, NOT CSS)
    )

    # Inline code chip geometry (separate from fenced-code block tokens)
    inline_code_padding: float = 3.0  # px, equal on all sides
    inline_code_border_radius: float = 3.0  # px corner radius

    # Blockquote font overrides (empty string = no override)
    blockquote_font_family: str = ""
    blockquote_font_weight: str = ""
    blockquote_font_style: str = ""  # "" | "italic" | "normal"
    blockquote_font_size: float = 0.0  # 0.0 = no override; use base_font_size
    blockquote_font_decoration: str = ""  # "" | "underline" | "line-through" | "none"
    blockquote_font_case: str = (
        ""  # "" | "upper" | "lower" (applied via string transform, NOT CSS)
    )

    # Heading scales (multipliers of base_font_size)
    h1_scale: float = 2.0
    h2_scale: float = 1.6
    h3_scale: float = 1.35
    h4_scale: float = 1.15
    h5_scale: float = 1.0
    h6_scale: float = 0.9
    # Absolute per-level sizes (px). When set, override the scale × base_font_size
    # calculation. Lets a caller pin headings to an absolute type stack.
    h1_size: Optional[float] = None
    h2_size: Optional[float] = None
    h3_size: Optional[float] = None
    h4_size: Optional[float] = None
    h5_size: Optional[float] = None
    h6_size: Optional[float] = None
    # Empty string = no override; falls back to font_family. Mirrors
    # blockquote_font_family/code_font_family's override-or-inherit shape.
    heading_font_family: str = ""
    heading_font_weight: str | int = "bold"
    # Optional tighter line-height multiplier for headings; None falls back to
    # the body line_height. Body multipliers (1.5-1.6) read too loose between
    # wrapped heading lines.
    heading_line_height: Optional[float] = None
    heading_margin_top: float = 1.5  # em (overridden by heading_margin_top_px)
    heading_margin_bottom: float = 0.5  # em (overridden by heading_margin_bottom_px)
    heading_margin_top_px: Optional[float] = None
    heading_margin_bottom_px: Optional[float] = None

    # Spacing
    paragraph_spacing: float = 12.0
    list_indent: float = 18.0
    list_item_spacing: float = 1.0
    code_block_padding: float = 12.0
    code_block_border_radius: float = 4.0
    code_block_overflow: CodeBlockOverflow = "wrap"  # wrap, show, hide, ellipsis
    code_highlight: bool = False  # Pygments per-token coloring for fenced code
    code_theme: str = "monokai"  # Pygments theme name (used when code_highlight=True)
    blockquote_padding: float = 16.0
    blockquote_border_width: float = 3.0

    # Table
    table_border_color: str = "#e5e7eb"
    table_header_background: str = "#f9fafb"
    table_cell_padding: float = 8.0

    # Horizontal rule
    hr_color: str = "#e5e7eb"
    hr_height: float = 1.0

    # Images
    image_width: Optional[float] = None  # None = full width (100% of container)
    image_height: Optional[float] = (
        None  # None = auto (based on fetched/fallback ratio)
    )
    image_fallback_aspect_ratio: float = (
        16 / 9
    )  # Used when dimensions can't be detected
    image_enforce_aspect_ratio: bool = False  # Skip fetching, always use fallback ratio
    image_preserve_aspect_ratio: str = "xMidYMid meet"  # SVG preserveAspectRatio attr

    # Text measurement — last-resort estimates, only used when SVGRenderer has
    # no real font face to measure (see the field docstrings above).
    char_width_ratio: float = 0.48  # Reference ratio for bold/italic scaling
    bold_char_width_ratio: float = 0.58  # Wider for bold (~20% wider than regular)
    # Narrower for italic: midpoint of measured Source Serif 4 (-13.85%) and
    # Inter (+0.20%) variable-font italic deltas vs their own regular font face.
    italic_char_width_ratio: float = 0.447  # 0.48 * (1 - 0.068)
    # Text alignment
    text_align: TextAlign = "left"  # Horizontal alignment: "left", "center", "right"

    def with_updates(self, **kwargs: Any) -> Style:
        """
        Create a new Style with updated values.

        Args:
            **kwargs: Style properties to update.

        Returns:
            A new Style instance with the specified updates.

        Example:
            >>> style = Style()
            >>> dark_style = style.with_updates(
            ...     text_color="#ffffff",
            ...     code_background="#1f2937"
            ... )
        """
        return replace(self, **kwargs)

    def get_heading_scale(self, level: int) -> float:
        """
        Get the font size scale for a heading level.

        Args:
            level: Heading level (1-6).

        Returns:
            The font size multiplier for that heading level.
        """
        scales = {
            1: self.h1_scale,
            2: self.h2_scale,
            3: self.h3_scale,
            4: self.h4_scale,
            5: self.h5_scale,
            6: self.h6_scale,
        }
        return scales.get(level, 1.0)

    def get_heading_size(self, level: int) -> float:
        """
        Get the absolute font size in pixels for a heading level.

        If the corresponding ``h*_size`` field is set, return it directly.
        Otherwise fall back to ``base_font_size × get_heading_scale(level)``.

        Args:
            level: Heading level (1-6).

        Returns:
            Font size in pixels for that heading level.
        """
        sizes = {
            1: self.h1_size,
            2: self.h2_size,
            3: self.h3_size,
            4: self.h4_size,
            5: self.h5_size,
            6: self.h6_size,
        }
        absolute = sizes.get(level)
        if absolute is not None:
            return absolute
        return self.base_font_size * self.get_heading_scale(level)

    def get_heading_color(self) -> str:
        """Get the color for headings, falling back to text_color."""
        return self.heading_color or self.text_color

    def get_text_anchor(self) -> str:
        """Get the SVG text-anchor value corresponding to text_align."""
        anchor_map = {
            "left": "start",
            "center": "middle",
            "right": "end",
        }
        return anchor_map.get(self.text_align, "start")


# Pre-built themes
LIGHT_THEME = Style(code_theme="default")

DARK_THEME = Style(
    text_color="#e5e7eb",
    heading_color="#f9fafb",
    link_color="#60a5fa",
    code_color="#f472b6",
    code_background="#374151",
    blockquote_color="#9ca3af",
    blockquote_border_color="#4b5563",
    table_border_color="#4b5563",
    table_header_background="#374151",
    hr_color="#4b5563",
    code_theme="monokai",
)

GITHUB_THEME = Style(
    font_family="-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif",
    mono_font_family="ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace",
    base_font_size=16.0,
    text_color="#1f2328",
    heading_color="#1f2328",
    link_color="#0969da",
    code_color="#1f2328",
    code_background="#f6f8fa",
    blockquote_color="#59636e",
    blockquote_border_color="#d1d9e0",
    code_theme="default",
)


# Style presets for different use cases
# These control spacing/margins rather than colors/fonts


class StylePresets:
    """
    Pre-configured style presets optimized for different rendering contexts.

    Presets adjust spacing and margins while preserving color and font settings.
    Combine with themes for full customization:

        >>> from mdsvg import render, COMPACT_PRESET, DARK_THEME
        >>> style = COMPACT_PRESET.with_updates(**vars(DARK_THEME))

    Or use the merge_styles helper:

        >>> from mdsvg import merge_styles, COMPACT_PRESET, DARK_THEME
        >>> style = merge_styles(COMPACT_PRESET, DARK_THEME)
    """

    # Document preset: generous whitespace for long-form reading (original defaults)
    DOCUMENT = Style(
        heading_margin_top=1.5,  # 1.5em above headings
        heading_margin_bottom=0.5,  # 0.5em below headings
        paragraph_spacing=12.0,  # 12px between paragraphs
        list_item_spacing=4.0,  # 4px between list items
    )

    # Compact preset: tighter spacing for dashboards, cards, and UI components
    COMPACT = Style(
        heading_margin_top=0.3,  # Reduced from 1.5em
        heading_margin_bottom=0.3,  # Reduced from 0.5em
        paragraph_spacing=8.0,  # Reduced from 12px
        list_item_spacing=3.0,  # Slightly tighter list spacing
    )

    # Minimal preset: very tight spacing for tooltips and constrained spaces
    MINIMAL = Style(
        heading_margin_top=0.1,  # Minimal top margin
        heading_margin_bottom=0.1,  # Minimal bottom margin
        paragraph_spacing=4.0,  # Tight paragraph spacing
        list_item_spacing=2.0,  # Tight list spacing
    )


# Export presets as top-level constants for easy access
DOCUMENT_PRESET = StylePresets.DOCUMENT
COMPACT_PRESET = StylePresets.COMPACT
MINIMAL_PRESET = StylePresets.MINIMAL


def merge_styles(*styles: Style) -> Style:
    """
    Merge multiple Style objects, with later styles overriding earlier ones.

    This is useful for combining presets (spacing) with themes (colors):

        >>> from mdsvg import merge_styles, COMPACT_PRESET, DARK_THEME
        >>> style = merge_styles(COMPACT_PRESET, DARK_THEME)

    Only non-default values from each style are applied:

        >>> base = Style(text_color="red")
        >>> overlay = Style(link_color="blue")
        >>> merged = merge_styles(base, overlay)
        >>> merged.text_color
        'red'
        >>> merged.link_color
        'blue'

    Args:
        *styles: Style objects to merge, in order of priority (later wins).

    Returns:
        A new Style with merged values.
    """
    if not styles:
        return Style()

    # Start with default style
    default = Style()
    result_kwargs: dict[str, Any] = {}

    for style in styles:
        # Get all fields that differ from default
        for field_name in style.__dataclass_fields__:
            style_value = getattr(style, field_name)
            default_value = getattr(default, field_name)
            if style_value != default_value:
                result_kwargs[field_name] = style_value

    return Style(**result_kwargs)
