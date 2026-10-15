"""Pie/donut engine constants: label thresholds and attached-table geometry."""

from typing import Final

# Wedges with share at or below this value get no direct label: the wedge
# still renders, hover tooltip carries the value. 8% is the observed danger
# floor: 5-6% wedges next to each other vertically stack their labels at
# near-cardinal angles; 8% gives an honest buffer.
WEDGE_LABEL_MIN_SHARE: Final[float] = 0.08
# Wedges below this share are physically unreliable: arc gap/stroke can
# erase the colored sliver, leaving the source row with no visible mark or
# hover target.
INVISIBLE_SLICE_SHARE: Final[float] = 0.02
# Below this fraction of the label-free disk, direct labels are starving
# the wheel badly enough to prefer the table. Locked against the
# calibration sweep (see the wheel-first-pie-donut-labeling initiative).
WHEEL_DOMINANCE_MIN_RATIO: Final[float] = 0.6
# Calibration and composition constants shared by final pie resolution
# and the mechanical SVG/Vega emitters.
LABEL_REACH_COEFFICIENT: Final[float] = 0.85
OUTER_FRACTION: Final[float] = 0.9
ATTACHED_TABLE_GAP_PX: Final[float] = 12.0
HYBRID_HEADING_GAP_PX: Final[float] = 6.0
# Placement thresholds (right-of-wheel vs below): the table goes to the
# RIGHT when the wheel region left over after reserving the table's
# measured width stays comfortably dominant: at least this fraction of
# the card width, AND at least this many px absolute (guards tiny-but-
# proportionally-fine regions on very narrow cards). Otherwise "below".
RIGHT_PLACEMENT_MIN_WIDTH_FRACTION: Final[float] = 0.5
RIGHT_PLACEMENT_MIN_WIDTH_PX: Final[float] = 280.0
# Right-placement wheel diameter cap: without it, a very wide card leaves
# an oversized wheel region, inflating the donut and stranding the table
# way out at the card's far edge. Comfortable-viewing ceiling.
RIGHT_PLACEMENT_MAX_WHEEL_PX: Final[float] = 480.0
