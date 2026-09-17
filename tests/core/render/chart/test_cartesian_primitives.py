"""Tests for shared cartesian primitives in v2/emitters/_cartesian.py.

Covers the structural contract of the extracted primitives: the VLDict
typedef boundary and the named-tuple return shapes (replacing opaque
positional tuples). Vega-Lite output parity across the refactor
is covered separately by the render-v2 emitter parity suite.
"""

from __future__ import annotations

import dataclasses
import json
import re

import pytest
import vl_convert as vlc

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.chart.authored._annotations import ChartSort
from dbt_charts.core.compile.models.style.resolved._base import ResolvedAxisStyle
from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
from dbt_charts.core.compile.resolve.style.axis_cascade import (
    AxisOverrides,
    build_resolved_axis,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.chart.emitters._cartesian import (
    CartesianXResolution,
    XYTitles,
    bar_sort_to_vl,
    build_palette_config,
    build_x_enc,
    chart_sort_to_vl,
    pin_normalize_axis_format,
    resolve_cartesian_x,
    resolve_xy_titles,
)
from dbt_charts.core.render.chart.spec import RenderBox
from dbt_charts.core.render.chart.vl_field_maps import compose_axis_label_expr
from dbt_charts.core.text.predefined_formats import (
    PREDEFINED_SPECS,
    PredefinedNumberFormat,
)
from dbt_charts.core.utils import x_domain_order

from ...conftest import fixture_chart_for_type


def _axes() -> tuple[ResolvedAxisStyle, ResolvedAxisStyle]:
    chart_style_context = resolve_chart_style_context(
        get_theme_style(get_default_theme_name())
    )
    ax_merged, ay_merged, ax_band_position, ay_band_position, _, _ = (
        _bake_cartesian_axes(
            chart_style_context,
            fixture_chart_for_type("line"),
            "line",
            "temporal",
            "quantitative",
            AxisOverrides(),
        )
    )
    return (
        build_resolved_axis(
            ax_merged,
            band_position=ax_band_position,
            chart_id="test",
            format_authored=True,
            format_is_alias=False,
        ),
        build_resolved_axis(
            ay_merged,
            band_position=ay_band_position,
            chart_id="test",
            format_authored=True,
            format_is_alias=False,
        ),
    )


def test_build_palette_config_with_palette() -> None:
    assert build_palette_config(("#fff", "#000")) == {
        "range": {"category": ["#fff", "#000"]}
    }


def test_build_palette_config_without_palette() -> None:
    assert build_palette_config(None) == {}


def test_resolve_cartesian_x_returns_named_fields() -> None:
    ax, _ay = _axes()
    data = [{"date": "2025-01-01"}, {"date": "2025-02-01"}]
    result = resolve_cartesian_x(
        "date",
        data,
        ax,
        label_usable_ratio=1.0,
        chart_width=600.0,
        chart_id="c1",
        mark_type="line",
        panel_fields=(),
    )
    assert isinstance(result, CartesianXResolution)
    assert result.vl_type
    assert isinstance(result.axis, dict)
    assert isinstance(result.scale, dict)


def test_resolve_xy_titles_returns_named_fields() -> None:
    ax, ay = _axes()
    result = resolve_xy_titles(
        "date", "revenue", None, None, ax, ay, RenderBox(width=600.0, height=300.0), ""
    )
    assert isinstance(result, XYTitles)
    assert result.x_title == "date"
    assert result.y_title == "revenue"


def test_build_x_enc_uses_resolved_fields() -> None:
    enc = build_x_enc(
        "date", "temporal", "Date", {"grid": True}, {"padding": 1}, sort=None
    )
    assert enc == {
        "field": "date",
        "type": "temporal",
        "title": "Date",
        "axis": {"grid": True},
        "sort": None,
        "scale": {"padding": 1},
    }


def test_build_x_enc_carries_an_authored_sort() -> None:
    """The authored sort reaches the encoding verbatim, so a discrete x scale
    takes its domain order from the sort field rather than from row order."""
    enc = build_x_enc(
        "month",
        "nominal",
        "Month",
        {},
        {},
        sort=chart_sort_to_vl(ChartSort(by="seq", order="desc")),
    )
    assert enc["sort"] == {"field": "seq", "order": "descending"}


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


def test_chart_sort_to_vl_does_not_pin_op() -> None:
    """chart_sort_to_vl stays unopinionated about VL's own sort ``op`` default.

    It is the shared mapper, reached by surfaces with no aggregate in common:
    the support table's category domain is not a VL scale at all, while a
    bar's stacked sort-by-measure IS a stacked total and a dimension axis
    never is. A caller that needs the aggregate stated says so at its own
    point — ``dimension_sort_to_vl`` for a dimension axis, ``bar_sort_to_vl``
    for a bar — rather than making this mapper opine on every surface at once.
    """
    vl_sort = chart_sort_to_vl(ChartSort(by="val", order="desc"))
    assert vl_sort == {"field": "val", "order": "descending"}, (
        f"chart_sort_to_vl must not add an 'op' key, got {vl_sort!r}"
    )


@pytest.mark.parametrize(
    ("sort_by", "stacked", "op"),
    [
        ("seq", True, "min"),
        ("seq", False, "min"),
        ("val", True, "sum"),
        ("val", False, "min"),
    ],
)
def test_bar_sort_to_vl_pins_sum_only_for_a_stacked_measure_sort(
    sort_by: str, stacked: bool, op: str
) -> None:
    """Sorting a stack by its own measure is the one sort that means a total."""
    assert bar_sort_to_vl(ChartSort(by=sort_by, order="asc"), "val", stacked) == {
        "field": sort_by,
        "order": "ascending",
        "op": op,
    }


def test_bar_sort_to_vl_passes_an_unauthored_sort_through() -> None:
    """No authored sort, nothing to pin — the encoding keeps VL's own order."""
    assert bar_sort_to_vl(None, "val", True) is None


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
        _, ay_merged, _, ay_band_position, ay_format_authored, ay_format_is_alias = (
            _bake_cartesian_axes(
                chart_style_context,
                fixture_chart_for_type("line"),
                "line",
                "temporal",
                "quantitative",
                AxisOverrides(),
            )
        )
        return build_resolved_axis(
            ay_merged,
            band_position=ay_band_position,
            chart_id="test",
            format_authored=ay_format_authored,
            format_is_alias=ay_format_is_alias,
        )

    def test_clears_a_composed_label_expr(self) -> None:
        ay = self._ladder_less_ay()
        assert ay.tick_label is not None and ay.tick_label.si_format is not None
        ay_vl = compose_axis_label_expr({}, ay.ruler, ay)
        assert "labelExpr" in ay_vl
        pin_normalize_axis_format(ay_vl, ay)
        assert "labelExpr" not in ay_vl
        assert ay_vl["format"] == PREDEFINED_SPECS[PredefinedNumberFormat.percent_whole]

    def test_keeps_an_authored_label_expr(self) -> None:
        """The pre-existing contract: an author's own `axis_y.labels.expr`
        already wins over `format` today (Vega's own labelExpr precedence) --
        that must survive unchanged, not be swept up by the new clear.
        """
        ay = self._ladder_less_ay()
        authored_ay = dataclasses.replace(
            ay, labels=dataclasses.replace(ay.labels, expr="'x'")
        )
        ay_vl = {"labelExpr": "'x'"}
        pin_normalize_axis_format(ay_vl, authored_ay)
        assert ay_vl["labelExpr"] == "'x'"

    def test_no_op_when_no_label_expr_present(self) -> None:
        ay = self._ladder_less_ay()
        ay_vl: dict[str, object] = {}
        pin_normalize_axis_format(ay_vl, ay)
        assert "labelExpr" not in ay_vl
        assert ay_vl["format"] == PREDEFINED_SPECS[PredefinedNumberFormat.percent_whole]
