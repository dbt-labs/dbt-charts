"""Layout threshold constants: facet panel width, plot-size floors, support table limits."""

from typing import Final

# Per-panel width below which a small-multiples chart warns; advisory, the card boundary still wins.
FACET_MIN_PANEL_PX: Final[float] = 120.0
# Share of the card's own height its plot must clear; calibrated against a grouped-bar sweep.
PLOT_HEIGHT_FLOOR_RATIO: Final[float] = 0.30
# Temporal or quantitative x axes with more ticks than this sample every Nth column so the header stays legible.
SUPPORT_TABLE_MAX_X_TICKS: Final[int] = 40
# Share of the card's width the plot must clear beside the column block; breaching it raises, as the plot would vanish.
SUPPORT_TABLE_PLOT_WIDTH_FLOOR_RATIO: Final[float] = 0.30
# Column block share of the block-plus-plot footprint above which a warning fires, before the hard floor raises.
SUPPORT_TABLE_COLUMN_BLOCK_SHARE_WARN_RATIO: Final[float] = 0.4
# Vega's own default for the gap between a title and its subtitle, which applies because the spec never sets subtitlePadding;
# the subtitle line is as tall as its font size because the spec never sets subtitleLineHeight either.
VEGA_SUBTITLE_PADDING: Final[float] = 3.0
# Vega axis config default for titlePadding, which applies when the theme leaves the padding unset.
VEGA_AXIS_TITLE_PADDING: Final[float] = 4.0
