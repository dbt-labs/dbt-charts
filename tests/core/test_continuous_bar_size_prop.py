"""Tests for `continuous_bar_size_prop`'s numeric-cell filter.

Warehouse NUMERIC/DECIMAL x columns (BigQuery, DuckDB) yield `Decimal` cells,
and a real result set can carry non-finite floats. Both must be handled the
same way `coerce_numeric_cell` (`dbt_charts.core.utils`) already handles them
everywhere else in the render layer: `Decimal` counts as numeric, NaN/Infinity
do not.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from dbt_charts.core.compile.models.style.theme.marks import BarMarkStyle
from dbt_charts.core.render.chart.vl_field_maps import continuous_bar_size_prop

_BAR = BarMarkStyle(gap=3.0, min_size=4.0, max_size=20.0)


def test_decimal_x_values_reach_the_computed_gap_ladder() -> None:
    """Decimal cells (a warehouse NUMERIC/DECIMAL column) must be recognized
    as numeric so the function computes the gap/min/max ladder — not fall
    through the "< 2 distinct numeric values" branch to a flat max_size."""
    data = [{"x": Decimal("1")}, {"x": Decimal("2")}, {"x": Decimal("3")}]
    prop = continuous_bar_size_prop(_BAR, "x", data, "vertical")
    width = prop["width"]
    assert isinstance(width, dict) and "expr" in width, (
        f"Decimal x values must reach the gap/min/max ladder (an {{'expr': ...}} "
        f"dict), not the max_size fallback; got {width!r}"
    )


def test_nan_x_value_never_reaches_the_emitted_expression() -> None:
    """A non-finite x cell must be excluded from the step computation entirely
    — not sorted alongside real values, not interpolated into the Vega expr.

    Before the fix, `_is_numeric(float('nan'))` was True, `sorted()` over a
    NaN-bearing set didn't raise, and NaN could end up as the step distance
    between the two closest xs — `repr(float('nan'))` then interpolated the
    literal text "nan" into the emitted expression string, and Vega dies with
    an opaque 'Unrecognized signal name: "nan"' at render time instead of a
    clean dbt charts diagnostic. Two values, one real and one NaN, land NaN
    exactly there pre-fix (`min(nan - 1.0, ...)` — NaN sorts to the end and
    the pairwise diff against it is itself NaN).
    """
    data = [{"x": 1.0}, {"x": float("nan")}]
    prop = continuous_bar_size_prop(_BAR, "x", data, "vertical")
    assert "nan" not in repr(prop).lower(), (
        f"NaN leaked into the emitted prop: {prop!r}"
    )


def test_null_gap_fails_loudly_instead_of_leaking_into_the_expression() -> None:
    """`marks.bar.gap` can genuinely be None at this point: `BarChartStyle.marks`
    carries an InheritSlot, so a chart-local `marks.bar.gap: null` can clear it
    after the theme cascade has otherwise populated `size`/`min_size`/`max_size`
    (the same premise as the existing null-`size` regression test in
    `test_bar_grouped_columns.py`). `f"{None!r}"` renders the literal text
    "None", which is not valid Vega expression syntax and used to die with an
    opaque 'Unrecognized signal name: "None"' at render time — a loud assert
    here is the fix, per "no magic, validate and error fast".
    """
    bar = BarMarkStyle(gap=None, min_size=4.0, max_size=20.0)
    data = [{"x": 1.0}, {"x": 2.0}, {"x": 3.0}]
    with pytest.raises(AssertionError, match="marks.bar.gap unset"):
        continuous_bar_size_prop(bar, "x", data, "vertical")


def test_unauthored_max_size_caps_at_the_engine_ceiling() -> None:
    bar = BarMarkStyle(gap=3.0, min_size=4.0)
    assert continuous_bar_size_prop(bar, "x", [], "vertical") == {"width": 20.0}
    expr = continuous_bar_size_prop(bar, "x", [{"x": 1}, {"x": 2}], "vertical")
    assert expr["width"]["expr"].endswith("4.0, 20.0)")
