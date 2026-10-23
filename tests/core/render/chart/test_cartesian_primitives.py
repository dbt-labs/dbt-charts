"""Tests for shared cartesian primitives in v2/emitters/_cartesian.py.

Covers the structural contract of the extracted primitives: the VLDict
typedef boundary and the named-tuple return shapes (replacing opaque
positional tuples). Vega-Lite output parity across the refactor
is covered separately by the render-v2 emitter parity suite.
"""

from __future__ import annotations

import json
import re

import pytest
import vl_convert as vlc

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.style.resolved._base import ResolvedAxisStyle
from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
from dbt_charts.core.compile.resolve.style.axis_cascade import (
    AxisOverrides,
    build_resolved_axis,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.utils import x_domain_order

from ...conftest import baked_format, fixture_chart_for_type


def _axes() -> tuple[ResolvedAxisStyle, ResolvedAxisStyle]:
    chart_style_context = resolve_chart_style_context(
        get_theme_style(get_default_theme_name())
    )
    baked = _bake_cartesian_axes(
        chart_style_context,
        fixture_chart_for_type("line"),
        "line",
        "temporal",
        "quantitative",
        AxisOverrides(),
    )
    ax_merged, ay_merged, ax_band_position, ay_band_position = (
        baked.x.style,
        baked.y.style,
        baked.x.band_position,
        baked.y.band_position,
    )

    return (
        build_resolved_axis(
            ax_merged,
            format=baked_format(ax_merged),
            band_position=ax_band_position,
            chart_id="test",
            formats=None,
        ),
        build_resolved_axis(
            ay_merged,
            format=baked_format(ay_merged),
            band_position=ay_band_position,
            chart_id="test",
            formats=None,
        ),
    )


def test_x_domain_order_ops_disagree_on_multi_row_categories() -> None:
    """``sum`` and ``min`` are different orders, and only multi-row categories
    show it — which is why the argument is undefaulted. Jan sums to 11 and
    mins to 1; Feb sums to 10 and mins to 4.
    """
    rows = [
        {"m": "Jan", "v": 10.0},
        {"m": "Jan", "v": 1.0},
        {"m": "Feb", "v": 6.0},
        {"m": "Feb", "v": 4.0},
    ]
    assert x_domain_order(rows, "m", "v", True, op="sum") == ["Jan", "Feb"]
    assert x_domain_order(rows, "m", "v", True, op="min") == ["Feb", "Jan"]


@pytest.mark.parametrize("descending", [False, True])
@pytest.mark.parametrize(
    ("op", "sort_field", "expected"),
    [
        # sum over text totals to NaN per category; VL's comparator returns 0
        # and its sort is stable, so row order stands — in BOTH directions.
        ("sum", "r", ["Feb", "Mar", "Jan"]),
        # A number against a string is incomparable the same way.
        ("min", "k", ["Feb", "Mar", "Jan"]),
    ],
)
def test_x_domain_order_leaves_row_order_where_vega_lite_does(
    op: str, sort_field: str, expected: list[str], descending: bool
) -> None:
    """Row order here is not a fallback — it is what Vega-Lite draws. Returning
    an empty domain instead would blank the chart, and returning a ranking VL
    does not produce would reorder an axis behind the author's back. Both cases
    are measured against ``vl_convert`` in
    ``test_vega_lite_leaves_row_order_where_it_cannot_rank`` below.
    """
    assert x_domain_order(_UNRANKABLE_ROWS, "m", sort_field, descending, op=op) == (
        expected
    )


_UNRANKABLE_ROWS = [
    {"m": "Feb", "r": "north", "k": 2, "v": 1.0},
    {"m": "Mar", "r": "south", "k": "n/a", "v": 2.0},
    {"m": "Jan", "r": "east", "k": 1, "v": 3.0},
]


@pytest.mark.parametrize(("op", "sort_field"), [("sum", "r"), ("min", "k")])
@pytest.mark.parametrize("order", ["ascending", "descending"])
def test_vega_lite_leaves_row_order_where_it_cannot_rank(
    op: str, sort_field: str, order: str
) -> None:
    """The oracle for the test above: what Vega-Lite actually draws for a sort
    it cannot rank. Pinned here so the reproduction and the thing it reproduces
    are checked against each other, not asserted from memory."""
    spec = {
        "data": {"values": _UNRANKABLE_ROWS},
        "mark": "bar",
        "encoding": {
            "x": {
                "field": "m",
                "type": "nominal",
                "sort": {"field": sort_field, "order": order, "op": op},
            },
            "y": {"field": "v", "type": "quantitative"},
        },
    }
    svg = vlc.vegalite_to_svg(json.dumps(spec))
    drawn = re.findall(r'aria-label="X-axis[^:]*: ([^"]*)"', svg)
    assert drawn == ["Feb, Mar, Jan"]


# Two arrangements of the same heterogeneous category. Only the string-first
# one discriminates: with the number first, d3's fold, a +Infinity-seeded fold
# and Python's own min all answer 1.
_HETEROGENEOUS_ROWS = {
    "number-first": [
        {"m": "Feb", "k": 2, "v": 1.0},
        {"m": "Mar", "k": 3, "v": 2.0},
        {"m": "Jan", "k": 1, "v": 3.0},
        {"m": "Jan", "k": "n/a", "v": 3.0},
    ],
    "string-first": [
        {"m": "Feb", "k": 2, "v": 1.0},
        {"m": "Mar", "k": 3, "v": 2.0},
        {"m": "Jan", "k": "n/a", "v": 3.0},
        {"m": "Jan", "k": 1, "v": 3.0},
    ],
}

# (arrangement, order, what Vega draws, what x_domain_order returns). The two
# agree except in the last row, where Jan folds to "n/a" and Vega's comparator
# cannot order it against 2 or 3: it reports "equal" for those pairs, and
# `Array.prototype.sort` over a non-transitive comparator partially ranks on
# its own pivot choices. Row order is the deterministic reading of that. On
# the families that pin an explicit domain the divergence cannot reach a
# rendered axis, since that domain is what Vega draws. Bar is the exception:
# it pins the sort's aggregate but never calls pin_sorted_domain, so a bar
# sorted by a column mixing numbers and strings can land here. Recorded, not
# chased.
_HETEROGENEOUS_CASES = [
    ("number-first", "ascending", "Jan, Feb, Mar", ["Jan", "Feb", "Mar"]),
    ("number-first", "descending", "Mar, Feb, Jan", ["Mar", "Feb", "Jan"]),
    ("string-first", "ascending", "Feb, Mar, Jan", ["Feb", "Mar", "Jan"]),
    ("string-first", "descending", "Mar, Feb, Jan", ["Feb", "Mar", "Jan"]),
]


@pytest.mark.parametrize(("arrangement", "order", "vega", "ours"), _HETEROGENEOUS_CASES)
def test_x_domain_order_folds_a_heterogeneous_category_like_vega(
    arrangement: str, order: str, vega: str, ours: list[str]
) -> None:
    """``vega`` is measured by the oracle below; ``ours`` is what this returns."""
    rows = _HETEROGENEOUS_ROWS[arrangement]
    assert x_domain_order(rows, "m", "k", order == "descending", op="min") == ours


@pytest.mark.parametrize(("arrangement", "order", "vega", "ours"), _HETEROGENEOUS_CASES)
def test_vega_lite_folds_a_heterogeneous_category_to_its_comparable_value(
    arrangement: str, order: str, vega: str, ours: list[str]
) -> None:
    """The oracle for the test above — it fails if Vega's own answer moves."""
    spec = {
        "data": {"values": _HETEROGENEOUS_ROWS[arrangement]},
        "mark": "line",
        "encoding": {
            "x": {
                "field": "m",
                "type": "nominal",
                "sort": {"field": "k", "order": order, "op": "min"},
            },
            "y": {"field": "v", "type": "quantitative"},
        },
    }
    svg = vlc.vegalite_to_svg(json.dumps(spec))
    assert re.findall(r'aria-label="X-axis[^:]*: ([^"]*)"', svg) == [vega]


def test_x_domain_order_ranks_a_text_column_under_min() -> None:
    """``min`` is a comparison, so a text column IS an ordering there —
    east/north/south, not the rows' own order."""
    assert x_domain_order(_UNRANKABLE_ROWS, "m", "r", False, op="min") == (
        ["Jan", "Feb", "Mar"]
    )


class TestPinNormalizeAxisFormat:
    """A normalize-stacked axis is always a 0-100% share axis, unconditionally
    -- including over a labelExpr the ladder-less sub-unit guard composed for
    the axis's own (irrelevant here) SI default. See this function's own
    docstring and ``inject_axis_numeral_expr``'s.
    """

    @staticmethod
    def _ladder_less_ay() -> ResolvedAxisStyle:
        """An axis whose `tick_label.si_format` is baked -- exactly the state
        a normalize-stacked measure axis reaches today (ladder-less, SI
        format unauthored), which is what let a stale labelExpr survive
        alongside the percent pin.
        """
        chart_style_context = resolve_chart_style_context(
            get_theme_style(get_default_theme_name())
        )
        baked = _bake_cartesian_axes(
            chart_style_context,
            fixture_chart_for_type("line"),
            "line",
            "temporal",
            "quantitative",
            AxisOverrides(),
        )
        return build_resolved_axis(
            baked.y.style,
            format=baked.y.format,
            band_position=baked.y.band_position,
            chart_id="test",
            formats=None,
        )
