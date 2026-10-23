"""Tests for D-002: bucketed-time x-axes default to ordinal scale.

Key contracts:
- Any resolved time_unit (yearmonth, yearquarter, year, yearweek, yearmonthdate)
  → encoding.x.type == "ordinal" by default.
- axis_x.type: temporal is the escape hatch to get temporal scale.
- axis_x.type: ordinal forces ordinal even for continuous data.
- datetime.date values become ISO date strings on the ordinal path.
- No timeUnit emitted on ordinal path.
- axis.values (precomputed list) emitted on ordinal bucketed-time path.
- Time-part units (monthofyear, dayofweek, etc.) remain temporal — unchanged.
- time_unit: none stays temporal (continuous date).
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.style.authored import (
    AxisXStylePatch,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())


class TestAxisXTypeFieldValidation:
    """AxisXStylePatch.type validates allowed values."""

    def test_invalid_axis_type_raises_validation_error(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AxisXStylePatch(type="banana")

    def test_valid_axis_types_accepted(self) -> None:
        for valid in ("auto", "ordinal", "temporal"):
            patch = AxisXStylePatch(type=valid)
            assert patch.type == valid
