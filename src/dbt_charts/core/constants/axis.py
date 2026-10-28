"""Axis, time-bucket, and gradient-legend engine constants."""

from typing import Final

LABEL_GAP_SPACES: Final[int] = 1
LABEL_GAP_SPACES_NUMERIC: Final[int] = 2
# Absolute pixels, deliberately not a fraction of plot width: a proportional
# rule can't tell "60 monthly points at year cadence, 45px apart on a 282px
# card" (correct to leave flat) from "...180px apart on a 1128px card" (correct
# to reject as too sparse): both are 19.9% of usable width, opposite correct
# answers.
SPARSE_CEILING_PX: Final[float] = 90.0
# An ordinal bar chart becomes an indistinguishable comb of gridlines well
# before decades of buckets pile up. 60 buckets (5 years of monthly ticks) is
# the point where individual bars stop being resolvable at typical chart
# widths; past it, fall back to a continuous temporal scale and let Vega-Lite
# pick its own sparse "nice" ticks. Also the floor for the scaffold budget: an
# auto-detected grain, coarse or fine, survives only while the empty buckets
# gap-fill would synthesize stay within max(distinct buckets, this value).
MAX_ORDINAL_BUCKETS: Final[int] = 60
# Nice-tick target count for a data-derived gradient legend domain (heatmap's
# themed color scale; geoshape never reaches this, since its domain is read
# back client-side), matching the example in nice_tick_values's own docstring.
GRADIENT_NICE_TICK_COUNT: Final[int] = 6
# Target length of the ladder behind `ticks.count: auto`: sparse enough to read, dense enough to scan.
AUTO_TICK_TARGET: Final = 6
# The empty space an end label needs beside its neighbor, as a share of the tick spacing, to earn its place.
# Measured on the glyphs, not the dates: a label crammed against its neighbor reads as a collision whatever the
# date gap, while 0.45 keeps the roomy labels the sweep's reviewer liked.
EDGE_LABEL_MIN_GAP_RATIO: Final[float] = 0.45
