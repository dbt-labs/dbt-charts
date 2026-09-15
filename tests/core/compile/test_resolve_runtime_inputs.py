from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    Chart,
    TableChart,
)
from dbt_charts.core.compile.models.chart.resolved import ResolvedTableChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve import (
    auto_link_excludes_x,
    resolve,
    should_fetch_table_fk_links,
    should_synthesize_auto_link,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context


def _style() -> ChartStyleContext:
    return resolve_chart_style_context(get_theme_style("clarity"))


def _chart(payload: dict[str, Any]) -> Chart:
    return TypeAdapter(Chart).validate_python(payload)


def _bar(**overrides: Any) -> BarChart:
    chart = _chart(
        {
            "id": "revenue",
            "type": "bar",
            "query": SqlQuery(sql="select * from main.revenue", source="warehouse"),
            "x": "category",
            "y": "amount",
            **overrides,
        }
    )
    assert isinstance(chart, BarChart)
    return chart


def test_auto_link_eligibility_stays_in_compile() -> None:
    rows = [{"category": 1.5, "amount": 10.0, "region": "west"}]

    assert should_synthesize_auto_link(_bar(color="region"), rows, True)
    assert auto_link_excludes_x(_bar(color="region"), rows)
    assert not should_synthesize_auto_link(_bar(link="/authored/{{ x }}"), rows, True)
    assert not should_synthesize_auto_link(_bar(), rows, False)


def test_auto_link_excludes_x_ignores_bar_orientation() -> None:
    """An inline `style.orientation: horizontal` must not exclude x — a bar's
    x is always the category/time channel, never the measure, regardless of
    orientation. A plain bar with no style at all was never excluded either;
    pinned here so a future orientation-based check can't reintroduce this.
    """
    string_x_rows = [
        {"category": "north", "amount": 10.0},
        {"category": "south", "amount": 20.0},
    ]

    assert not auto_link_excludes_x(
        _bar(style={"orientation": "horizontal"}), string_x_rows
    )
    assert not auto_link_excludes_x(_bar(), string_x_rows)


def test_table_fk_fact_eligibility_is_independent_of_root_link() -> None:
    query = SqlQuery(sql="select * from main.orders", source="warehouse")
    chart = TableChart(
        id="orders",
        type="table",
        query=query,
        link="/authored-row/{{ id }}",
    )

    assert not should_synthesize_auto_link(chart, [{"id": 1}], True)
    assert should_fetch_table_fk_links(chart, True)


def test_resolve_selects_authored_or_runtime_root_link_before_construction() -> None:
    rows = [{"category": "A", "amount": 10.0}]

    automatic = resolve(
        _bar(), rows, _style(), automatic_link_candidate="/auto/{{ x }}"
    )
    authored = resolve(
        _bar(link="/authored/{{ x }}"),
        rows,
        _style(),
        automatic_link_candidate="/auto/{{ x }}",
    )

    assert automatic.link == "/auto/{{ x }}"
    assert authored.link == "/authored/{{ x }}"


def test_standalone_none_width_uses_the_same_default_as_omission() -> None:
    rows = [{"category": "A", "amount": 10.0}]

    omitted = resolve(_bar(), rows, _style())
    standalone = resolve(_bar(), rows, _style(), width=None)

    assert standalone == omitted


def test_resolve_builds_final_fk_columns_with_precedence_and_identity_protection() -> (
    None
):
    style = _style()
    row_role = "_df_row_role"
    table_column_links = {
        "id": "/orders/{{ id }}",
        "customer_id": "/customers/{{ customer_id }}",
        "status": "/runtime-status/{{ status }}",
    }
    table_column_rows = [
        {"name": "id", "actual_type": "INTEGER"},
        {"name": "customer_id", "actual_type": "INTEGER"},
        {"name": "status", "actual_type": "VARCHAR"},
    ]
    chart = _chart(
        {
            "id": "orders",
            "type": "table",
            "style": {
                "columns": {
                    "status": {
                        "link": "/authored-status/{{ status }}",
                    }
                },
                "row": {"role": row_role},
            },
        }
    )
    assert isinstance(chart, TableChart)
    rows = [
        {
            "id": 1,
            "customer_id": 7,
            "status": "open",
            row_role: "body",
        }
    ]

    resolved = resolve(
        chart,
        rows,
        style,
        table_column_links=table_column_links,
        table_column_rows=table_column_rows,
    )

    assert isinstance(resolved, ResolvedTableChart)
    assert resolved.columns is not None
    assert resolved.columns["status"].link == "/authored-status/{{ status }}"
    # style.columns is styling-only: every query column is materialized, and
    # runtime FK facts fill the unlisted ones the same as the inferred case
    # below ("id" is identity-protected, so it gets no FK link).
    assert set(resolved.columns) == {"id", "customer_id", "status"}
    assert resolved.columns["id"].link is None
    assert resolved.columns["customer_id"].link == "/customers/{{ customer_id }}"

    inferred = resolve(
        _chart(
            {
                "id": "inferred",
                "type": "table",
                "style": {"row": {"role": row_role}},
            }
        ),
        rows,
        style,
        table_column_links=table_column_links,
        table_column_rows=table_column_rows,
    )
    assert isinstance(inferred, ResolvedTableChart)
    assert inferred.columns is not None
    assert row_role not in inferred.columns
    assert inferred.columns["id"].link is None
    assert inferred.columns["customer_id"].link == "/customers/{{ customer_id }}"
    assert inferred.columns["status"].link == "/runtime-status/{{ status }}"


def test_resolve_keeps_fk_links_on_a_pivoting_chart_row_dimension() -> None:
    """A pivoting chart's `rows:` dimension survives the pivot transform
    unchanged (unlike its measure columns), so it is an ordinary inferred
    column — FK links must still reach it, not just non-pivoting tables."""
    chart = TableChart(
        id="p",
        type="table",
        rows=["region"],
        columns=["month"],
        values=["amount"],
    )
    rows = [
        {"region": "US", "month": "Jan", "amount": 100},
        {"region": "EU", "month": "Jan", "amount": 150},
    ]

    resolved = resolve(
        chart,
        rows,
        _style(),
        table_column_links={"region": "/regions/{{ region }}"},
        table_column_rows=[{"name": "region", "actual_type": "VARCHAR"}],
    )

    assert isinstance(resolved, ResolvedTableChart)
    assert resolved.columns is not None
    assert resolved.columns["region"].link == "/regions/{{ region }}"
    # The measure has no key space at resolve — its own leaf columns only
    # exist after render's pivot transform (column_defaults reaches them
    # there instead; see test_table_column_defaults.py).
    assert "amount" not in resolved.columns


def test_resolve_constructs_request_local_chart_text_without_mutating_normalized() -> (
    None
):
    chart = _bar(
        title="{{ region }} revenue",
        subtitle="Compared with {{ comparison }}",
        link="/regions/{{ category }}",
    )
    rows = [{"category": "enterprise", "amount": 10.0}]

    west = resolve(
        chart,
        rows,
        _style(),
        variables={"region": "West", "comparison": "plan", "category": "wrong"},
    )
    east = resolve(
        chart,
        rows,
        _style(),
        variables={"region": "East", "comparison": "prior year"},
    )
    no_runtime_variables = resolve(chart, rows, _style(), variables={})
    static = resolve(chart, rows, _style())

    assert west.title == "West revenue"
    assert west.subtitle == "Compared with plan"
    assert east.title == "East revenue"
    assert east.subtitle == "Compared with prior year"
    assert no_runtime_variables.title == "{{ region }} revenue"
    assert static.title == "{{ region }} revenue"
    assert west.link == "/regions/{{ category }}"
    assert east.link == "/regions/{{ category }}"
    assert chart.title == "{{ region }} revenue"
    assert chart.subtitle == "Compared with {{ comparison }}"
    assert chart.link == "/regions/{{ category }}"


def test_resolve_constructs_final_kpi_and_callout_text() -> None:
    style = _style()
    kpi = _chart(
        {
            "id": "revenue",
            "type": "kpi",
            "value": "amount",
            "label": "{{ region }} revenue",
        }
    )
    callout = _chart(
        {
            "id": "notice",
            "type": "callout",
            "title": "Status for {{ region }}",
            "message": "Loaded through {{ date }}",
        }
    )

    resolved_kpi = resolve(
        kpi,
        [{"amount": 10.0}],
        style,
        variables={"region": "West"},
    )
    resolved_callout = resolve(
        callout,
        [],
        style,
        variables={"region": "West", "date": "today"},
    )

    assert resolved_kpi.label == "West revenue"
    assert resolved_callout.title == "Status for West"
    assert resolved_callout.message == "Loaded through today"


def test_link_false_suppresses_auto_link_per_chart() -> None:
    """link: false opts one chart out of a board-level auto_link: true."""
    rows = [{"category": "A", "amount": 10.0, "region": "west"}]

    assert not should_synthesize_auto_link(_bar(link=False, color="region"), rows, True)
    # Explicit null is indistinguishable from omission everywhere in the schema —
    # link: ~ does NOT opt out; the chart still synthesizes.
    assert should_synthesize_auto_link(_bar(link=None, color="region"), rows, True)

    resolved = resolve(
        _bar(link=False), rows, _style(), automatic_link_candidate="/auto/{{ x }}"
    )
    assert resolved.link is None


def test_link_false_keeps_table_fk_links() -> None:
    """link: false suppresses only the row link — FK column links still fetch,
    exactly as they do for a chart with an explicit link."""
    query = SqlQuery(sql="select * from main.orders", source="warehouse")
    chart = TableChart(id="orders", type="table", query=query, link=False)

    assert not should_synthesize_auto_link(chart, [{"id": 1}], True)
    assert should_fetch_table_fk_links(chart, True)
