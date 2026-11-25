"""WCAG contrast floors. Accessibility minimums, never authorable."""

from __future__ import annotations

from typing import Final

# Floor label_ink guarantees against its canvas; above ~4.6 a mid-gray canvas cannot clear it.
LABEL_INK_MIN_CONTRAST: Final[float] = 4.5
# WCAG 2.1 AA floor for normal text, checked at render time (WARN-LOW-TEXT-CONTRAST).
TEXT_MIN_RATIO: Final[float] = 4.5
# WCAG large-text floor: >=24px at any weight, or >=18.67px (14pt) bold.
LARGE_TEXT_MIN_RATIO: Final[float] = 3.0
# Floor for a dark series name painted inside its own band: on a mid-tone band no ink in the
# band's hue clears 4.5 (even white reaches 4.2 on a mid green), only near-black does.
SERIES_NAME_DARK_INK_MIN_CONTRAST: Final[float] = 3.0
# How far past its band's own chroma a dark band name's ink may go; the gamut-maximal ink glows.
SERIES_NAME_DARK_INK_CHROMA_STRETCH: Final[float] = 1.5
# A dark band leaves room for a light name to sit paler and quieter than a dark name does on a
# mid band: a higher floor, and half the band's chroma so the name does not glow.
SERIES_NAME_LIGHT_INK_MIN_CONTRAST: Final[float] = 4.0
SERIES_NAME_LIGHT_INK_CHROMA_STRETCH: Final[float] = 0.5
# Chrome steps an `auto` annotation rule may take: the ladder's two pale and two
# near-black ends are never eligible.
RULE_GRAY_FIRST_STEP: Final[int] = 3
RULE_GRAY_LAST_STEP: Final[int] = 10
