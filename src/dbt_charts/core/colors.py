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


def is_color_token(value: str) -> bool:
    """Return True if ``value`` is shaped like a palette token.

    Shape only — whether the token resolves needs the theme's palettes/roles,
    which live in the style cascade.
    """
    return bool(_COLOR_TOKEN_PATTERN.match(value))


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

    A readability floor for ink derived from an author-picked color that has
    no registered dark companion (``resolve_dark_companion_stops`` falls back
    to the bright color itself when a custom fill isn't found in any
    registered categorical palette — e.g. a sequential-gray override like
    ``dbt-seq-gray.2``), or a mark color that was never a palette member at
    all (e.g. a named border token). Binary-searches OKLCH lightness, moving
    toward WHICHEVER OF BLACK/WHITE HAS MORE CONTRAST against *background*,
    for the closest-to-original L that still clears ``min_ratio`` -- C and H
    stay fixed, so the result still reads as the same hue, just legible.
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
