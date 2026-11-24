"""Pattern engine constants: the narrow-bar tile sizes of a ladder palette."""

from __future__ import annotations

from typing import Final

# A ladder palette (`pattern-one-ink`) draws one tile size per chart, chosen from the
# chart's bar thickness: the full tile from this many px, the three-quarter tile from
# the next constant, and the half tile below it.
PATTERN_FULL_TILE_MIN_PX: Final[float] = 20.0
PATTERN_THREE_QUARTER_TILE_MIN_PX: Final[float] = 12.0
