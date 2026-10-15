"""WCAG contrast floors. Accessibility minimums, never authorable."""

from typing import Final

# Floor label_ink guarantees against its canvas; above ~4.6 a mid-gray canvas cannot clear it.
LABEL_INK_MIN_CONTRAST: Final[float] = 4.5
# WCAG 2.1 AA floor for normal text, checked at render time (WARN-LOW-TEXT-CONTRAST).
TEXT_MIN_RATIO: Final[float] = 4.5
# WCAG large-text floor: >=24px at any weight, or >=18.67px (14pt) bold.
LARGE_TEXT_MIN_RATIO: Final[float] = 3.0
