"""Font-based text measurement for precise width calculations.

This module provides accurate text measurement using fonttools to read
actual glyph metrics from font files.

## Basic Usage

    from mdsvg.fonts import FontMeasurer, get_system_font

    # Use system font (auto-detected)
    measurer = FontMeasurer.system_default()
    width = measurer.measure("Hello World", font_size=14)

## Custom Fonts

You can use any TTF/OTF font file:

    measurer = FontMeasurer("/path/to/your/font.ttf")
    width = measurer.measure("Hello", 14)

### Where to put custom font files

Recommended locations:
- Project directory: `./fonts/MyFont.ttf`
- User fonts (macOS): `~/Library/Fonts/MyFont.ttf`
- User fonts (Linux): `~/.local/share/fonts/MyFont.ttf`
- User fonts (Windows): `C:\\Users\\<user>\\AppData\\Local\\Microsoft\\Windows\\Fonts\\`

### Google Fonts

Download fonts from Google Fonts automatically:

    from mdsvg.fonts import download_google_font, FontMeasurer

    font_path = download_google_font("Inter")
    measurer = FontMeasurer(font_path)

Or download manually from https://fonts.google.com and place in your project.
"""

from __future__ import annotations

import os
import platform
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional

# One glyph's width for an emoji cluster, however many codepoints compose it.
# Matches Noto Emoji's advance for the curated glyphs (2600/2048 units) -- a
# real, measured value, not a guess at an OS's color-emoji metrics.
_EMOJI_ADVANCE_EM = 1.27

# Default-emoji-presentation pictograph blocks: always render as a picture,
# no variation selector required.
_EMOJI_PICTOGRAPH_RANGES: tuple[tuple[int, int], ...] = (
    (0x1F300, 0x1F5FF),
    (0x1F600, 0x1F64F),
    (0x1F680, 0x1F6FF),
    (0x1F7E0, 0x1F7EB),  # colored status-dot squares/circles (🟠🟡🟢...)
    (0x1F900, 0x1F9FF),
    (0x1FA70, 0x1FAFF),
)

# Default-emoji-presentation singles that live outside the main pictograph
# blocks, in the BMP Miscellaneous Symbols/Dingbats/Arrows-and-Symbols blocks
# (2600-27BF, 2B00-2BFF) -- these render as a picture with no VS16 needed,
# unlike most of their block, which is text-presentation by default (below).
_EMOJI_DEFAULT_PRESENTATION_SINGLES = frozenset(
    {
        0x231A,
        0x231B,  # ⌚⌛
        0x23E9,
        0x23EA,
        0x23EB,
        0x23EC,
        0x23F0,
        0x23F3,
        0x25FD,
        0x25FE,  # ◽◾
        0x2614,
        0x2615,  # ☔☕
        *range(0x2648, 0x2654),  # ♈-♓ zodiac signs
        0x267F,  # ♿
        0x2693,  # ⚓
        0x26A1,  # ⚡
        0x26AA,
        0x26AB,  # ⚪⚫
        0x26BD,
        0x26BE,  # ⚽⚾
        0x26C4,
        0x26C5,  # ⛄⛅
        0x26CE,  # ⛎
        0x26D4,  # ⛔
        0x26EA,  # ⛪
        0x26F2,
        0x26F3,  # ⛲⛳
        0x26F5,  # ⛵
        0x26FA,  # ⛺
        0x26FD,  # ⛽
        0x2705,  # ✅
        0x270A,
        0x270B,  # ✊✋
        0x2728,  # ✨
        0x274C,  # ❌
        0x274E,  # ❎
        0x2753,
        0x2754,
        0x2755,  # ❓❔❕
        0x2757,  # ❗
        0x2795,
        0x2796,
        0x2797,  # ➕➖➗
        0x27B0,  # ➰
        0x27BF,  # ➿
        0x2B1B,
        0x2B1C,  # ⬛⬜
        0x2B50,  # ⭐
        0x2B55,  # ⭕
    }
)

# Text-presentation-by-default symbol blocks/codepoints: only render as a
# picture when immediately followed by U+FE0F (VS16) -- otherwise they're
# ordinary text symbols and fall through to the normal glyph-or-fallback path.
_EMOJI_TEXT_PRESENTATION_RANGES: tuple[tuple[int, int], ...] = (
    (0x2600, 0x27BF),
    (0x2B00, 0x2BFF),
)
# Individual codepoints outside those two blocks that are also text-presentation
# by default: the diagonal/horizontal arrows (2194-2199) and information source
# (2139, Letterlike Symbols block) -- ℹ️ is a real-world canonical VS16-forcing
# spelling, not a hypothetical one.
_EMOJI_TEXT_PRESENTATION_SINGLES = frozenset({0x2139, *range(0x2194, 0x219A)})

_REGIONAL_INDICATOR_START = 0x1F1E6
_REGIONAL_INDICATOR_END = 0x1F1FF
_SKIN_TONE_MODIFIER_START = 0x1F3FB
_SKIN_TONE_MODIFIER_END = 0x1F3FF
_ZERO_WIDTH_JOINER = 0x200D
_VARIATION_SELECTOR_15 = 0xFE0E
_VARIATION_SELECTOR_16 = 0xFE0F
_KEYCAP_MARK = 0x20E3
_KEYCAP_BASES = frozenset(range(0x30, 0x3A)) | {0x23, 0x2A}  # '0'-'9', '#', '*'


def _is_wide_east_asian(char: str) -> bool:
    """Whether ``char`` occupies a full em-square advance in a real
    CJK-capable font: Unicode East Asian Width "Wide" or "Fullwidth", and
    not a nonspacing/enclosing combining mark (category ``Mn``/``Me`` --
    see ``_is_zero_advance_wide_mark``). A *spacing* combining mark
    (category ``Mc``) does have a real, nonzero advance despite also being
    a combining mark, so it stays in the wide bucket.
    """
    return unicodedata.east_asian_width(char) in (
        "W",
        "F",
    ) and unicodedata.category(char) not in ("Mn", "Me")


def _is_zero_advance_wide_mark(char: str) -> bool:
    """Whether ``char`` is East Asian Wide/Fullwidth but renders at zero
    advance, stacked onto the preceding base character: the combining
    voiced/semi-voiced kana marks (U+3099, U+309A), four of the six
    ideographic/hangul tone marks (U+302A-U+302D), and U+16FE4. Booking a
    full em for one of these -- or even the generic placeholder -- would
    over-measure NFD-normalized text and let the wrapper split a line
    between a base character and its mark.
    """
    return unicodedata.east_asian_width(char) in ("W", "F") and unicodedata.category(
        char
    ) in ("Mn", "Me")


def _is_pictograph(codepoint: int) -> bool:
    return codepoint in _EMOJI_DEFAULT_PRESENTATION_SINGLES or any(
        start <= codepoint <= end for start, end in _EMOJI_PICTOGRAPH_RANGES
    )


def _is_text_presentation_symbol(codepoint: int) -> bool:
    return codepoint in _EMOJI_TEXT_PRESENTATION_SINGLES or any(
        start <= codepoint <= end for start, end in _EMOJI_TEXT_PRESENTATION_RANGES
    )


def _emoji_base_length(text: str, i: int) -> int:
    """Length of one emoji base glyph at ``i``: a pictograph, or a
    text-presentation symbol forced to emoji by a trailing VS16, plus any
    trailing VS16/skin-tone modifier. ``0`` if ``i`` isn't a base start, or if
    an explicit VS15 (text-presentation request) follows -- that suppresses
    the emoji cluster rather than extending it.

    A pictograph's own trailing VS16 must be absorbed here too, not just the
    text-presentation symbol's qualifying one: a ZWJ sequence whose first
    codepoint is a pictograph authored with a redundant-but-canonical VS16
    (e.g. the rainbow flag, eye-in-speech-bubble) would otherwise leave that
    VS16 orphaned, breaking the ZWJ-chain fold in ``_emoji_cluster_length``.
    """
    n = len(text)
    if i >= n:
        return 0
    codepoint = ord(text[i])
    if _is_pictograph(codepoint) or (
        _is_text_presentation_symbol(codepoint)
        and i + 1 < n
        and ord(text[i + 1]) == _VARIATION_SELECTOR_16
    ):
        length = 1
    else:
        return 0
    if i + length < n and ord(text[i + length]) == _VARIATION_SELECTOR_15:
        return 0
    if i + length < n and ord(text[i + length]) == _VARIATION_SELECTOR_16:
        length += 1
    if (
        i + length < n
        and _SKIN_TONE_MODIFIER_START
        <= ord(text[i + length])
        <= _SKIN_TONE_MODIFIER_END
    ):
        length += 1
    return length


def _emoji_cluster_length(text: str, i: int) -> int:
    """Codepoint count of the emoji cluster starting at ``i``, or ``0``.

    Recognizes a regional-indicator flag pair, a keycap sequence
    (digit/``#``/``*`` + optional VS16 + U+20E3), or a pictograph/VS16-forced
    base with any ZWJ-chained emoji bases and their own modifiers folded into
    the same single-unit cluster (e.g. the 3-codepoint "man technologist" ZWJ
    sequence is one cluster, not three).
    """
    n = len(text)
    if i >= n:
        return 0
    codepoint = ord(text[i])

    # Every recognized range lives above U+2000 except the keycap bases
    # (ASCII digits/#/*) -- an early out here skips two range scans per
    # codepoint for ordinary Latin prose, the overwhelmingly common case.
    if codepoint < 0x2000 and codepoint not in _KEYCAP_BASES:
        return 0

    if _REGIONAL_INDICATOR_START <= codepoint <= _REGIONAL_INDICATOR_END:
        if (
            i + 1 < n
            and _REGIONAL_INDICATOR_START <= ord(text[i + 1]) <= _REGIONAL_INDICATOR_END
        ):
            return 2
        return 0

    if codepoint in _KEYCAP_BASES:
        j = i + 1
        if j < n and ord(text[j]) == _VARIATION_SELECTOR_16:
            j += 1
        if j < n and ord(text[j]) == _KEYCAP_MARK:
            return j + 1 - i
        return 0

    length = _emoji_base_length(text, i)
    if length == 0:
        return 0

    while i + length < n and ord(text[i + length]) == _ZERO_WIDTH_JOINER:
        next_base = _emoji_base_length(text, i + length + 1)
        if next_base == 0:
            break
        length += 1 + next_base

    return length


@dataclass(frozen=True)
class FontFace:
    """One concrete font file, optionally instanced at a variable-font weight."""

    path: str
    weight: float | None = None
    font_number: int = 0


@dataclass(frozen=True)
class FontFaces:
    """The set of files a caller paints with, so each can be measured honestly.

    Passed to ``SVGRenderer(fonts=...)`` instead of a bare ``font_path`` so
    bold/italic/mono runs measure against the real font face that will be
    painted, rather than a ratio-scaled guess from the regular font face.
    ``heading`` is the font face a plain (neither bold nor italic) heading run
    is *horizontally measured* against, whenever supplied — the SVG itself
    always paints a heading via ``Style.heading_font_family`` (renderer-side,
    independent of this dataclass); ``heading`` exists so that measurement
    agrees with that paint instead of drifting from it. ``None`` measures
    headings from ``regular`` like every other run, matching a heading with
    no family override of its own. Vertical placement (baseline, line
    height) still reads ``regular``'s own metrics for every run including
    headings — a real, narrower gap than the horizontal one this field
    closes, invisible unless a heading's family carries markedly different
    ascent/descent from the body face.
    """

    regular: FontFace
    bold: FontFace | None = None
    italic: FontFace | None = None
    bold_italic: FontFace | None = None
    mono: FontFace | None = None
    heading: FontFace | None = None


@dataclass
class FontMeasurer:
    """
    Measure text width using actual font metrics via fonttools.

    Example:
        >>> measurer = FontMeasurer("/System/Library/Fonts/Helvetica.ttc")
        >>> measurer.measure("Hello World", 14)
        72.4
    """

    font_path: str
    font_number: int = 0  # For .ttc files with multiple fonts
    weight: float | None = None  # Variable-font weight (requires 'fvar')
    _cmap: Optional[Dict[int, str]] = field(default=None, init=False, repr=False)
    _hmtx: Optional[Any] = field(default=None, init=False, repr=False)
    _units_per_em: int = field(default=1000, init=False, repr=False)
    _available: bool = field(default=False, init=False, repr=False)
    _advance_deltas: Optional[Dict[str, float]] = field(
        default=None, init=False, repr=False
    )
    _supports_weight: bool = field(default=False, init=False, repr=False)
    # hhea ascent/descent in font units; the properties below scale them to em.
    _ascent_raw: int = field(default=0, init=False, repr=False)
    _descent_raw: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self._init_font()

    def _init_font(self) -> None:
        """Load font metrics from the font file.

        When ``weight`` is set, the font must have an ``fvar`` table (i.e. be
        a variable font) — that is a caller error, not a case to degrade
        silently, so it raises rather than falling back to the font's default
        weight.
        """
        try:
            from fontTools.ttLib import (  # pyright: ignore[reportMissingTypeStubs]
                TTFont,
                TTLibError,
            )
        except ImportError:
            # fontTools not installed — measurement unavailable.
            self._available = False
            return
        try:
            font = TTFont(self.font_path, fontNumber=self.font_number)
            self._supports_weight = "fvar" in font and any(
                axis.axisTag == "wght" for axis in font["fvar"].axes
            )
            if self.weight is not None:
                if "fvar" not in font:
                    raise ValueError(
                        f"FontMeasurer(weight={self.weight}) requires a variable "
                        f"font with an 'fvar' table; {self.font_path!r} has none."
                    )
                self._load_advance_deltas(font, self.weight)
            self._cmap = font.getBestCmap()
            self._hmtx = font["hmtx"]
            # fonttools table attrs are dynamic (__getattr__); no upstream stubs
            head = font["head"]
            upm = head.unitsPerEm  # pyright: ignore[reportAttributeAccessIssue]
            self._units_per_em = upm
            hhea = font["hhea"]
            self._ascent_raw = hhea.ascent  # pyright: ignore[reportAttributeAccessIssue]
            self._descent_raw = hhea.descent  # pyright: ignore[reportAttributeAccessIssue]
            self._available = True
        except (FileNotFoundError, OSError, TTLibError):
            # Font file not found, unreadable, or invalid. A file that loads but
            # is missing a mandatory table raises KeyError instead and is not
            # caught: that is malformed input, not a font to measure at zero.
            self._available = False

    def _load_advance_deltas(self, font: Any, weight: float) -> None:
        """Build per-glyph advance deltas for ``weight`` from the ``HVAR`` table.

        Measuring only needs horizontal advances at a weight, and ``HVAR`` stores
        exactly those deltas. Reading them costs a few milliseconds; the obvious
        alternative — ``instantiateVariableFont`` — rebuilds ``glyf``/``gvar``/
        ``GPOS``/``avar``/``STAT`` to produce the same numbers and measured 200x
        slower here (0.2-0.8s per font file, seconds per render once bold and italic
        are included). It is also what shapers actually do, and it skips the
        instancer's rounding of advances to whole font units.

        Falls back to full instancing for a variable font with no ``HVAR`` — rare,
        but such a font has no other machine-readable source for the deltas.
        """
        # Lazy imports: this machinery is only needed for variable fonts, and the
        # common static-font path should not pay to import it.
        from fontTools.varLib.models import (  # pyright: ignore[reportMissingTypeStubs]
            normalizeValue,
            piecewiseLinearMap,
        )

        fvar = font["fvar"]
        axes = {a.axisTag: (a.minValue, a.defaultValue, a.maxValue) for a in fvar.axes}
        if "wght" not in axes:
            raise ValueError(
                f"FontMeasurer(weight={weight}) requires a 'wght' axis; "
                f"{self.font_path!r} has {sorted(axes)}."
            )

        if "HVAR" not in font:
            from fontTools.varLib.instancer import (  # pyright: ignore[reportMissingTypeStubs]
                instantiateVariableFont,
            )

            instantiateVariableFont(font, {"wght": weight}, inplace=True)
            return

        from fontTools.varLib.varStore import (  # pyright: ignore[reportMissingTypeStubs]
            VarStoreInstancer,
        )

        location = {"wght": normalizeValue(weight, axes["wght"])}
        # avar remaps the normalized axis position before the deltas are looked
        # up. Skipping it silently misplaces every weight except the default.
        avar = font.get("avar")
        if avar is not None:
            for tag, segment in avar.segments.items():
                if tag in location and segment:
                    location[tag] = piecewiseLinearMap(location[tag], segment)

        hvar = font["HVAR"].table
        varstore = VarStoreInstancer(hvar.VarStore, fvar.axes, location)
        mapping = hvar.AdvWidthMap.mapping if hvar.AdvWidthMap is not None else None
        glyph_order = font.getGlyphOrder()

        deltas: Dict[str, float] = {}
        for index, glyph in enumerate(glyph_order):
            store_index = mapping[glyph] if mapping is not None else index
            deltas[glyph] = varstore[store_index]
        self._advance_deltas = deltas

    def measure(self, text: str, font_size: float) -> float:
        """
        Measure the width of text in pixels.

        An emoji cluster (a pictograph, a VS16-forced symbol, a ZWJ sequence,
        a flag, or a keycap) books a fixed emoji-advance width regardless of
        whether the font has a real glyph for any of its codepoints -- these
        are authored as pictures, not measured as ordinary text. An unmapped
        East Asian Wide/Fullwidth codepoint (CJK ideographs, kana, hangul,
        fullwidth punctuation) books a full em-square advance, matching how
        CJK-capable fonts actually measure it -- except the small set of
        Wide/Fullwidth combining marks that stack onto their base character
        at zero advance (see ``_is_zero_advance_wide_mark``). Any other
        unmapped codepoint keeps the placeholder (space-like) fallback width.

        Args:
            text: The text to measure.
            font_size: Font size in pixels.

        Returns:
            Width in pixels.

        Raises:
            RuntimeError: If fonttools is not available.
        """
        if not text:
            return 0.0

        if not self._available:
            raise RuntimeError(
                "FontMeasurer not available. Install fonttools: pip install fonttools"
            )

        deltas = self._advance_deltas
        total_width: float = 0
        i = 0
        n = len(text)
        while i < n:
            cluster_length = _emoji_cluster_length(text, i)
            if cluster_length:
                # Books one emoji-glyph advance for the whole cluster rather
                # than falling through per-codepoint below. A handful of these
                # codepoints do have a real (non-emoji) glyph in some measured
                # fonts -- deliberately overridden here rather than checked,
                # since a codepoint in these ranges is authored as a picture.
                total_width += self._units_per_em * _EMOJI_ADVANCE_EM
                i += cluster_length
                continue

            char = text[i]
            glyph_id = self._cmap.get(ord(char)) if self._cmap else None
            if glyph_id and self._hmtx and glyph_id in self._hmtx.metrics:
                advance_width, _ = self._hmtx.metrics[glyph_id]
                total_width += advance_width
                if deltas is not None:
                    total_width += deltas.get(glyph_id, 0.0)
            elif _is_wide_east_asian(char):
                # Fallback for unknown East Asian Wide/Fullwidth glyphs: a
                # full em-square, not the space-like placeholder below.
                total_width += self._units_per_em
            elif _is_zero_advance_wide_mark(char):
                pass  # stacks onto the preceding base glyph; books nothing
            else:
                # Fallback for unknown glyphs (space-like width)
                total_width += self._units_per_em * 0.25
            i += 1

        return (total_width / self._units_per_em) * font_size

    def has_glyph(self, char: str) -> bool:
        """Whether ``char`` maps to a real, measurable glyph in this font.

        ``measure()`` degrades an unmapped codepoint to a placeholder
        (space-like) width rather than raising — the right behavior for
        ordinary prose, where blocking a whole render over one rare
        character would be worse than an estimate. (An unmapped emoji
        codepoint instead books a fixed emoji-advance width, and an unmapped
        East Asian Wide/Fullwidth codepoint books a full em-square advance —
        see ``measure()``'s own docstring.) A caller composing a layout out
        of *specific* codepoints chosen for their exact advance needs the
        opposite answer: whether the font can actually back that assumption,
        not a width that looks plausible but isn't real.
        """
        if not char or not self._available:
            return False
        glyph_id = self._cmap.get(ord(char)) if self._cmap else None
        return bool(glyph_id and self._hmtx and glyph_id in self._hmtx.metrics)

    @property
    def is_available(self) -> bool:
        """Check if font measurement is available."""
        return self._available

    @property
    def supports_weight(self) -> bool:
        """Whether this file can be measured at a caller-chosen weight.

        True only for a variable font with a ``wght`` axis. Lets a caller ask for a
        weight where the font can honor it and keep the default elsewhere, rather
        than constructing a ``FontMeasurer(weight=...)`` and catching the raise.
        """
        return self._supports_weight

    @property
    def ascent_em(self) -> float:
        """Ascender height above the baseline, as a fraction of em.

        With :attr:`descent_em` this is the face's content box — the height a
        line of it occupies before any leading. A line box taller than the two
        has leading to split above and below the text; one shorter has the
        glyphs overflowing it, which is normal at a tight line height and is
        why neither value is clamped here.
        """
        self._require_vertical_metrics()
        return self._ascent_raw / self._units_per_em

    @property
    def descent_em(self) -> float:
        """Descender depth below the baseline, as a positive fraction of em.

        ``hhea.descent`` is negative in a well-formed font — down is negative in
        the Y-up font coordinate system — and is returned here as a magnitude, so
        a caller computing ``baseline + descent_em * size`` needs to know nothing
        about that convention.
        """
        self._require_vertical_metrics()
        return abs(self._descent_raw) / self._units_per_em

    def _require_vertical_metrics(self) -> None:
        if not self._available:
            raise RuntimeError(
                f"Font {self.font_path!r} did not load; it has no vertical metrics "
                "to read. Check `is_available` before asking for them."
            )

    @classmethod
    def system_default(cls) -> Optional[FontMeasurer]:
        """
        Create a FontMeasurer using the system default font.

        Returns:
            FontMeasurer if a system font is found and fonttools is available,
            None otherwise.
        """
        font_path = get_system_font()
        if font_path:
            measurer = cls(font_path)
            if measurer.is_available:
                return measurer
        return None


@dataclass(frozen=True)
class WrapPiece:
    """A measured word/token plus the separator that may precede it."""

    text: str
    width: float
    separator: str = ""
    separator_width: float = 0.0
    meta: Any = None


def get_system_font() -> Optional[str]:
    """
    Find a system font that can be used for measurement.

    Looks for common sans-serif fonts that match typical "system-ui" rendering.

    Returns:
        Path to a font file, or None if not found.
    """
    system = platform.system()

    if system == "Darwin":  # macOS
        candidates = [
            "/System/Library/Fonts/SFNS.ttf",
            "/System/Library/Fonts/SFNSText.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ]
    elif system == "Windows":
        windir = os.environ.get("WINDIR", "C:\\Windows")
        candidates = [
            os.path.join(windir, "Fonts", "segoeui.ttf"),
            os.path.join(windir, "Fonts", "arial.ttf"),
            os.path.join(windir, "Fonts", "calibri.ttf"),
        ]
    else:  # Linux
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
        ]

    for path in candidates:
        if os.path.exists(path):
            return path

    return None


def get_system_mono_font() -> Optional[str]:
    """Find a system monospace font for inline-code measurement."""
    system = platform.system()

    if system == "Darwin":  # macOS
        candidates = [
            "/System/Library/Fonts/SFNSMono.ttf",
            "/System/Library/Fonts/SFMono-Regular.otf",
            "/System/Library/Fonts/Menlo.ttc",
            "/System/Library/Fonts/Supplemental/Menlo.ttc",
            "/Library/Fonts/Courier New.ttf",
        ]
    elif system == "Windows":
        windir = os.environ.get("WINDIR", "C:\\Windows")
        candidates = [
            os.path.join(windir, "Fonts", "consola.ttf"),
            os.path.join(windir, "Fonts", "cour.ttf"),
        ]
    else:  # Linux
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        ]

    for path in candidates:
        if os.path.exists(path):
            return path

    return None


@lru_cache(maxsize=32)
def _cached_measurer(
    font_path: str, font_number: int = 0, weight: float | None = None
) -> FontMeasurer:
    """Return a process-global cached FontMeasurer for *(font_path, font_number, weight)*.

    FontMeasurer is read-only after __post_init__, so sharing a single
    instance across many SVGRenderer calls (the common dbt charts pattern)
    is safe and avoids repeated TTF CMAP decompile overhead. Weight is part
    of the key because instancing a variable font at a different weight
    produces different glyph metrics from the same file.
    """
    return FontMeasurer(font_path, font_number, weight)


@lru_cache(maxsize=1)
def get_default_measurer() -> Optional[FontMeasurer]:
    """Get a cached FontMeasurer using the system default font."""
    font_path = get_system_font()
    if font_path is None:
        return None
    measurer = _cached_measurer(font_path)
    return measurer if measurer.is_available else None


def measure_text_precise(
    text: str,
    font_size: float,
    measurer: FontMeasurer,
) -> float:
    """Measure text with a real FontMeasurer."""
    if not measurer.is_available:
        raise RuntimeError(
            "Precise font measurer is required for strict wrapping/truncation"
        )
    return float(measurer.measure(text, font_size))


_SEAM_CHARS = frozenset("_/.-?&=:")


def _find_seam_break(
    token: str, max_width: float, measure: Callable[[str], float]
) -> int | None:
    """Return the index after the longest seam-char-terminated prefix that fits max_width.

    Scans left-to-right collecting the last seam position whose prefix fits, so
    the first acceptable position is always the longest one.  Returns None when
    no seam-terminated prefix fits within max_width.
    """
    best: int | None = None
    for i, ch in enumerate(token):
        if ch in _SEAM_CHARS and i > 0 and measure(token[: i + 1]) <= max_width:
            best = i + 1
    return best


def split_token_precise(
    token: str,
    max_width: float,
    measure: Callable[[str], float],
) -> list[str]:
    """Split a long token into pieces that fit within max_width.

    Tries seam characters first (``_/.-?&=:``) then falls back to a
    binary-search character break for tokens with no seam that fits.

    Args:
        token: The token to split.
        max_width: Maximum width of each piece.
        measure: Width measurement function.
    """
    if not token:
        return [""]
    if measure(token) <= max_width:
        return [token]

    pieces: list[str] = []
    remaining = token
    while remaining:
        if measure(remaining) <= max_width:
            pieces.append(remaining)
            break
        seam_pos = _find_seam_break(remaining, max_width, measure)
        if seam_pos is not None:
            pieces.append(remaining[:seam_pos])
            remaining = remaining[seam_pos:]
        else:
            # No seam fits — fall back to binary-search character break
            lo, hi = 1, len(remaining)
            best = 1
            while lo <= hi:
                mid = (lo + hi) // 2
                candidate = remaining[:mid]
                if measure(candidate) <= max_width:
                    best = mid
                    lo = mid + 1
                else:
                    hi = mid - 1
            pieces.append(remaining[:best])
            remaining = remaining[best:]
    return pieces


def _line_width(line: list[WrapPiece]) -> float:
    return sum(p.width for p in line) + sum(p.separator_width for p in line[1:])


def wrap_measured_pieces(
    pieces: list[WrapPiece],
    max_width: float,
    avoid_runts: bool = False,
) -> list[list[WrapPiece]]:
    """Wrap already-measured pieces into lines.

    With ``avoid_runts``, a final line left holding one piece pulls a piece down
    from the line above, so a paragraph does not end on a lone short word. The
    pull is skipped when it would only relocate the problem -- if the line above
    has nothing to spare, or the moved piece would not fit.

    Off by default: this wrapper is shared by every consumer of the package, and
    changing where lines break changes every rendered output.
    """
    if not pieces:
        return []

    lines: list[list[WrapPiece]] = [[]]
    current_width = 0.0
    for piece in pieces:
        candidate_width = piece.width
        if lines[-1]:
            candidate_width += piece.separator_width
        if lines[-1] and current_width + candidate_width > max_width:
            lines.append([piece])
            current_width = piece.width
        else:
            lines[-1].append(piece)
            current_width += candidate_width

    lines = [line for line in lines if line]

    if avoid_runts and len(lines) >= 2 and len(lines[-1]) == 1:
        penultimate, last = lines[-2], lines[-1]
        if len(penultimate) >= 2:
            moved = penultimate[-1]
            candidate = [moved, *last]
            if _line_width(candidate) <= max_width:
                lines[-2] = penultimate[:-1]
                lines[-1] = candidate

    return lines


def pieces_to_text(pieces: list[WrapPiece]) -> str:
    """Join measured pieces back into text."""
    rendered: list[str] = []
    for index, piece in enumerate(pieces):
        if index > 0 and piece.separator:
            rendered.append(piece.separator)
        rendered.append(piece.text)
    return "".join(rendered)


def truncate_text_precise(
    text: str,
    max_width: float,
    font_size: float,
    measurer: FontMeasurer,
    *,
    ellipsis: bool,
    normalize_whitespace: bool = True,
) -> str:
    """Clip or ellipsize text to fit within max_width using precise measurement."""
    if normalize_whitespace:
        text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return ""

    def measure(value: str) -> float:
        return measure_text_precise(value, font_size, measurer)

    if measure(text) <= max_width:
        return text

    suffix = "…" if ellipsis else ""
    lo, hi = 0, len(text)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = text[:mid].rstrip() + suffix
        if measure(candidate) <= max_width:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    if best:
        return best
    return text[:1]


def wrap_text_precise(
    text: str,
    max_width: float,
    font_size: float,
    measurer: FontMeasurer,
    *,
    max_lines: int | None = None,
    ellipsis: bool = False,
    normalize_whitespace: bool = True,
) -> tuple[list[str], bool]:
    """Wrap plain text using precise measurement only.

    Returns ``(lines, truncated)`` where ``truncated`` is ``True`` when
    ``max_lines`` was set and the natural wrap produced more lines than
    ``max_lines`` — meaning some text was cut.  The truncation signal is
    independent of whether the last line ends with an ellipsis character;
    authored text that already contains ``…`` never sets this flag.
    """
    if normalize_whitespace:
        text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return [], False

    def measure(value: str) -> float:
        return measure_text_precise(value, font_size, measurer)

    space_width = measure(" ")
    pieces: list[WrapPiece] = []
    words = text.split(" ")
    for word_index, word in enumerate(words):
        for chunk_index, chunk in enumerate(
            split_token_precise(word, max_width, measure)
        ):
            separator = " " if word_index > 0 and chunk_index == 0 else ""
            separator_width = space_width if separator else 0.0
            pieces.append(
                WrapPiece(
                    text=chunk,
                    width=measure(chunk),
                    separator=separator,
                    separator_width=separator_width,
                )
            )

    lines = wrap_measured_pieces(pieces, max_width)
    if max_lines is not None and len(lines) > max_lines:
        tail: list[WrapPiece] = []
        for line in lines[max_lines - 1 :]:
            tail.extend(line)
        rendered = [pieces_to_text(line) for line in lines[: max_lines - 1]]
        rendered.append(
            truncate_text_precise(
                pieces_to_text(tail),
                max_width,
                font_size,
                measurer,
                ellipsis=ellipsis,
                normalize_whitespace=False,
            )
        )
        return rendered, True
    return [pieces_to_text(line) for line in lines], False


def create_precise_wrapper(
    max_width: float,
    font_size: float,
    measurer: FontMeasurer | None = None,
) -> Callable[[str], list[str]]:
    """
    Create a text wrapper function that uses precise font measurement.

    Args:
        max_width: Maximum line width in pixels.
        font_size: Font size in pixels.
        measurer: FontMeasurer to use (auto-detects if None).

    Returns:
        A function that takes text and returns list of wrapped lines.
    """
    if measurer is None:
        measurer = get_default_measurer()

    if measurer is None or not measurer.is_available:
        raise RuntimeError(
            "FontMeasurer is required for precise wrapping; no heuristic fallback is allowed"
        )

    def wrap_precise(text: str) -> list[str]:
        lines, _ = wrap_text_precise(text, max_width, font_size, measurer)
        return lines if lines else [""]

    return wrap_precise


def get_font_cache_dir() -> str:
    """
    Get the directory for caching downloaded fonts.

    Creates the directory if it doesn't exist.

    Returns:
        Path to the font cache directory.
    """
    # Use platform-appropriate cache directory
    system = platform.system()

    if system == "Darwin":
        cache_base = os.path.expanduser("~/Library/Caches")
    elif system == "Windows":
        cache_base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    else:
        cache_base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))

    cache_dir = os.path.join(cache_base, "mdsvg", "fonts")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def download_google_font(
    font_name: str,
    weight: int = 400,
    cache_dir: Optional[str] = None,
) -> str:
    """
    Download a font from Google Fonts.

    Downloads the font file and caches it locally. Subsequent calls
    return the cached file.

    Args:
        font_name: Name of the font (e.g., "Inter", "Roboto", "Open Sans").
        weight: Font weight (100-900). Default 400 (regular).
        cache_dir: Directory to cache fonts. Uses system cache if None.

    Returns:
        Path to the downloaded font file.

    Raises:
        RuntimeError: If download fails or font not found.

    Example:
        >>> font_path = download_google_font("Inter")
        >>> measurer = FontMeasurer(font_path)
        >>> measurer.measure("Hello", 14)

        >>> # With specific weight
        >>> bold_path = download_google_font("Inter", weight=700)
    """
    import re
    import urllib.error
    import urllib.request

    if cache_dir is None:
        cache_dir = get_font_cache_dir()

    # Normalize font name for filename
    safe_name = re.sub(r"[^a-zA-Z0-9]", "", font_name)
    font_filename = f"{safe_name}-{weight}.ttf"
    font_path = os.path.join(cache_dir, font_filename)

    # Return cached font if exists
    if os.path.exists(font_path):
        return font_path

    # Google Fonts CSS API URL
    css_url = f"https://fonts.googleapis.com/css2?family={font_name.replace(' ', '+')}:wght@{weight}"

    try:
        # Fetch CSS to get the actual font URL
        # Use a browser-like User-Agent to get TTF instead of WOFF2
        request = urllib.request.Request(
            css_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            css = response.read().decode("utf-8")

        # Extract font URL from CSS
        # Looking for: src: url(https://fonts.gstatic.com/...) format('truetype')
        match = re.search(r"src:\s*url\(([^)]+\.ttf)\)", css)
        if not match:
            # Try woff2 and convert note
            match = re.search(r"src:\s*url\(([^)]+)\)", css)
            if match:
                raise RuntimeError(
                    f"Font '{font_name}' only available as WOFF2. "
                    f"Download TTF manually from https://fonts.google.com/specimen/{font_name.replace(' ', '+')}"
                )
            raise RuntimeError(f"Could not find font URL for '{font_name}'")

        font_url = match.group(1)

        # Download the font file
        with urllib.request.urlopen(font_url, timeout=60) as response:
            font_data = response.read()

        # Save to cache
        with open(font_path, "wb") as f:
            f.write(font_data)

        return font_path

    except urllib.error.HTTPError as e:
        if e.code == 400:
            raise RuntimeError(
                f"Font '{font_name}' not found on Google Fonts. "
                f"Check spelling at https://fonts.google.com"
            ) from e
        raise RuntimeError(f"Failed to download font: {e}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error downloading font: {e}") from e


def list_cached_fonts(cache_dir: Optional[str] = None) -> List[str]:
    """
    List all fonts in the cache directory.

    Args:
        cache_dir: Cache directory to list. Uses system cache if None.

    Returns:
        List of cached font file paths.
    """
    if cache_dir is None:
        cache_dir = get_font_cache_dir()

    if not os.path.exists(cache_dir):
        return []

    return [
        os.path.join(cache_dir, f)
        for f in os.listdir(cache_dir)
        if f.endswith((".ttf", ".otf", ".ttc"))
    ]
