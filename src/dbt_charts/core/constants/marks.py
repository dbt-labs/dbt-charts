"""Mark engine constants: series-label rail geometry, stroke and point sizing."""

from typing import Final

# Largest share of the chart width the series-label rail may claim; must stay below 1.
ENDPOINT_LABEL_MAX_WIDTH_FRACTION: Final[float] = 0.33
# Separation between adjacent endpoint labels, as a multiple of the font size.
ENDPOINT_LABEL_LINE_HEIGHT_MULTIPLIER: Final[float] = 1.3

# Hairline floor of the density-adaptive stroke: below it a dense line stops reading as a trace.
STROKE_MIN_WIDTH: Final[float] = 1.5
# Ceiling of the density-adaptive stroke: above it a sparse line dominates the data it draws.
STROKE_MAX_WIDTH: Final[float] = 4.0
# Width when no tier pins one and no density signal exists (no data, no x field, non-cartesian).
STROKE_FALLBACK_WIDTH: Final[float] = 2.5
# Point diameter as a multiple of the line stroke; 1.0 hides in the round cap, 2.0 beads at density.
POINT_DIAMETER_RATIO: Final[float] = 1.7
# Points switch on at this many pixels per distinct x; below it they bead into a caterpillar.
POINT_MIN_PX_PER_POINT: Final[float] = 22.5

# Samples a fitted trend is drawn from; a data set with fewer distinct x values is drawn through them.
TREND_SAMPLES: Final[int] = 64
