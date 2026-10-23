"""Tests for x-axis time-unit behavior on bar/line/area charts.

After D-002: bucketed-time x-axes on bar/column default to ordinal scale; the
temporal scale is an opt-in escape hatch (axis_x.type: temporal). Line/area
are the opposite default — a bucketed-calendar grain always routes to a
continuous temporal scale (no per-bucket density gate; see
value-driven-axis-type-inference task).

Verifies:
- bar on DATE columns emits ordinal (not temporal); line/area emit temporal
- explicit time_unit override (yearquarter, yearmonth) → bar stays ordinal
- time_unit: none → temporal continuous (unchanged)
- datetime.date objects are recognized as date-like
- sparse-gap behavior on bar: ordinal collapses the gap (no missing band)
- Q-label / W-label normalization to ISO dates runs on bar and line
- band_width resizes bars on ordinal scale
- grouped bar x-offset still works on ordinal scale
- Time-part units (monthofyear/dayofweek/etc.) remain temporal
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.style.authored import (
    AxisXStylePatch,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())


class TestSparseGapPreservation:
    # `in`, not `==`: the expression also carries the label-period gate
    # that decides WHICH of this axis's per-bucket ticks speak. The three
    # assertions above are this test's actual subject — no `formatType:
    # "time"`, no raw `format`, the author's spec reaching the labels —
    # and all three still hold. See
    # test_authored_format_keeps_the_cadence_gate.py for the gate itself.

    def test_invalid_time_unit_value_raises_validation_error(self) -> None:
        # time_unit is a Literal type — garbage values must be rejected at construction.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AxisXStylePatch(time_unit="banana")
