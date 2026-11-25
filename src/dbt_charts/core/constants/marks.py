"""Mark engine constants: series-label rail geometry, stroke and point sizing."""

from __future__ import annotations

from typing import Final

# Largest share of the chart width the series-label rail may claim; must stay below 1.
ENDPOINT_LABEL_MAX_WIDTH_FRACTION: Final[float] = 0.33
# Largest share a rail label carrying a value may take (a fifth); past it the label wraps, then drops
# the value, so a narrow card keeps its plot.
ENDPOINT_LABEL_VALUE_MAX_WIDTH_FRACTION: Final[float] = 0.2
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

# A line palette hands out styles in plain slot order up to this many lines; from here on, lines that
# stay apart may share one, so a crowded chart keeps its variety.
LINE_STYLE_SHARE_FROM: Final[int] = 5
# Most lines one style may serve; only the last slot, the overflow, may exceed it.
LINE_STYLE_MAX_USES: Final[int] = 2
# Two lines may share a style only while their mean vertical distance stays at or above this.
LINE_STYLE_SHARE_MIN_GAP_PX: Final[float] = 40.0
