"""Tests for board-slot -> paint resolution (color_at) and the shared
category_scale_for lookup.

These live beside CategoryColorScale in dbt_charts.core.compile.models.style.
theme.category_colors — a compile/ model, not the neutral dbt_charts.core.
colors leaf (which only validates foreign CSS/SVG color strings; see
dbt-charts/tests/core/test_colors.py).
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorScale,
    category_scale_for,
    color_at,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError


def test_color_at_returns_palette_slot():
    scale = CategoryColorScale(field="cat", slots={"A": 0, "B": 1}, overrides={})
    assert color_at(scale, "A", ["#111", "#222"]) == "#111"
    assert color_at(scale, "B", ["#111", "#222"]) == "#222"


def test_color_at_returns_override_before_slot():
    scale = CategoryColorScale(field="cat", slots={"A": 0}, overrides={"A": "#abcdef"})
    assert color_at(scale, "A", ["#111"]) == "#abcdef"


def test_color_at_raises_chart_data_error_for_unseated_value():
    """A value outside scale.slots is a real bug upstream — never a bare KeyError."""
    scale = CategoryColorScale(field="cat", slots={"A": 0}, overrides={})
    with pytest.raises(ChartDataError, match="Z.*cat"):
        color_at(scale, "Z", ["#111"])


def test_color_at_raises_rather_than_wraps_for_a_too_short_palette():
    """A slot planned against a wider palette must never silently wrap.

    Wrapping via `% len(palette)` is exactly the silent fallback that lets a
    chart-local palette shorter than the board's collapse two categories onto
    one swatch. Two categories must never share a swatch — raise instead.
    """
    scale = CategoryColorScale(field="cat", slots={"A": 0, "D": 3}, overrides={})
    palette = ["#111", "#222", "#333"]
    with pytest.raises(ChartDataError, match="D.*cat"):
        color_at(scale, "D", palette)


def test_category_scale_for_returns_the_matching_scale():
    a = CategoryColorScale(field="a", slots={}, overrides={})
    b = CategoryColorScale(field="b", slots={}, overrides={})
    assert category_scale_for((a, b), "b") is b


def test_category_scale_for_returns_none_when_unbound():
    a = CategoryColorScale(field="a", slots={}, overrides={})
    assert category_scale_for((a,), "missing") is None
