"""Bar chart engine constants: hover band, label fit, horizontal rail spacing."""

from typing import Final

# Hover band applies only to a bar or total under this fraction of the largest value.
HOVER_BAND_TRIGGER_FRACTION: Final[float] = 0.02
# Fraction of the plot's pixel extent the hover band extends by once triggered.
HOVER_BAND_EXTEND_FRACTION: Final[float] = 0.05
# Room one value label needs in its segment, as a multiple of the font size.
LABEL_FIT_LINE_HEIGHT_MULTIPLIER: Final[float] = 1.15
# Minimum gap between adjacent top-rail labels, in word-spaces; crowded rails fall back to a legend.
TOP_RAIL_LABEL_GAP_SPACES: Final[int] = 1
# Ceiling of a continuous-scale bar's computed width when `marks.bar.max_size` is unauthored.
CONTINUOUS_BAR_MAX_SIZE: Final[float] = 20.0
