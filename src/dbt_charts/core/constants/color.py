"""Color derivation constants for ``variant()`` and ``label_ink()``."""

from typing import Final

# Every tier is L' = L + (pole - L) * k in OKLCH: hue held, chroma scaled, gamut-clipped.
# Fraction of the remaining distance to white the light tier moves.
LIGHT_K: Final[float] = 0.30
# Chroma multiplier of the light tier; keeps it from turning neon.
LIGHT_CHROMA: Final[float] = 0.60
# Lightness light keeps above its base even where the pale cap would pull it closer.
LIGHT_MIN_GAP: Final[float] = 0.03
# Gap light targets under the pale band when LIGHT_MIN_GAP does not win.
LIGHT_PALE_GAP: Final[float] = 0.03
# Lightness pole of the dark tier and of label ink on a light canvas.
DARK_POLE: Final[float] = 0.20
# Fraction of the distance to DARK_POLE that the dark tier and label ink's pre-floor step move.
DARK_K: Final[float] = 0.30
# Flat lightness of the pale tier; the band itself is the pole.
PALE_L: Final[float] = 0.86
# Chroma multiplier of the pale tier.
PALE_CHROMA: Final[float] = 0.32
# Lightness pole of the deep tier, further than DARK_POLE.
DEEP_POLE: Final[float] = 0.28
# Fraction of the distance to DEEP_POLE the deep tier moves.
DEEP_K: Final[float] = 0.85
