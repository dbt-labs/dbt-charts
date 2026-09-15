"""The endpoint-label rail anchors on the last category of the rendered x axis.

A categorical x renders in first-occurrence row order (the order
``pin_categorical_domain_order`` pins for the axis), which has nothing to do
with how the category strings compare lexically. Anchoring on a raw ``>=``
over the x values put every label at the lexically greatest category — for a
slope chart over "Before"/"After" that is the *first* column, so the labels
sat at the wrong end and came out in the wrong vertical order.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_STYLE = resolve_style(get_theme_style())

_SERIES = ("Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot")


def _anchors(make_chart, model_copy_at, data, x_field, expect_x_type, **kw):
    """The emitted ``$df_endpoint_label_cascade.anchors``, as a dict.

    ``expect_x_type`` pins which x path the fixture actually took. The line
    emitter promotes year-shaped category strings ("2019", "FY2019") to a
    continuous temporal axis, so a fixture built out of those would leave the
    categorical branch untested and pass against the broken code.
    """
    chart = make_chart("line", x=x_field, y="value", color="series", **kw)
    seed = model_copy_at(
        get_theme_style(),
        "charts.line.endpoint_labels",
        EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
    )
    resolved_chart = resolve(
        chart, data, chart_style_context=resolve_chart_style_context(seed)
    )
    spec = render_resolved_chart(resolved_chart, data, _BOARD_STYLE).payload
    assert spec["hconcat"][0]["encoding"]["x"]["type"] == expect_x_type
    return dict(spec["$df_endpoint_label_cascade"]["anchors"])


def _slope_rows(first: str, last: str):
    """Six series across two periods, first period first in row order."""
    return [
        row
        for i, s in enumerate(_SERIES)
        for row in (
            {"period": first, "value": 10.0 + i, "series": s},
            {"period": last, "value": 100.0 + i * 10, "series": s},
        )
    ] + [
        # A null x carries no endpoint; the rail must skip it, not key on it.
        {"period": None, "value": 999.0, "series": _SERIES[0]}
    ]


def test_categorical_anchor_is_the_last_axis_category_not_the_lexical_max(
    make_chart, model_copy_at
):
    """ "Before"/"After": axis order is the reverse of alphabetical order."""
    data = _slope_rows("Before", "After")
    expected = {row["series"]: row["value"] for row in data if row["period"] == "After"}
    assert _anchors(make_chart, model_copy_at, data, "period", "nominal") == expected


def test_categorical_anchor_matches_when_alphabetical_agrees_with_axis_order(
    make_chart, model_copy_at
):
    """ "P1"/"P2" is the same chart with categories that sort into axis order."""
    data = _slope_rows("P1", "P2")
    expected = {row["series"]: row["value"] for row in data if row["period"] == "P2"}
    assert _anchors(make_chart, model_copy_at, data, "period", "nominal") == expected


def test_temporal_x_still_anchors_at_its_own_last_value(make_chart, model_copy_at):
    """A bump chart over years keeps ordering by x value, not by row position.

    The rows are deliberately emitted newest-first, so a continuous x that
    took the categorical branch's first-occurrence order would anchor on 2023
    rather than 2019 — and the label would sit at the wrong end of the line.
    """
    data = [
        {"year": year, "value": float(year - 2000 + rank), "series": s}
        for year in (2023, 2022, 2021, 2020, 2019)
        for rank, s in enumerate(_SERIES, start=1)
    ]
    expected = {s: float(23 + rank) for rank, s in enumerate(_SERIES, start=1)}
    assert _anchors(make_chart, model_copy_at, data, "year", "temporal") == expected


def test_quantitative_x_still_anchors_at_its_own_last_value(make_chart, model_copy_at):
    """Same pin for a plain numeric x, which resolves quantitative rather than temporal."""
    data = [
        {"step": step, "value": float(step * 10 + rank), "series": s}
        for step in (5, 4, 3, 2, 1)
        for rank, s in enumerate(_SERIES, start=1)
    ]
    expected = {s: float(50 + rank) for rank, s in enumerate(_SERIES, start=1)}
    assert _anchors(make_chart, model_copy_at, data, "step", "quantitative") == expected


def test_ordinal_calendar_x_anchors_chronologically_whatever_the_row_order(
    make_chart, model_copy_at
) -> None:
    """An ordinal calendar x keeps comparing raw values, and must stay correct.

    The emitter rewrites the rows it drew and publishes them on ``spec.data``,
    which is exactly the case ``_anchor_rows`` declines. ISO date strings
    already compare in axis order, so the raw walk-back is right here — this
    pins that the categorical fix does not reach in and reorder it.
    """
    data = [
        {"month": month, "value": value, "series": s}
        for month, alpha, bravo in (
            ("2024-05-01", 100.0, 10.0),
            ("2024-04-01", 130.0, 13.0),
            ("2024-03-01", 140.0, 14.0),
        )
        for s, value in (("Alpha", alpha), ("Bravo", bravo))
    ]
    anchors = _anchors(
        make_chart,
        model_copy_at,
        data,
        "month",
        "ordinal",
        style={"axis_x": {"type": "ordinal"}},
    )
    assert anchors == {"Alpha": 100.0, "Bravo": 10.0}


def test_authored_sort_moves_the_anchor_to_the_column_it_renders_last() -> None:
    """An authored ``sort:`` reorders the axis, and no row order reflects that.

    The bar emitter puts the sort on the x encoding itself, so the rendered
    order is the sort-field order — here the column with the *smallest*
    per-category minimum renders last. Neither the raw row order nor a lexical
    max finds it.
    """
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    cats = ["C", "A", "B", "D", "E", "F", "G", "H"]
    weight = {c: float(i + 1) for i, c in enumerate(cats)}
    data = [
        {"cat": c, "value": weight[c] * (2 if s == "s1" else 1), "series": s}
        for c in cats
        for s in ("s1", "s2")
    ]
    chart = BarChart(
        id="t",
        source_path="charts.t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="cat",
        y="value",
        color="series",
        sort={"by": "value", "order": "desc"},
        style={
            "stack": "none",
            "orientation": "vertical",
            "endpoint_labels": {"visible": True},
        },
    )
    resolved = resolve(
        chart, data, chart_style_context=resolve_chart_style_context(get_theme_style())
    )
    spec = render_resolved_chart(resolved, data, _BOARD_STYLE).payload
    x_enc = spec["hconcat"][0]["encoding"]["x"]
    assert x_enc["type"] == "nominal"
    assert x_enc["sort"] == {"field": "value", "order": "descending", "op": "min"}
    # Each column's own minimum is its weight, so descending renders "C"
    # (weight 1) last — s1 = 2.0, s2 = 1.0 there. A lexical max would pick
    # "H" (16, 8).
    assert dict(spec["$df_endpoint_label_cascade"]["anchors"]) == {
        "s1": 2.0,
        "s2": 1.0,
    }


def test_labeled_calendar_buckets_anchor_on_the_chronologically_last_bucket(
    make_chart, model_copy_at
) -> None:
    """A labeled bucket is rewritten by the emitter, so the rail leaves it alone.

    ``normalize_labeled_temporal`` turns "Q1 2024" into an ISO date before the
    axis is drawn, putting the query's own rows in a different *value* space
    from the rendered domain — a rank taken against it would name a column
    these rows never held. ``_anchor_rows`` declines whenever the emitter
    published rewritten rows. This pins that the raw walk-back still lands.
    """
    data = [
        {"quarter": quarter, "value": value, "series": s}
        for quarter, alpha, bravo in (
            ("Q3 2024", 100.0, 10.0),
            ("Q2 2024", 130.0, 13.0),
            ("Q1 2024", 140.0, 14.0),
        )
        for s, value in (("Alpha", alpha), ("Bravo", bravo))
    ]
    anchors = _anchors(
        make_chart,
        model_copy_at,
        data,
        "quarter",
        "ordinal",
        style={"axis_x": {"type": "ordinal"}},
    )
    assert anchors == {"Alpha": 100.0, "Bravo": 10.0}


def test_date_objects_anchor_on_the_chronologically_last_bucket(
    make_chart, model_copy_at
) -> None:
    """The same declined path, reached through a raw ``datetime.date`` cell."""
    import datetime as dt

    data = [
        {"month": dt.date(2024, month, 1), "value": value, "series": s}
        for month, alpha, bravo in (
            (5, 100.0, 10.0),
            (4, 130.0, 13.0),
            (3, 140.0, 14.0),
        )
        for s, value in (("Alpha", alpha), ("Bravo", bravo))
    ]
    anchors = _anchors(
        make_chart,
        model_copy_at,
        data,
        "month",
        "ordinal",
        style={"axis_x": {"type": "ordinal"}},
    )
    assert anchors == {"Alpha": 100.0, "Bravo": 10.0}


def test_wide_measures_anchor_at_the_last_axis_category(make_chart, model_copy_at):
    """A wide ``y: [a, b]`` chart names its measures off the same rail.

    Its series come from the wide fold rather than a ``color:`` column, so it
    reaches the anchor through a branch no color-series fixture covers.
    """
    data = [
        {"period": "Before", "alpha": 10.0, "bravo": 20.0},
        {"period": "After", "alpha": 90.0, "bravo": 70.0},
    ]
    chart = make_chart("line", x="period", y=["alpha", "bravo"])
    seed = model_copy_at(
        get_theme_style(),
        "charts.line.endpoint_labels",
        EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
    )
    resolved = resolve(
        chart, data, chart_style_context=resolve_chart_style_context(seed)
    )
    spec = render_resolved_chart(resolved, data, _BOARD_STYLE).payload
    assert spec["hconcat"][0]["encoding"]["x"]["type"] == "nominal"
    assert dict(spec["$df_endpoint_label_cascade"]["anchors"]) == {
        "alpha": 90.0,
        "bravo": 70.0,
    }


def test_wide_measures_under_a_sort_rank_on_vega_lites_own_fold() -> None:
    """The rail's anchor follows the order the emitted sort actually produces.

    The domain comes from the rows before this feature's own wide fold, which
    skips a null cell where Vega-Lite's ``fold`` transform keeps the row. A
    wide bar pins ``op: min`` (``bar_sort_to_vl``), where a dropped row cannot
    move the result, so the two folds agree today — the pre-fold domain is what
    keeps them agreeing if the aggregate ever changes again.
    """
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    # alpha descending renders B, A, C — "C" is the last column, and both
    # labels belong to it. One row per category here, so the fold cannot move
    # anything; the pre-fold domain is what keeps it that way.
    data = [
        {"cat": "C", "alpha": 10.0, "bravo": 1.0},
        {"cat": "A", "alpha": 20.0, "bravo": None},
        {"cat": "B", "alpha": 30.0, "bravo": 3.0},
    ]
    chart = BarChart(
        id="t",
        source_path="charts.t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="cat",
        y=["alpha", "bravo"],
        sort={"by": "alpha", "order": "desc"},
        style={
            "stack": "none",
            "orientation": "vertical",
            "endpoint_labels": {"visible": True},
        },
    )
    resolved = resolve(
        chart, data, chart_style_context=resolve_chart_style_context(get_theme_style())
    )
    spec = render_resolved_chart(resolved, data, _BOARD_STYLE).payload
    assert spec["hconcat"][0]["encoding"]["x"]["sort"] == {
        "field": "alpha",
        "order": "descending",
        "op": "min",
    }
    assert dict(spec["$df_endpoint_label_cascade"]["anchors"]) == {
        "alpha": 10.0,
        "bravo": 1.0,
    }
