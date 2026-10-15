"""Layout estimates: stand-ins for sizes the engine does not yet measure."""

from typing import Final

# Height of a spark_bar title block, before the subtitle line.
SPARK_BAR_TITLE_HEIGHT_PX: Final[float] = 24.0
# Baseline of a spark_bar title, measured from the top of its block.
SPARK_BAR_TITLE_BASELINE_Y_PX: Final[float] = 16.0

# Gutter for a small-multiples row-header label plus the measure axis.
FACET_CHROME_PX: Final[float] = 120.0
# Extra right-edge gutter when the measure axis is mirrored to the far edge.
FACET_MIRROR_AXIS_PX: Final[float] = 80.0

# Left reserve a single-row top legend loses to card and frame chrome; measured on a plain vertical bar (18px, rounded up).
LEGEND_MIN_RESERVE_PX: Final[float] = 20.0
# Chrome beyond the label text (tick length, label offset) on a horizontal bar's dimension axis; 65px total for 22.3px labels.
LEGEND_DIMENSION_AXIS_RESERVE_CHROME_PX: Final[float] = 43.0
# One wrapped legend row inside its total footprint at collapse; fitted by binary search over row counts 6 to 10.
LEGEND_ROW_HEIGHT_PX: Final[float] = 16.0
# Fixed title and padding chrome above a wrapped legend's rows; the other half of that fit.
LEGEND_CHROME_HEIGHT_PX: Final[float] = 47.0
# Marginal cost of one wrapped compact-legend row against the plot-height floor; not LEGEND_ROW_HEIGHT_PX, whose total already carries fixed chrome.
# Runs optimistic by +11.8px at 1 series down to +1.8px at 10.
COMPACT_LEGEND_ROW_PX: Final[float] = 21.0
# Flat height of a single-row top legend against the plot-height floor; measured at 2, 5, 16 and 25 series.
ROW_LEGEND_TOTAL_PX: Final[float] = 31.0

# Fixed chrome the plot never gets (object title and x-axis band); card padding is charged separately.
PLOT_HEIGHT_FLOOR_IRREDUCIBLE_PX: Final[float] = 52.0
