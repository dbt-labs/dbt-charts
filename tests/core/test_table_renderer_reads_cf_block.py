"""Table renderer reads conditional formatting from the chart-level block.

After deleting ``TableColumnConfig.when``, the top-level
``conditional_formatting:`` block indexed by column is the single authored
path for threshold rules on tables — same as on every other chart type.
The renderer must resolve CF directly from ``chart.conditional_formatting``
without any internal lowering into ``style.columns[*].when``.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.normalized import TableChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

from ._engine_render import render_table_svg

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())


def test_table_renders_background_from_conditional_formatting_block() -> None:
    """A matching rule in the chart-level block paints the cell background."""
    chart = TableChart(
        id="t1",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="table",
        conditional_formatting={
            "arr": {
                "when": [{"gt": 1_000_000, "background": "#166534"}],
            }
        },
    )
    data = [{"region": "North", "arr": 1_500_000}, {"region": "South", "arr": 500_000}]
    resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    svg = render_table_svg(
        resolved,
        data,
        width=500,
        height=200,
        board_style=resolve_style(get_theme_style()),
    )

    assert "#166534" in svg


def test_table_renders_font_color_and_weight_from_block() -> None:
    """``font.color`` and ``font.weight`` in a block rule show up in SVG."""
    chart = TableChart(
        id="t2",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="table",
        conditional_formatting={
            "status": {
                "when": [
                    {
                        "eq": "Critical",
                        "font": {"color": "#991b1b", "weight": "bold"},
                    }
                ],
            }
        },
    )
    data = [{"status": "Critical"}, {"status": "OK"}]
    resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    svg = render_table_svg(
        resolved,
        data,
        width=500,
        height=200,
        board_style=resolve_style(get_theme_style()),
    )

    assert "#991b1b" in svg
    assert 'font-weight="bold"' in svg


def test_table_renders_multiple_columns_independently() -> None:
    """Each column's rule-set evaluates independently at render time."""
    chart = TableChart(
        id="t3",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="table",
        conditional_formatting={
            "arr": {"when": [{"lt": 0, "background": "#fee2e2"}]},
            "status": {"when": [{"eq": "Critical", "background": "#fecaca"}]},
        },
    )
    data = [
        {"arr": -100, "status": "OK"},
        {"arr": 200, "status": "Critical"},
    ]
    resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    svg = render_table_svg(
        resolved,
        data,
        width=600,
        height=200,
        board_style=resolve_style(get_theme_style()),
    )

    assert "#fee2e2" in svg
    assert "#fecaca" in svg
