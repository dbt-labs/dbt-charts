"""A chart's own ``legend:`` keys outrank the automatic legend policies.

Three ways an authored legend key used to be discarded in silence:

- ``legend.visible: true`` lost to the endpoint-label rail, which retires the
  color legend whenever it fires (the theme switches the rail on, so this hit
  every multi-series line and area chart).
- ``legend.position`` lost to the automatic top-legend policy on bar, and on
  line resolved to a legend that was never shown, because the position key
  alone left the theme's ``visible: false`` standing.
- ``legend.position: none`` was Vega-Lite's ``orient: "none"`` — "place me by
  hand", which with no coordinates floats the legend inside the plot — not
  "no legend".
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_legend_position_none_is_rejected(make_chart):
    """``none`` was VL's "position me by hand", never "hide me"."""
    with pytest.raises(ValidationError, match="position"):
        make_chart(
            "line",
            x="date",
            y="value",
            color="series",
            style={"legend": {"position": "none"}},
        )
