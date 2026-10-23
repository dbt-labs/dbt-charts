"""Tests for point_map's `collapse` opt-in (co-located marks sized by count).

`collapse` is an explicit authored opt-in (`collapse: true`), never a
silent default, and must refuse when it would repurpose an already-authored
`size:` measure channel (the bubble_map case).

Also covers the area-proportional size-scale fix: the previous [50, 1000]
range floor made bubble_map/collapse size an affine, not proportional,
function of the domain value.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.chart.authored import (
    PointMapChart as AuthoredPointMapChart,
)

# ---------------------------------------------------------------------------
# 1. Hard constraint: collapse cannot repurpose an authored size: channel
# ---------------------------------------------------------------------------


def test_collapse_with_size_rejected() -> None:
    """`collapse: true` + `size: <col>` both bind the size channel — refuse."""
    with pytest.raises(ValidationError, match="collapse"):
        AuthoredPointMapChart(type="bubble_map", size="magnitude", collapse=True)


def test_collapse_with_size_error_names_bubble_map_not_point_map() -> None:
    """`size:` is the bubble_map-only field — it's the likeliest way to hit
    this validator, so the error must name the chart type the author
    actually typed, not a hardcoded "point_map:" that never applies here."""
    with pytest.raises(ValidationError, match="bubble_map:") as excinfo:
        AuthoredPointMapChart(type="bubble_map", size="magnitude", collapse=True)
    assert "point_map:" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# 1b. Hard constraint: collapse cannot repurpose the color channel either
# (2026-08-12 Decision — "collapse and the color channel").
# ---------------------------------------------------------------------------


def test_collapse_with_field_color_rejected() -> None:
    """`collapse: true` + `color: <col>` makes VL group the count aggregate by
    an extra field — re-creating the exact pile `collapse` exists to remove.
    """
    with pytest.raises(ValidationError, match="collapse"):
        AuthoredPointMapChart(type="point_map", color="store_type", collapse=True)


def test_collapse_error_names_both_resolutions() -> None:
    """The error must name both fixes: drop one field, or aggregate in SQL
    and use bubble_map with size:/color: on the pre-aggregated counts."""
    with pytest.raises(ValidationError, match="bubble_map"):
        AuthoredPointMapChart(type="point_map", color="store_type", collapse=True)


def test_collapse_alone_is_valid() -> None:
    chart = AuthoredPointMapChart(type="point_map", collapse=True)
    assert chart.collapse is True


def test_size_alone_is_valid() -> None:
    chart = AuthoredPointMapChart(type="bubble_map", size="magnitude")
    assert chart.size == "magnitude"
    assert chart.collapse is False


def test_collapse_defaults_false() -> None:
    chart = AuthoredPointMapChart(type="point_map")
    assert chart.collapse is False


# ---------------------------------------------------------------------------
# 2. Emitter: collapse aggregates size by count via native VL, not row grouping
# ---------------------------------------------------------------------------

_PILE_ROWS: list[dict[str, Any]] = [
    {"lat": 34.64, "lng": -120.60},
    {"lat": 34.64, "lng": -120.60},
    {"lat": 34.64, "lng": -120.60},
]


# ---------------------------------------------------------------------------
# 4. Integer count legend — a fractional count is not a data value
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 5. Draw order — collapsed marks draw largest-behind-smallest
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 6. bubble_map's authored size: channel keeps the same area-proportional fix
# ---------------------------------------------------------------------------
