"""The support-table strip must thin at the cadence the axis actually paints.

``_label_period_filter_expr`` (``support_table_attachment.py``) runs the same
cadence ladder the axis does, so the strip's cells and the axis's labels land
on one rhythm — the module's own docstring says keeping the two in agreement
is the point. That ladder now takes the authored time format, because the
format is what the axis paints and therefore what decides its cadence.

This is the one production seam for that argument outside
``emitters/_label_overlap.py``, and nothing else exercises it: drop the kwarg
and the strip filters at a different cadence than the axis shows — duplicated
or missing period headers — with the rest of the suite green.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.authored import (
    ChartSupportTable,
    ChartSupportTableAggregate,
)
from dbt_charts.core.compile.models.chart.normalized import LineChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AxisXStylePatch,
    LineChartStylePatch,
    StylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_MONTHS = [f"{year}-{month:02d}-01" for year in (2022, 2023) for month in range(1, 13)]


def _strip_filters(
    time_format: str | None = None,
    *,
    chart_style: Any = None,
    board_patch: Any = None,
) -> list[str]:
    """Every ``filter`` transform in the rendered spec, strip layers included."""
    data: list[dict[str, Any]] = [
        {"month": month, "value": float(i + 1)} for i, month in enumerate(_MONTHS)
    ]
    style = chart_style
    if style is None:
        style = (
            LineChartStylePatch(time_format=time_format)
            if time_format
            else LineChartStylePatch()
        )
    chart = LineChart(
        id="c",
        source_path="charts.c",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="line",
        x="month",
        y="value",
        style=style,
        support_table=ChartSupportTable(
            entries=[ChartSupportTableAggregate(aggregate="sum", source="value")]
        ),
    )
    patches = (board_patch,) if board_patch is not None else ()
    rs, ctx = resolve_style_and_context(get_theme_style(), *patches)
    resolved = resolve(chart, data, chart_style_context=ctx, width=420.0)
    spec = render_resolved_chart(resolved, data, rs, width=420.0).payload

    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for step in node.get("transform", []) or []:
                if isinstance(step, dict) and "filter" in step:
                    found.append(str(step["filter"]))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(spec)
    return found


def test_strip_thins_to_quarterly_on_the_bare_month_vocabulary() -> None:
    """24 monthly points at 420px: "Jan" fits every quarter."""
    assert any("% 3 === 0" in expr for expr in _strip_filters(None))


def test_an_authored_format_coarsens_the_strip_with_the_axis() -> None:
    """ "January 2022" does not fit every quarter, so the axis coarsens to
    yearly — and the strip has to follow it there, or it paints four period
    headers under one label."""
    filters = _strip_filters("%B %Y")
    assert any("utcmonth(toDate(datum['month'])) === 0" in expr for expr in filters), (
        filters
    )
    assert not any("% 3 === 0" in expr for expr in filters), filters


def test_ordinal_strip_honors_the_chart_own_time_unit_over_the_family_default() -> None:
    """A chart's own ``style.axis_x.time_unit`` must win over the chart-type
    (family-wide) ``charts.line.axis_x.time_unit`` default on the ordinal
    bucketed-time path.

    The board sets a family-wide ``yearquarter`` default; this chart's own
    style asks for ``yearmonth`` (matching its actual monthly data) and forces
    the ordinal scale via ``axis_x.type: ordinal``. ``resolve_axis_x_overlap``
    independently thins the visible labels to quarterly for this width, so
    the strip must filter to quarter-opening months.
    """
    board_patch = StylePatch.model_validate(
        {"charts": {"line": {"axis_x": {"time_unit": "yearquarter"}}}}
    )
    chart_style = LineChartStylePatch(
        axis_x=AxisXStylePatch(type="ordinal", time_unit="yearmonth")
    )
    filters = _strip_filters(chart_style=chart_style, board_patch=board_patch)
    assert any(
        "utcmonth(toDate(datum['month'])) % 3 === 0" in expr for expr in filters
    ), filters
