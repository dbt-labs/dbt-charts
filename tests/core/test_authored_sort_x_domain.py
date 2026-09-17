"""An authored ``sort:`` orders the categorical x axis on every family that
documents the key — area, line and heatmap alongside bar and scatter — and
every consumer that reads that order back agrees with what Vega-Lite draws.

Area, line and heatmap all default to the query's own row order for an axis
the author said nothing about; an authored ``sort:`` overrides that default.
The tests below pin each family's unauthored default alongside its authored
one, because the fix is exactly the line between them.

The four orders are deliberately all different, so no assertion here can pass
by accident:

    query row order   Feb, Mar, Jan
    alphabetical      Feb, Jan, Mar
    sort asc by seq   Jan, Feb, Mar
    sort desc by seq  Mar, Feb, Jan
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal

import pytest
import vl_convert as vlc

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.authored import ChartSort
from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    HeatmapChart,
    LineChart,
)
from dbt_charts.core.compile.models.style.authored import (
    AxisXStylePatch,
    LineChartStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.session import BoardRenderSession
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox
from dbt_charts.core.render.chart.x_domain import rendered_x_domain

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())
_BOX = RenderBox(width=600.0, height=300.0)
# Wide enough that `_series_label_layout_for_width` leaves endpoint labels on:
# the `tiny` tier turns the rail off entirely, so a narrower box would let a
# rail test pass by rendering no rail at all.
_RAIL_BOX = RenderBox(width=900.0, height=420.0)

_ROWS: list[dict] = [
    {"month": "Feb", "seq": 2, "revenue": 200.0},
    {"month": "Mar", "seq": 3, "revenue": 50.0},
    {"month": "Jan", "seq": 1, "revenue": 100.0},
]
# Heatmap needs a second grid dimension; both cells of a month share its seq.
_HEATMAP_ROWS: list[dict] = [
    {**row, "series": series} for row in _ROWS for series in ("alpha", "gamma")
]

# Two rows per category, so the aggregate VL folds them with is observable:
# by sum Jan=11/Feb=10/Mar=9 (desc -> Jan, Feb, Mar), by min Jan=1/Feb=4/Mar=9
# (desc -> Mar, Feb, Jan). A dimension axis pins min. Single-row-per-category
# data hides every op disagreement, which is why it cannot be the only fixture.
_MULTI_ROWS: list[dict] = [
    {"month": "Feb", "series": "A", "revenue": 6.0},
    {"month": "Feb", "series": "B", "revenue": 4.0},
    {"month": "Mar", "series": "A", "revenue": 9.0},
    {"month": "Jan", "series": "A", "revenue": 10.0},
    {"month": "Jan", "series": "B", "revenue": 1.0},
]
_BY_MIN_DESC = ["Mar", "Feb", "Jan"]

# Wide (`y:` a list) rows — the multi-measure emit paths take their own
# `build_x_enc` call, which no single-measure test reaches.
_WIDE_ROWS: list[dict] = [{**row, "target": 1.0} for row in _ROWS]

_QUERY_ORDER = ["Feb", "Mar", "Jan"]
_ASC = ["Jan", "Feb", "Mar"]
_DESC = ["Mar", "Feb", "Jan"]

_DISCRETE_X_AXIS = r'aria-label="X-axis for a discrete scale[^:]*'

_Chart = AreaChart | LineChart | HeatmapChart
_CartesianCls = type[AreaChart] | type[LineChart]

_CARTESIAN = [
    pytest.param(AreaChart, "area", id="area"),
    pytest.param(LineChart, "line", id="line"),
]


def _emit(chart: _Chart, rows: list[dict]) -> ChartSpec:
    resolved = resolve(chart, rows, chart_style_context=_BOARD_CTX)
    return BoardRenderSession.create(_BOARD_STYLE).emit_chart(
        resolved, _BOX, {resolved.query_name: rows}
    )


def _vl(chart: _Chart, rows: list[dict]) -> VLDict:
    resolved = resolve(chart, rows, chart_style_context=_BOARD_CTX)
    session = BoardRenderSession.create(_BOARD_STYLE)
    return session.finalize_vl(
        session.emit_chart(resolved, _BOX, {resolved.query_name: rows})
    )


def _svg(chart: _Chart, rows: list[dict]) -> str:
    return vlc.vegalite_to_svg(json.dumps(_vl(chart, rows)))


def _rendered_x_domain(chart: _Chart, rows: list[dict]) -> list[str]:
    """The x-scale values Vega actually rendered, in axis order.

    Read off the x-axis group's own ``aria-label``, which Vega writes as
    "X-axis for a discrete scale with N values: a, b, c" — the scale's final
    domain as drawn. Asserting on the spec's ``sort`` key instead would prove
    nothing: an explicit ``scale.domain`` silently outranks ``sort`` in
    Vega-Lite, so the key can survive verbatim while being ignored.
    """
    match = re.search(_DISCRETE_X_AXIS + ': ([^"]*)"', _svg(chart, rows))
    assert match is not None, "no discrete x-axis aria-label in the rendered SVG"
    return match.group(1).split(", ")


def _rendered_cell_x_order(chart: _Chart, rows: list[dict]) -> list[str]:
    """The x categories a rect mark's cells actually occupy, left to right.

    For a chart that draws no x axis to read a domain off — the multi-measure
    heatmap builds no ``axis`` dict — the cells' own rendered positions are
    the only statement of the order.
    """
    vega = vlc.vegalite_to_vega(json.dumps(_vl(chart, rows)))
    cells: list[tuple[float, str]] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("marktype") == "rect":
                cells.extend(
                    (item["x"], item["description"])
                    for item in node.get("items", [])
                    if "description" in item
                )
            for child in node.get("items", []):
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(vlc.vega_to_scenegraph(vega)["scenegraph"])
    assert cells, "no rect cells in the rendered scenegraph"
    ordered: list[str] = []
    for _, description in sorted(cells):
        match = re.match(rf"{chart.x}: (\S+?);", description)
        assert match is not None, f"unreadable cell description {description!r}"
        if match.group(1) not in ordered:
            ordered.append(match.group(1))
    return ordered


def _heatmap(sort: ChartSort | None) -> HeatmapChart:
    return HeatmapChart(
        id="c1", type="heatmap", x="month", y="series", color="revenue", sort=sort
    )


@pytest.mark.parametrize(("cls", "kind"), _CARTESIAN)
@pytest.mark.parametrize(("order", "expected"), [("asc", _ASC), ("desc", _DESC)])
def test_cartesian_x_honors_authored_sort(
    cls: _CartesianCls, kind: str, order: str, expected: list[str]
) -> None:
    chart = cls(
        id="c1",
        type=kind,
        x="month",
        y="revenue",
        sort=ChartSort(by="seq", order=order),
    )
    assert _rendered_x_domain(chart, _ROWS) == expected


@pytest.mark.parametrize(("order", "expected"), [("asc", _ASC), ("desc", _DESC)])
def test_heatmap_x_honors_authored_sort(order: str, expected: list[str]) -> None:
    chart = _heatmap(ChartSort(by="seq", order=order))
    assert _rendered_x_domain(chart, _HEATMAP_ROWS) == expected


@pytest.mark.parametrize(("cls", "kind"), _CARTESIAN)
def test_cartesian_x_without_sort_keeps_query_row_order(
    cls: _CartesianCls, kind: str
) -> None:
    """The unauthored default: an explicit ``sort: None`` on the encoding, so
    the query's own row order reaches the axis."""
    chart = cls(id="c1", type=kind, x="month", y="revenue")
    assert _emit(chart, _ROWS).encoding["x"]["sort"] is None
    assert _rendered_x_domain(chart, _ROWS) == _QUERY_ORDER


def test_heatmap_x_without_sort_keeps_query_row_order() -> None:
    """Heatmap's unauthored default: an explicit ``sort: None`` on the x
    encoding, the same query-row-order default area/line/bar pin — not
    Vega-Lite's own alphabetical fallback for a nominal field."""
    chart = _heatmap(None)
    assert _emit(chart, _HEATMAP_ROWS).encoding["x"]["sort"] is None
    assert _rendered_x_domain(chart, _HEATMAP_ROWS) == _QUERY_ORDER


def test_heatmap_y_without_sort_keeps_query_row_order() -> None:
    """The y-axis twin: heatmap has two categorical dimensions, and both
    default to query row order now, not just x."""
    chart = _heatmap(None)
    spec = _emit(chart, _HEATMAP_ROWS)
    assert spec.encoding["y"]["sort"] is None


def test_heatmap_y_honors_authored_sort() -> None:
    """An authored ``sort:`` reaches the y encoding the same way it already
    reaches x."""
    chart = _heatmap(ChartSort(by="seq", order="desc"))
    spec = _emit(chart, _HEATMAP_ROWS)
    assert spec.encoding["y"]["sort"] == {
        "field": "seq",
        "order": "descending",
        "op": "min",
    }


# A bucketed-time x that resolves ORDINAL: rows reach the encoding already
# re-sorted chronologically by ``canonicalize_and_sort_ordinal_x``
# (``time_unit_detect.py``), so query row order is not what the axis shows.
_BUCKETED_ROWS: list[dict] = [
    {"month": "2024-02-01", "revenue": 200.0},
    {"month": "2024-03-01", "revenue": 50.0},
    {"month": "2024-01-01", "revenue": 100.0},
]
_CHRONOLOGICAL = ["2024-01-01", "2024-02-01", "2024-03-01"]
_BY_REVENUE_DESC = ["2024-02-01", "2024-01-01", "2024-03-01"]


def _ordinal_bucketed_line(sort: ChartSort | None) -> LineChart:
    return LineChart(
        id="c1",
        type="line",
        x="month",
        y="revenue",
        sort=sort,
        style=LineChartStylePatch(axis_x=AxisXStylePatch(type="ordinal")),
    )


def test_bucketed_ordinal_x_without_sort_stays_chronological() -> None:
    """``canonicalize_and_sort_ordinal_x`` owns the order of an UNAUTHORED
    bucketed-time x: the axis reads chronologically even though the query
    returned the rows Feb, Mar, Jan."""
    assert _rendered_x_domain(_ordinal_bucketed_line(None), _BUCKETED_ROWS) == (
        _CHRONOLOGICAL
    )


def test_authored_sort_outranks_derived_chronological_order() -> None:
    """...and an authored ``sort:`` outranks it. The chronological order is a
    default for an axis the author said nothing about, not an override of one
    they did — the two never race, because VL reads a discrete scale's domain
    off the encoding's ``sort`` rather than off row order."""
    chart = _ordinal_bucketed_line(ChartSort(by="revenue", order="desc"))
    assert _rendered_x_domain(chart, _BUCKETED_ROWS) == _BY_REVENUE_DESC


def test_sort_stays_inert_on_a_continuous_temporal_x() -> None:
    """The honest limit of the key: the same bucketed x left to resolve
    temporal is a continuous scale, and Vega-Lite's ``sort`` applies only to
    discrete ones. Such an axis is ordered by its own values by construction,
    so there is nothing for a sort to reorder — the emitted key is carried,
    and ignored."""
    chart = LineChart(
        id="c1",
        type="line",
        x="month",
        y="revenue",
        sort=ChartSort(by="revenue", order="desc"),
    )
    x_enc = _emit(chart, _BUCKETED_ROWS).encoding["x"]
    assert x_enc["type"] == "temporal"
    assert x_enc["sort"] == {"field": "revenue", "order": "descending", "op": "min"}
    assert re.search(_DISCRETE_X_AXIS, _svg(chart, _BUCKETED_ROWS)) is None


def _rail_positions(chart: _Chart, rows: list[dict] | None = None) -> dict[str, float]:
    """Each series' endpoint-label anchor, read off the rail pane's own rows.

    The rail is an ``hconcat`` sibling of the plot; its data rows carry the
    resolved anchor per series (mirrors ``test_bar_endpoint_labels``).
    """
    rows = _MULTI_ROWS if rows is None else rows
    resolved = resolve(chart, rows, chart_style_context=_BOARD_CTX)
    session = BoardRenderSession.create(_BOARD_STYLE)
    spec = session.emit_chart(resolved, _RAIL_BOX, {resolved.query_name: rows})
    vl = session.finalize_vl(spec)
    assert "hconcat" in vl, f"no endpoint-label rail in the spec, got {list(vl)}"
    return {row["series"]: row["__y"] for row in vl["hconcat"][1]["data"]["values"]}


def test_rendered_x_domain_agrees_with_the_axis_on_multi_row_categories() -> None:
    """``rendered_x_domain`` is the repo's single definition of "the order
    Vega-Lite will draw", and every read-back consumer trusts it — the endpoint
    rail, band value labels, the overlay reconciler that *pins* it as an
    explicit ``scale.domain``.

    It reproduces VL's order by folding each category's rows with VL's own
    aggregate, which is ``sum`` for a stacking mark and ``min`` otherwise. A
    line is not a stacking mark, so a dimension x sort that left the aggregate
    inferred would make the helper claim Jan, Feb, Mar while the axis drew Mar,
    Feb, Jan — silently, on a default-on feature.
    """
    chart = LineChart(
        id="c1",
        type="line",
        x="month",
        y="revenue",
        color="series",
        sort=ChartSort(by="revenue", order="desc"),
    )
    resolved = resolve(chart, _MULTI_ROWS, chart_style_context=_BOARD_CTX)
    spec = BoardRenderSession.create(_BOARD_STYLE).emit_chart(
        resolved, _BOX, {resolved.query_name: _MULTI_ROWS}
    )
    assert _rendered_x_domain(chart, _MULTI_ROWS) == _BY_MIN_DESC
    assert (
        rendered_x_domain(spec.encoding["x"], _MULTI_ROWS, [], chart_id=None)
        == _BY_MIN_DESC
    )


def _stacked_area(order: str, sort_by: str = "revenue") -> AreaChart:
    return AreaChart(
        id="c1",
        type="area",
        x="month",
        y="revenue",
        color="series",
        style={"stack": "zero"},
        sort=ChartSort(by=sort_by, order=order),
    )


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        # min(revenue) per month: Jan=1, Feb=4, Mar=9. Each series anchors at
        # its OWN last non-null column in the drawn order, not at one shared
        # trailing column.
        # asc  -> Jan, Feb, Mar. A ends in Mar (only A=9 there, 0..9, mid 4.5);
        #         B ends in Feb, where A=6 sits at the baseline and B=4 above
        #         it (6..10, mid 8.0).
        ("asc", {"A": 4.5, "B": 8.0}),
        # desc -> Mar, Feb, Jan. Both series reach Jan: A=10 at the baseline
        #         (0..10, mid 5.0), B=1 above it (10..11, mid 10.5).
        ("desc", {"A": 5.0, "B": 10.5}),
    ],
)
def test_stacked_area_rail_follows_an_authored_sort(
    order: str, expected: dict[str, float]
) -> None:
    """The endpoint rail anchors each series on the column VL draws last, so it
    has to rank columns in the axis's order.

    The anchors are pinned as values, not merely as "asc differs from desc":
    ranking by the wrong aggregate swaps the two results, and swapped values
    are still unequal.
    """
    assert _rail_positions(_stacked_area(order)) == expected


def test_stacked_area_needs_no_unorderable_sort_guard() -> None:
    """A stacked bar refuses a sort column with no numbers in it
    (``ERR-ENDPOINT-LABELS-UNORDERABLE-SORT``), a gate kept from when bar left
    its aggregate to Vega-Lite; it is conservative now that bar pins ``min``.

    A dimension axis folds with ``min``, a comparison — so a text column is an
    ordering, not an unorderable one, and the rail follows it. The guard would
    be a hard error with nothing to protect. Here every month's smallest
    ``series`` is "A", so the tie leaves row order standing and Jan is drawn
    last; the ordering case proper is
    ``test_a_text_or_date_sort_column_orders_the_axis``.
    """
    assert _rail_positions(_stacked_area("asc", sort_by="series")) == {
        "A": 5.0,
        "B": 10.5,
    }


@pytest.mark.parametrize(("cls", "kind"), _CARTESIAN)
@pytest.mark.parametrize(("order", "expected"), [("asc", _ASC), ("desc", _DESC)])
def test_wide_cartesian_x_honors_authored_sort(
    cls: _CartesianCls, kind: str, order: str, expected: list[str]
) -> None:
    """The wide (``y:`` a list) paths are separate emit functions with their
    own ``build_x_enc`` call — ``_emit_multi_metric_area`` and
    ``_emit_folded_line`` — which no single-measure test reaches."""
    chart = cls(
        id="c1",
        type=kind,
        x="month",
        y=["revenue", "target"],
        sort=ChartSort(by="seq", order=order),
    )
    assert _rendered_x_domain(chart, _WIDE_ROWS) == expected


@pytest.mark.parametrize(("order", "expected"), [("asc", _ASC), ("desc", _DESC)])
def test_wide_heatmap_x_honors_authored_sort(order: str, expected: list[str]) -> None:
    """Heatmap's multi-measure branch returns before the single-measure x
    encoding, so it needs the sort wired on its own.

    Read off the cells rather than the axis: that branch builds no ``axis``
    dict, so the chart draws no x-axis group to read a domain from."""
    chart = HeatmapChart(
        id="c1",
        type="heatmap",
        x="month",
        y=["revenue", "target"],
        sort=ChartSort(by="seq", order=order),
    )
    assert _rendered_cell_x_order(chart, _WIDE_ROWS) == expected


@pytest.mark.parametrize(("cls", "kind"), _CARTESIAN)
def test_value_labels_keep_a_sorted_x_axis(cls: _CartesianCls, kind: str) -> None:
    """A VL sub-layer carrying its own ``transform`` array makes vl-convert
    discard the shared categorical scale's ``sort``
    (``features/value_labels.py``). A sorted dimension axis survives it because
    the order is pinned as an explicit ``scale.domain``, which a discarded
    ``sort`` cannot undo."""
    # Area's labels live on its own line mark; AreaChartMarksStylePatch's
    # `area` group carries no labels of its own.
    chart = cls(
        id="c1",
        type=kind,
        x="month",
        y="revenue",
        sort=ChartSort(by="seq", order="asc"),
        style={"marks": {"line": {"labels": {"visible": True}}}},
    )
    assert _rendered_x_domain(chart, _ROWS) == _ASC


def test_a_ragged_stacked_area_draws_the_domain_the_read_back_predicts() -> None:
    """The prediction and the drawing must be the same order, on the shape
    where Vega-Lite's own inference cannot be reproduced.

    A stacked area with ``color:`` imputes a 0 for every series a category is
    missing, and VL folds the sort aggregate over those imputed rows — so on a
    ragged grid (``_MULTI_ROWS`` has no B in Mar, an ordinary shape: a series
    that starts mid-period) it orders by numbers the query never returned. No
    aggregate over the raw rows reproduces that, which is why the emitter pins
    the domain instead of leaving the order inferred.
    """
    for order in ("asc", "desc"):
        chart = _stacked_area(order)
        resolved = resolve(chart, _MULTI_ROWS, chart_style_context=_BOARD_CTX)
        spec = BoardRenderSession.create(_BOARD_STYLE).emit_chart(
            resolved, _BOX, {resolved.query_name: _MULTI_ROWS}
        )
        predicted = rendered_x_domain(
            spec.encoding["x"], _MULTI_ROWS, [], chart_id=None
        )
        assert predicted == _rendered_x_domain(chart, _MULTI_ROWS), (
            f"{order}: read-back disagrees with the drawn axis"
        )


# A text column and a date column, each ordering the months differently from
# query order, alphabetical order and each other.
_TYPED_SORT_ROWS: list[dict] = [
    {"month": "Feb", "region": "north", "start": date(2024, 2, 1), "revenue": 1.0},
    {"month": "Mar", "region": "south", "start": date(2024, 3, 1), "revenue": 2.0},
    {"month": "Jan", "region": "east", "start": date(2024, 1, 1), "revenue": 3.0},
]


@pytest.mark.parametrize("sort_by", ["region", "start"])
@pytest.mark.parametrize(("order", "expected"), [("asc", _ASC), ("desc", _DESC)])
def test_a_text_or_date_sort_column_orders_the_axis(
    sort_by: str, order: str, expected: list[str]
) -> None:
    """Vega compares strings and dates natively, so ``sort: {by: month_start}``
    — the most natural way to order a month axis — is a real ordering.

    Reproducing it therefore has to compare, not coerce to a number: scoring
    the column numerically drops every category, and pinning row order from
    that would replace a working sort with query order, with ``asc`` and
    ``desc`` indistinguishable.
    """
    chart = LineChart(
        id="c1",
        type="line",
        x="month",
        y="revenue",
        sort=ChartSort(by=sort_by, order=order),
    )
    assert _rendered_x_domain(chart, _TYPED_SORT_ROWS) == expected


_DECIMAL_X = [Decimal("1.5"), Decimal("3.5"), Decimal("2.5")]
_DATE_X = [date(2024, 1, 15), date(2024, 3, 15), date(2024, 2, 15)]


@pytest.mark.parametrize(
    ("x_values", "wide", "expected"),
    [
        pytest.param(_DECIMAL_X, False, [1.5, 2.5, 3.5], id="decimal"),
        pytest.param(_DECIMAL_X, True, [1.5, 2.5, 3.5], id="decimal-wide"),
        # A date on the single-measure path is normalized upstream by
        # canonicalize_cartesian_x_data; the multi-measure branch returns
        # before it, so only that one reaches the pin raw.
        pytest.param(
            _DATE_X,
            True,
            ["2024-01-15", "2024-02-15", "2024-03-15"],
            id="date-wide",
        ),
    ],
)
def test_a_pinned_domain_carries_json_scalars(
    x_values: list[object], wide: bool, expected: list[object]
) -> None:
    """Domain values reach the spec without passing through
    ``normalize_data_types``, so a raw ``Decimal`` or ``date`` pinned here
    fails vl_convert's JSON serialization outright — an error card where a
    chart drew before. Heatmap classifies a non-string x as nominal, so it is
    the family that reaches this first.
    """
    rows = [
        {"x": value, "series": "a", "revenue": float(i), "other": 1.0}
        for i, value in enumerate(x_values)
    ]
    chart = HeatmapChart(
        id="c1",
        type="heatmap",
        x="x",
        y=["revenue", "other"] if wide else "series",
        color=None if wide else "revenue",
        sort=ChartSort(by="x", order="asc"),
    )
    spec = _emit(chart, rows)
    assert spec.encoding["x"]["scale"]["domain"] == expected
    # The serialization itself is the assertion: a raw value raises here.
    json.dumps(_vl(chart, rows))


def test_small_multiples_x_still_narrows_per_panel_under_a_sort() -> None:
    """``multiples:`` resolves x independently per panel so a panel holding a
    subset of the domain trims the slots it does not use. One explicit
    ``scale.domain`` applies to every independent scale, so the sorted x is
    left to Vega-Lite's own sort here rather than pinned — otherwise every
    sparse panel paints the empty category slots narrowing exists to remove.
    """
    # Rows deliberately out of sort order within each panel, so a panel drawn
    # in query order reads "Mar, Feb" and fails — the sort is doing work here,
    # it is only the PIN that must not fire.
    rows = [
        {"month": "Mar", "region": "east", "seq": 3, "revenue": 2.0},
        {"month": "Feb", "region": "east", "seq": 2, "revenue": 1.0},
        {"month": "Apr", "region": "west", "seq": 4, "revenue": 4.0},
        {"month": "Jan", "region": "west", "seq": 1, "revenue": 3.0},
    ]
    chart = LineChart(
        id="c1",
        type="line",
        x="month",
        y="revenue",
        multiples={"columns": "region"},
        sort=ChartSort(by="seq", order="asc"),
    )
    panels = re.findall(r'aria-label="X-axis[^:]*: ([^"]*)"', _svg(chart, rows))
    assert panels == ["Feb, Mar", "Jan, Apr"]


_PARTLY_RANKABLE_ROWS: list[dict] = [
    {"month": "Feb", "series": "A", "rank": 2, "revenue": 6.0},
    {"month": "Feb", "series": "B", "rank": 2, "revenue": 4.0},
    {"month": "Mar", "series": "A", "rank": "n/a", "revenue": 9.0},
    {"month": "Jan", "series": "A", "rank": 1, "revenue": 10.0},
    {"month": "Jan", "series": "B", "rank": 1, "revenue": 1.0},
]


@pytest.mark.parametrize("order", ["asc", "desc"])
def test_a_partly_rankable_sort_column_still_draws_its_categories(order: str) -> None:
    """A sort Vega-Lite cannot rank leaves row order standing — reproducing it
    must never empty the domain.

    An empty ``scale.domain`` is not "no pin": Vega-Lite draws a scale with
    zero values, i.e. a blank chart.
    """
    chart = LineChart(
        id="c1",
        type="line",
        x="month",
        y="revenue",
        color="series",
        sort=ChartSort(by="rank", order=order),
    )
    assert _rendered_x_domain(chart, _PARTLY_RANKABLE_ROWS) == _QUERY_ORDER


def test_a_partly_rankable_sort_column_keeps_the_rail_off_the_baseline() -> None:
    """The rail ranks columns through the same helper, so an empty domain
    collapses every series onto ``last_rank = 0`` — every label stacked at the
    chart baseline. Area takes no ``_refuse_unorderable_sort`` guard, so
    nothing else catches this.
    """
    chart = AreaChart(
        id="c1",
        type="area",
        x="month",
        y="revenue",
        color="series",
        style={"stack": "zero"},
        sort=ChartSort(by="rank", order="asc"),
    )
    # Row order stands, so Jan is drawn last: A=10 at the baseline (mid 5.0),
    # B=1 above it (mid 10.5).
    assert _rail_positions(chart, _PARTLY_RANKABLE_ROWS) == {"A": 5.0, "B": 10.5}


def test_a_layered_chart_never_pins_an_empty_shared_domain() -> None:
    """``_reconcile_x_domain`` writes ``rendered_x_domain``'s result onto the
    shared categorical scale unguarded, so an empty domain from an unrankable
    sort reaches Vega-Lite as a scale with no values at all.

    A layer sharing the base's rows contributes no x column of its own, so the
    union IS the base domain — nothing else can refill it.

    Unrankable here means a column Vega's own comparator cannot order: a bar's
    sort pins ``op: min`` (``bar_sort_to_vl``), and ``min`` compares strings and
    dates natively, so a plain text column is an ordering rather than the empty
    case. A column mixing a number with a string is not.
    """
    chart = BarChart(
        id="c1",
        type="bar",
        x="month",
        y="target",
        layers=[LineLayer(type="line", y="target")],
        sort=ChartSort(by="rank", order="asc"),
    )
    rows = [
        {"month": "Feb", "rank": "n/a", "target": 1.0},
        {"month": "Mar", "rank": 2, "target": 2.0},
        {"month": "Jan", "rank": 1, "target": 3.0},
    ]
    resolved = resolve(chart, rows, chart_style_context=_BOARD_CTX)
    session = BoardRenderSession.create(_BOARD_STYLE)
    vl = session.finalize_vl(
        session.emit_chart(resolved, _BOX, {resolved.query_name: rows})
    )
    assert vl["encoding"]["x"]["scale"]["domain"] == _QUERY_ORDER


_TEMPORAL_ROWS: list[dict] = [
    {"day": "2024-01-01", "series": "A", "revenue": 1.0},
    {"day": "2024-01-01", "series": "B", "revenue": 9.0},
    {"day": "2024-02-01", "series": "A", "revenue": 5.0},
    {"day": "2024-02-01", "series": "B", "revenue": 5.0},
    {"day": "2024-03-01", "series": "A", "revenue": 9.0},
]


@pytest.mark.parametrize("sort", [None, ChartSort(by="revenue", order="desc")])
def test_stacked_area_rail_ignores_a_sort_on_a_continuous_x(
    sort: ChartSort | None,
) -> None:
    """The rail must rank columns the same way the axis does — including when
    the axis does not rank at all.

    An authored ``sort:`` on a continuous temporal x is carried and ignored by
    Vega-Lite, so ranking the rail by it anchors every series on a column the
    axis does not draw last. A ends at Mar (0..9, mid 4.5) and B at Feb
    (5..10, mid 7.5) either way; ranking by revenue descending would move both
    onto January's stack instead.
    """
    chart = AreaChart(
        id="c1",
        type="area",
        x="day",
        y="revenue",
        color="series",
        style={"stack": "zero"},
        sort=sort,
    )
    assert _emit(chart, _TEMPORAL_ROWS).encoding["x"]["type"] == "temporal", (
        "precondition: continuous x"
    )
    assert _rail_positions(chart, _TEMPORAL_ROWS) == {"A": 4.5, "B": 7.5}


_HETEROGENEOUS_ROWS: list[dict] = [
    {"month": "Feb", "series": "A", "rank": 2, "revenue": 1.0},
    {"month": "Feb", "series": "B", "rank": 2, "revenue": 1.0},
    {"month": "Mar", "series": "A", "rank": 3, "revenue": 2.0},
    {"month": "Jan", "series": "A", "rank": 1, "revenue": 3.0},
    {"month": "Jan", "series": "B", "rank": "n/a", "revenue": 3.0},
]


@pytest.mark.parametrize(("cls", "kind"), _CARTESIAN)
def test_a_heterogeneous_sort_category_renders(cls: _CartesianCls, kind: str) -> None:
    """``min`` folds the column's own values, so a category holding a number
    beside a string must fold the way Vega does — passing over what it cannot
    compare — rather than raising ``TypeError`` out of the emitter.

    Vega's own answer for this data is Jan, Feb, Mar (measured in
    ``test_cartesian_primitives.py``): d3 keeps the running minimum unless
    ``min > value``, and Jan's numeric ``1`` comes first.
    """
    chart = cls(
        id="c1",
        type=kind,
        x="month",
        y="revenue",
        color="series",
        sort=ChartSort(by="rank", order="asc"),
    )
    assert _rendered_x_domain(chart, _HETEROGENEOUS_ROWS) == _ASC


def test_a_heterogeneous_sort_category_renders_on_heatmap() -> None:
    """The heatmap twin of the case above — same fold, different emitter."""
    chart = HeatmapChart(
        id="c1",
        type="heatmap",
        x="month",
        y="series",
        color="revenue",
        sort=ChartSort(by="rank", order="asc"),
    )
    assert _rendered_x_domain(chart, _HETEROGENEOUS_ROWS) == _ASC
