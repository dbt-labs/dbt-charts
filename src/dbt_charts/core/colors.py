"""CSS/SVG color-string validation and sRGB/OKLCH color math.

Lives in core (below compile/execute/render) so both compile (pie-attachment
table style resolution, palette dark-companion/contrast math) and render
(KPI/table SVG emission, support_table header ink) can validate or transform
a foreign color string without either reaching into the other's layer.
"""

from __future__ import annotations

import math
import re
from typing import overload

from dbt_charts.core.diagnostics.base import DbtChartsError

_CSS_HEX_COLOR_PATTERN = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# Palette token: "<palette-name>.<slot>" (dotted — "dbt-grays.gray-30",
# "negative.solid", "vivid-10.1", absolute palette.slot or role.alias) or
# "<role>[N]" (bracket — "category[2]", 1-indexed role slot). The leading
# character must be a letter so hex literals (#abcdef), CSS rgb()/rgba()
# values, decimal numerics like "0.5", and quoted font-family lists never
# match.
#
# Contract: any string anywhere in Style that matches this is treated as a
# palette token. If a future non-color string field could match (none today),
# it must be excluded from the cascade walk or the field reshaped.
_COLOR_TOKEN_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+|\[\d+\])$")


class InvalidColorError(DbtChartsError):
    """A color string is neither a valid hex color nor a recognized CSS keyword."""

    def __init__(self, message: str):
        self.message = message
        self.fields = {}
        super().__init__(message)
        if self.code is None:
            from dbt_charts.core.diagnostics.codes_unknown import ERR_INTERNAL

            self.code = ERR_INTERNAL


# The full CSS Color Module / SVG 1.1 extended keyword set, minus
# "transparent" -- that name means "no fill", not a color, and has no hex.
# A superset of _CSS_NAMED_COLORS (compile/models/primitives.py), a
# narrower table scoped to extracting a keyword from a border-shorthand
# string, not the full legal vocabulary for a Color()-faceted field like
# style.background.
_CSS_NAMED_COLOR_HEX: dict[str, str] = {
    "aliceblue": "#f0f8ff",
    "antiquewhite": "#faebd7",
    "aqua": "#00ffff",
    "aquamarine": "#7fffd4",
    "azure": "#f0ffff",
    "beige": "#f5f5dc",
    "bisque": "#ffe4c4",
    "black": "#000000",
    "blanchedalmond": "#ffebcd",
    "blue": "#0000ff",
    "blueviolet": "#8a2be2",
    "brown": "#a52a2a",
    "burlywood": "#deb887",
    "cadetblue": "#5f9ea0",
    "chartreuse": "#7fff00",
    "chocolate": "#d2691e",
    "coral": "#ff7f50",
    "cornflowerblue": "#6495ed",
    "cornsilk": "#fff8dc",
    "crimson": "#dc143c",
    "cyan": "#00ffff",
    "darkblue": "#00008b",
    "darkcyan": "#008b8b",
    "darkgoldenrod": "#b8860b",
    "darkgray": "#a9a9a9",
    "darkgreen": "#006400",
    "darkgrey": "#a9a9a9",  # codespell:ignore grey
    "darkkhaki": "#bdb76b",
    "darkmagenta": "#8b008b",
    "darkolivegreen": "#556b2f",
    "darkorange": "#ff8c00",
    "darkorchid": "#9932cc",
    "darkred": "#8b0000",
    "darksalmon": "#e9967a",
    "darkseagreen": "#8fbc8f",
    "darkslateblue": "#483d8b",
    "darkslategray": "#2f4f4f",
    "darkslategrey": "#2f4f4f",  # codespell:ignore grey
    "darkturquoise": "#00ced1",
    "darkviolet": "#9400d3",
    "deeppink": "#ff1493",
    "deepskyblue": "#00bfff",
    "dimgray": "#696969",
    "dimgrey": "#696969",  # codespell:ignore grey
    "dodgerblue": "#1e90ff",
    "firebrick": "#b22222",
    "floralwhite": "#fffaf0",
    "forestgreen": "#228b22",
    "fuchsia": "#ff00ff",
    "gainsboro": "#dcdcdc",
    "ghostwhite": "#f8f8ff",
    "gold": "#ffd700",
    "goldenrod": "#daa520",
    "gray": "#808080",
    "grey": "#808080",  # codespell:ignore grey
    "green": "#008000",
    "greenyellow": "#adff2f",
    "honeydew": "#f0fff0",
    "hotpink": "#ff69b4",
    "indianred": "#cd5c5c",
    "indigo": "#4b0082",
    "ivory": "#fffff0",
    "khaki": "#f0e68c",
    "lavender": "#e6e6fa",
    "lavenderblush": "#fff0f5",
    "lawngreen": "#7cfc00",
    "lemonchiffon": "#fffacd",
    "lightblue": "#add8e6",
    "lightcoral": "#f08080",
    "lightcyan": "#e0ffff",
    "lightgoldenrodyellow": "#fafad2",
    "lightgray": "#d3d3d3",
    "lightgreen": "#90ee90",
    "lightgrey": "#d3d3d3",  # codespell:ignore grey
    "lightpink": "#ffb6c1",
    "lightsalmon": "#ffa07a",
    "lightseagreen": "#20b2aa",
    "lightskyblue": "#87cefa",
    "lightslategray": "#778899",
    "lightslategrey": "#778899",  # codespell:ignore grey
    "lightsteelblue": "#b0c4de",
    "lightyellow": "#ffffe0",
    "lime": "#00ff00",
    "limegreen": "#32cd32",
    "linen": "#faf0e6",
    "magenta": "#ff00ff",
    "maroon": "#800000",
    "mediumaquamarine": "#66cdaa",
    "mediumblue": "#0000cd",
    "mediumorchid": "#ba55d3",
    "mediumpurple": "#9370db",
    "mediumseagreen": "#3cb371",
    "mediumslateblue": "#7b68ee",
    "mediumspringgreen": "#00fa9a",
    "mediumturquoise": "#48d1cc",
    "mediumvioletred": "#c71585",
    "midnightblue": "#191970",
    "mintcream": "#f5fffa",
    "mistyrose": "#ffe4e1",
    "moccasin": "#ffe4b5",
    "navajowhite": "#ffdead",
    "navy": "#000080",
    "oldlace": "#fdf5e6",
    "olive": "#808000",
    "olivedrab": "#6b8e23",
    "orange": "#ffa500",
    "orangered": "#ff4500",
    "orchid": "#da70d6",
    "palegoldenrod": "#eee8aa",
    "palegreen": "#98fb98",
    "paleturquoise": "#afeeee",
    "palevioletred": "#db7093",
    "papayawhip": "#ffefd5",
    "peachpuff": "#ffdab9",
    "peru": "#cd853f",
    "pink": "#ffc0cb",
    "plum": "#dda0dd",
    "powderblue": "#b0e0e6",
    "purple": "#800080",
    "rebeccapurple": "#663399",
    "red": "#ff0000",
    "rosybrown": "#bc8f8f",
    "royalblue": "#4169e1",
    "saddlebrown": "#8b4513",
    "salmon": "#fa8072",
    "sandybrown": "#f4a460",
    "seagreen": "#2e8b57",
    "seashell": "#fff5ee",
    "sienna": "#a0522d",
    "silver": "#c0c0c0",
    "skyblue": "#87ceeb",
    "slateblue": "#6a5acd",
    "slategray": "#708090",
    "slategrey": "#708090",  # codespell:ignore grey
    "snow": "#fffafa",
    "springgreen": "#00ff7f",
    "steelblue": "#4682b4",
    "tan": "#d2b48c",
    "teal": "#008080",
    "thistle": "#d8bfd8",
    "tomato": "#ff6347",
    "turquoise": "#40e0d0",
    "violet": "#ee82ee",
    "wheat": "#f5deb3",
    "white": "#ffffff",
    "whitesmoke": "#f5f5f5",
    "yellow": "#ffff00",
    "yellowgreen": "#9acd32",
}


def css_named_color_to_hex(name: str) -> str | None:
    """Hex for a CSS keyword color, or None when ``name`` isn't one.

    Case-insensitive. Covers the full CSS Color Module / SVG 1.1 extended
    keyword set, minus ``transparent`` -- that keyword means "no fill" and
    has no hex; ``parse_css_color`` special-cases it itself, before this
    function is ever consulted.
    """
    return _CSS_NAMED_COLOR_HEX.get(name.lower())


_HEX_ALPHA_COLOR_PATTERN = re.compile(
    r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$"
)
# rgb(...)/rgba(...) and hsl(...)/hsla(...): comma-separated
# ("rgb(10, 20, 30)", "hsla(220, 60%, 50%, 0.5)") or the modern
# space-separated form with a slash-delimited alpha ("rgb(10 20 30 / 50%)",
# "hsl(220 60% 50% / 50%)"). Channels and alpha both accept a percentage.
_COLOR_FUNCTION_PATTERN = re.compile(r"^(rgba?|hsla?)\(\s*(.+?)\s*\)$", re.IGNORECASE)


def _finite_float(token: str) -> float:
    """``float(token)``, rejecting nan/inf/-inf/infinity.

    ``float()`` parses all four without raising -- ``min(1.0, nan)`` then
    silently clamps a NaN channel to 1.0 (pure red from ``rgb(nan,0,0)``,
    not an error), and a NaN that survives into ``rgb01_to_hex``'s
    ``int(x * 255)`` raises an unlocated ``ValueError`` far from the
    authored value that caused it. Every numeric token in this grammar --
    an rgb() channel, an alpha, an hsl() hue -- routes through here first,
    so a non-finite value fails at the one seam that can name it.
    """
    value = float(token)
    if not math.isfinite(value):
        raise ValueError(f"not a finite number: {token!r}")
    return value


def _parse_channel_0_1(token: str) -> float:
    """One rgb() channel -- 0-255 integer or a percentage -- to 0-1, clamped."""
    token = token.strip()
    value = (
        _finite_float(token[:-1]) / 100.0
        if token.endswith("%")
        else _finite_float(token) / 255.0
    )
    return max(0.0, min(1.0, value))


def _parse_unit_interval(token: str) -> float:
    """An alpha channel -- a bare 0-1 float or a percentage -- to 0-1, clamped."""
    token = token.strip()
    value = (
        _finite_float(token[:-1]) / 100.0
        if token.endswith("%")
        else _finite_float(token)
    )
    return max(0.0, min(1.0, value))


def _parse_percentage(token: str) -> float:
    """An hsl() saturation/lightness channel -- a percentage ONLY -- to 0-1.

    Unlike alpha, CSS never accepts a bare number here: a browser reads
    ``hsl(220, 60, 50%)`` as invalid, not as 60 meaning 0.6 -- a bare
    number raises rather than silently being read as a fraction.
    """
    token = token.strip()
    if not token.endswith("%"):
        raise ValueError(f"not a percentage: {token!r}")
    return max(0.0, min(1.0, _finite_float(token[:-1]) / 100.0))


def _split_function_channels(body: str) -> tuple[list[str], str | None]:
    """Split a color function's parenthesized body into (channels, alpha).

    Shared by rgb()/rgba() and hsl()/hsla(): both accept 3 channels plus an
    optional alpha, in exactly one of two syntaxes -- comma-separated with
    alpha as a trailing 4th comma-separated argument
    (``"10, 20, 30, 0.5"``), or space-separated with alpha after a ``/``
    (``"10 20 30 / 50%"``). The two never mix. Raises ``ValueError`` (with
    the specific reason) on a malformed or mixed body.
    """
    if "," in body:
        if "/" in body:
            raise ValueError(f"comma syntax cannot carry a slash alpha: {body!r}")
        parts = [p.strip() for p in body.split(",")]
        if any(not p for p in parts) or len(parts) not in (3, 4):
            raise ValueError(f"expected 3 or 4 comma-separated values: {body!r}")
        return (parts[:3], parts[3]) if len(parts) == 4 else (parts, None)
    if "/" in body:
        channels_part, alpha_part = body.split("/", 1)
        alpha_token: str | None = alpha_part.strip()
        if not alpha_token:
            raise ValueError(f"empty alpha after slash: {body!r}")
    else:
        channels_part, alpha_token = body, None
    parts = [p for p in re.split(r"\s+", channels_part.strip()) if p]
    if len(parts) != 3:
        raise ValueError(f"expected 3 space-separated channels: {body!r}")
    return parts, alpha_token


def _hsl_to_rgb01(
    h_deg: float, s: float, lightness: float
) -> tuple[float, float, float]:
    """Standard HSL-to-sRGB conversion. ``h_deg`` in degrees; ``s``/``lightness`` 0-1."""
    h = h_deg % 360.0
    c = (1.0 - abs(2.0 * lightness - 1.0)) * s
    x = c * (1.0 - abs((h / 60.0) % 2.0 - 1.0))
    m = lightness - c / 2.0
    if h < 60:
        rp, gp, bp = c, x, 0.0
    elif h < 120:
        rp, gp, bp = x, c, 0.0
    elif h < 180:
        rp, gp, bp = 0.0, c, x
    elif h < 240:
        rp, gp, bp = 0.0, x, c
    elif h < 300:
        rp, gp, bp = x, 0.0, c
    else:
        rp, gp, bp = c, 0.0, x
    return (rp + m, gp + m, bp + m)


def parse_css_color(value: str) -> tuple[float, float, float, float]:
    """Parse a CSS/SVG color string to (r, g, b, a), each 0-1.

    Accepts hex (``#rgb``, ``#rgba``, ``#rrggbb``, ``#rrggbbaa``), a CSS
    keyword name (``css_named_color_to_hex``), ``transparent``/``none``
    (alpha 0), ``rgb()``/``rgba()`` with integer or percentage channels, and
    ``hsl()``/``hsla()`` with a degree hue and percentage saturation/
    lightness -- all four functions accept a 0-1 or percentage alpha,
    comma- or space-separated (``rgb(10 20 30 / 50%)``,
    ``hsl(220 60% 50% / 50%)``). Raises ``InvalidColorError`` on anything
    else -- a color the engine cannot read is a real authoring defect, not
    a case for a silent guess.
    """
    stripped = value.strip()
    lowered = stripped.lower()
    if lowered in {"transparent", "none"}:
        return (0.0, 0.0, 0.0, 0.0)

    hex_match = _HEX_ALPHA_COLOR_PATTERN.match(stripped)
    if hex_match:
        digits = hex_match.group(1)
        if len(digits) in (3, 4):
            digits = "".join(d * 2 for d in digits)
        r = int(digits[0:2], 16) / 255.0
        g = int(digits[2:4], 16) / 255.0
        b = int(digits[4:6], 16) / 255.0
        a = int(digits[6:8], 16) / 255.0 if len(digits) == 8 else 1.0
        return (r, g, b, a)

    named_hex = css_named_color_to_hex(stripped)
    if named_hex is not None:
        r = int(named_hex[1:3], 16) / 255.0
        g = int(named_hex[3:5], 16) / 255.0
        b = int(named_hex[5:7], 16) / 255.0
        return (r, g, b, 1.0)

    func_match = _COLOR_FUNCTION_PATTERN.match(stripped)
    function_reason: str | None = None
    if func_match:
        fn = func_match.group(1).lower()
        try:
            channels, alpha_token = _split_function_channels(func_match.group(2))
            a = _parse_unit_interval(alpha_token) if alpha_token is not None else 1.0
            if fn.startswith("rgb"):
                r, g, b = (_parse_channel_0_1(c) for c in channels)
            else:
                hue_token, sat_token, light_token = channels
                # CSS units are case-insensitive ("220DEG" is as legal
                # as "220deg"); strip the suffix on a lowered copy so
                # the numeric literal's own case is untouched.
                h_deg = _finite_float(
                    hue_token[:-3] if hue_token.lower().endswith("deg") else hue_token
                )
                s = _parse_percentage(sat_token)
                lightness = _parse_percentage(light_token)
                r, g, b = _hsl_to_rgb01(h_deg, s, lightness)
        except ValueError as e:
            # The function shape matched (rgb(...)/hsl(...)), so a deeper
            # grammar failure here has a specific, actionable reason --
            # a malformed channel split, a non-finite number, an hsl()
            # percentage missing its `%` -- worth keeping instead of
            # discarding it for the generic message below.
            function_reason = str(e)
        else:
            return (r, g, b, a)

    if function_reason is not None:
        raise InvalidColorError(f"Invalid color value: {value!r} ({function_reason})")
    raise InvalidColorError(f"Invalid color value: {value!r}")


def composite_over(
    top: tuple[float, float, float, float], under: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    """Standard source-over alpha compositing: paint ``top`` over ``under``.

    ``a_out = a_top + a_under * (1 - a_top)``; each output channel is the
    alpha-weighted blend of the two inputs, un-premultiplied by ``a_out``.
    Both colors are (r, g, b, a) tuples, each component 0-1. An output
    alpha of 0 (both layers fully transparent) returns black at alpha 0 --
    there is no color to report, only "nothing painted here."
    """
    rt, gt, bt, at = top
    ru, gu, bu, au = under
    a_out = at + au * (1.0 - at)
    if a_out <= 0.0:
        return (0.0, 0.0, 0.0, 0.0)
    under_weight = au * (1.0 - at)
    r_out = (rt * at + ru * under_weight) / a_out
    g_out = (gt * at + gu * under_weight) / a_out
    b_out = (bt * at + bu * under_weight) / a_out
    return (r_out, g_out, b_out, a_out)


def rgb01_to_hex(r: float, g: float, b: float) -> str:
    """(r, g, b), each 0-1, to a lowercase 6-digit hex string, clamped."""

    def _byte(c: float) -> int:
        return max(0, min(255, round(c * 255)))

    return f"#{_byte(r):02x}{_byte(g):02x}{_byte(b):02x}"


def is_color_token(value: str) -> bool:
    """Return True if ``value`` is shaped like a palette token.

    Shape only — whether the token resolves needs the theme's palettes/roles,
    which live in the style cascade.
    """
    return bool(_COLOR_TOKEN_PATTERN.match(value))


# A bare word with no dot/bracket suffix -- distinct from is_color_token's
# dotted/bracket shape ("category[2]", "chrome.ink"). Both shapes can be an
# unresolved theme-role reference at board/chart scope ("category", a role
# name authored as a bare string, matches neither a hex literal nor a CSS
# keyword by pattern alone).
_BARE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


# Reserved CSS keywords that are bare-identifier-shaped (matching
# _BARE_IDENTIFIER_PATTERN, exactly like a role name or a typo would) but are
# never a candidate role: a browser reserves these, so they can only ever be
# an unreadable color, same as oklch()/var(--x). Deferring them the way a
# genuine role candidate defers would route them to ERR-PALETTE-UNKNOWN
# ("unknown role") instead of the parse-failure fallback every other
# unreadable color takes -- a different, wrong reason.
_CSS_RESERVED_UNREADABLE_KEYWORDS = frozenset(
    {"currentcolor", "inherit", "initial", "unset", "revert"}
)


def is_deferred_color_reference(value: str) -> bool:
    """True when ``value`` is shaped like a possible theme-role reference
    (a color token, or a bare identifier) rather than literal color data.

    Shape only, same contract as ``is_color_token``: whether the reference
    actually resolves needs the theme's ``palettes:``/``roles:`` maps, which
    live in the style cascade. Shared by ``mark_ink()``
    (``compile/resolve/style/palette.py``) and the palette-list validator
    (``compile/resolve/style/tokens.py``) -- both need to tell a candidate
    role name/typo apart from literal, already-parseable color data.

    A CSS-reserved keyword (``currentColor``, ``inherit``, ...) matches the
    bare-identifier shape too, but is never a role candidate -- it is a
    definite, unreadable color, not a name that might resolve later.
    """
    if value.strip().lower() in _CSS_RESERVED_UNREADABLE_KEYWORDS:
        return False
    return is_color_token(value) or bool(_BARE_IDENTIFIER_PATTERN.match(value))


def is_sanitizable_color(color: str) -> bool:
    """Return True if ``color`` is accepted by sanitize_color (hex or transparent/none)."""
    return bool(_CSS_HEX_COLOR_PATTERN.match(color)) or color.lower() in {
        "transparent",
        "none",
    }


@overload
def sanitize_color(color: str | None, fallback: str) -> str: ...


@overload
def sanitize_color(color: str | None, fallback: None = ...) -> str | None: ...


def sanitize_color(color: str | None, fallback: str | None = None) -> str | None:
    """Validate and sanitize a color value for safe use in SVG/HTML.

    Accepts hex colors (``#fff``, ``#FFFFFF``) and the CSS keywords
    ``transparent`` and ``none``.  Presets use ``transparent`` to
    explicitly clear an inherited theme color (YAML ``null`` means
    "no override" in the merge system, so it doesn't disable an
    inherited fill).

    ``fallback`` is returned when ``color`` is None or empty.  A non-None
    ``color`` that is not a valid hex or keyword raises ``InvalidColorError``
    immediately — authored colors that are not valid are a configuration error,
    not a case for silent defaulting.
    """
    if not color:
        return fallback
    if _CSS_HEX_COLOR_PATTERN.match(color):
        return color
    if color.lower() in {"transparent", "none"}:
        return "transparent"
    raise InvalidColorError(f"Invalid color value: {color!r}")


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    if c <= 0.0:
        return 0.0
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def _cbrt(x: float) -> float:
    return x ** (1 / 3) if x >= 0 else -((-x) ** (1 / 3))


def _lrgb_to_oklab(r: float, g: float, b: float) -> tuple[float, float, float]:
    lo = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    lo, m, s = _cbrt(lo), _cbrt(m), _cbrt(s)
    return (
        0.2104542553 * lo + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * lo - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * lo + 0.7827717662 * m - 0.8086757660 * s,
    )


def _oklab_to_lrgb(L: float, a: float, b: float) -> tuple[float, float, float]:
    lo = L + 0.3963377774 * a + 0.2158037573 * b
    m = L - 0.1055613458 * a - 0.0638541728 * b
    s = L - 0.0894841775 * a - 1.2914855480 * b
    lo, m, s = lo**3, m**3, s**3
    return (
        +4.0767416621 * lo - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * lo + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * lo - 0.7034186147 * m + 1.7076147010 * s,
    )


def hex_to_oklch(hex_str: str) -> tuple[float, float, float]:
    """Convert an sRGB hex color to OKLCH (lightness, chroma, hue-degrees)."""
    h = hex_str.lstrip("#")
    r = int(h[0:2], 16) / 255
    g = int(h[2:4], 16) / 255
    b = int(h[4:6], 16) / 255
    rl, gl, bl = _srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b)
    L, a, bb = _lrgb_to_oklab(rl, gl, bl)
    C = math.sqrt(a * a + bb * bb)
    H = math.degrees(math.atan2(bb, a)) % 360
    return (L, C, H)


def oklch_to_hex(L: float, C: float, H: float) -> str:
    """Convert OKLCH to sRGB hex, gamut-clipping via binary search on C."""
    if not 0.0 <= L <= 1.0:
        raise ValueError(f"OKLCH lightness {L} is outside [0, 1]")

    def _try_c(cc: float) -> tuple[float, float, float] | None:
        a = cc * math.cos(math.radians(H))
        b = cc * math.sin(math.radians(H))
        r, g, bb = _oklab_to_lrgb(L, a, b)
        # Loose bounds tolerate floating-point overshoot; clamp to [0,1] on accept.
        if -1e-5 <= r <= 1.0001 and -1e-5 <= g <= 1.0001 and -1e-5 <= bb <= 1.0001:
            return (
                max(0.0, min(1.0, r)),
                max(0.0, min(1.0, g)),
                max(0.0, min(1.0, bb)),
            )
        return None

    result = _try_c(C)
    if result is None:
        lo, hi = 0.0, C
        # 30 iterations → precision ~C/2^30 ≈ 1e-9 in C, well below 8-bit rounding.
        for _ in range(30):
            mid = (lo + hi) / 2
            if _try_c(mid):
                lo = mid
            else:
                hi = mid
        # _try_c(lo) is unreachable-None: lo starts at chroma 0, which keeps
        # the OKLCH point within sRGB gamut for every L in [0, 1] (guarded
        # above). The fallback only satisfies the type checker.
        result = _try_c(lo) or (
            0.0,
            0.0,
            0.0,
        )  # type-state: silent_fallback — see comment above
    r, g, b = result
    rs = max(0.0, min(1.0, _linear_to_srgb(r)))
    gs = max(0.0, min(1.0, _linear_to_srgb(g)))
    bs = max(0.0, min(1.0, _linear_to_srgb(b)))
    return f"#{int(round(rs * 255)):02x}{int(round(gs * 255)):02x}{int(round(bs * 255)):02x}"


def relative_luminance(hex_str: str) -> float:
    """WCAG 2.1 relative luminance of an sRGB hex color."""
    h = hex_str.lstrip("#")
    r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
    rl, gl, bl = _srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b)
    return 0.2126 * rl + 0.7152 * gl + 0.0722 * bl


def wcag_contrast(hex_a: str, hex_b: str) -> float:
    """WCAG 2.1 contrast ratio between two sRGB hex colors."""
    l1, l2 = relative_luminance(hex_a), relative_luminance(hex_b)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


def is_light_canvas(canvas: str) -> bool:
    """True when black ink has more contrast against `canvas` than white does.

    The crossover is WCAG relative luminance ~0.18, not 0.5: WCAG relative
    luminance and contrast are both nonlinear, so black and white give equal
    contrast well below the midpoint. Any caller that has to pick an ink
    direction from a canvas color (``ensure_readable_ink``, ``label_ink``)
    needs this predicate rather than a luminance-vs-0.5 test -- see
    ``ensure_readable_ink``'s docstring for the failure a 0.5 pivot causes.
    """
    return wcag_contrast("#000000", canvas) >= wcag_contrast("#ffffff", canvas)


def ensure_readable_ink(color: str, background: str, min_ratio: float = 4.5) -> str:
    """Nudge *color* toward legible against *background*, hue/chroma preserved.

    A readability floor for ink derived from an author-picked color that
    ``label_ink()``'s dark-move alone doesn't clear -- e.g. a sequential-gray
    override like ``dbt-seq-gray.2``, or a mark color that was never a
    palette member at all (e.g. a named border token). Binary-searches OKLCH
    lightness, moving toward WHICHEVER OF BLACK/WHITE HAS MORE CONTRAST
    against *background*, for the closest-to-original L that still clears
    ``min_ratio`` -- C and H stay fixed, so the result still reads as the
    same hue, just legible.
    Returns *color* unchanged when it already clears the floor.

    Picking the higher-contrast endpoint (rather than pivoting on the
    background's own luminance at the 0.5 midpoint) is load-bearing: the
    luminance at which black and white give EQUAL contrast is ≈0.18, not
    0.5 (WCAG relative luminance and contrast are both nonlinear) -- a
    background between those two thresholds would otherwise search toward
    the LOWER-contrast endpoint and return ink that still fails the floor.

    Raises ``ValueError`` when neither black nor white can clear
    ``min_ratio`` against *background* -- the same "can't be met, don't
    return a failing color" contract ``_table_surface_seq``
    (``compile/resolve/style/palette.py``) already follows for its own WCAG
    floor.
    """
    if wcag_contrast(color, background) >= min_ratio:
        return color
    black_contrast = wcag_contrast("#000000", background)
    white_contrast = wcag_contrast("#ffffff", background)
    if max(black_contrast, white_contrast) < min_ratio:
        raise ValueError(
            f"no ink clears {min_ratio}:1 contrast against background "
            f"{background!r} (best achievable: "
            f"{max(black_contrast, white_contrast):.2f}:1)"
        )
    L, C, H = hex_to_oklch(color)
    pass_l, fail_l = (0.0, L) if is_light_canvas(background) else (1.0, L)
    for _ in range(30):
        mid = (pass_l + fail_l) / 2
        if wcag_contrast(oklch_to_hex(mid, C, H), background) >= min_ratio:
            pass_l = mid
        else:
            fail_l = mid
    return oklch_to_hex(pass_l, C, H)
