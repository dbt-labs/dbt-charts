"""When a ``curve: step`` line/area is band-aware.

On a categorical (nominal/ordinal) x-axis, ``curve: step`` draws a full-band-
width plateau per x-value instead of anchoring points at band centers. On a
continuous (temporal/quantitative) x-axis it is a plain step.
"""

from __future__ import annotations

BAND_STEP_CURVE = "step"

# A band scale is the categorical case; "temporal"/"quantitative" are continuous.
_BAND_SCALE_TYPES = ("nominal", "ordinal")


def is_band_step(curve: str | None, x_type: str | None) -> bool:
    """Whether ``curve`` triggers the full-band-width plateau on this x-axis.

    Band mode fires only for the literal ``step`` curve on a categorical
    (nominal/ordinal) x-scale. On a continuous (temporal/quantitative) x-scale
    ``step`` is a plain passthrough to VL's own native step interpolate.
    """
    return curve == BAND_STEP_CURVE and x_type in _BAND_SCALE_TYPES
