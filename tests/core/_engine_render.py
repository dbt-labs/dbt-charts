"""Render one kpi, spark_bar, table or pie chart in the engine, for tests that assert on its SVG."""

import xml.etree.ElementTree as ET
from typing import Any

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.board.normalized import (
    NO_TITLE_SHIFT,
    TitleShift,
    VariableValues,
)
from dbt_charts.core.compile.models.chart.normalized import PieChart
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedKpiChart,
    ResolvedPieChart,
    ResolvedSparkBarChart,
    ResolvedTableChart,
)
from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.models.style.theme import Style
from dbt_charts.core.compile.normalize.charts import normalize_chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart
from dbt_charts.core.render.engine import render_native
from dbt_charts.core.utils import Rows


def render_kpi_svg(
    chart: ResolvedKpiChart,
    data: list[dict[str, Any]],
    width: float | None = None,
    height: float | None = None,
    *,
    board_style: ResolvedStyle,
    inset: dict[str, float] | None = None,
) -> str:
    kpi = chart.style.kpi
    assert kpi is not None
    svg, _ = render_native(
        chart,
        data,
        width=kpi.preferred_width if width is None else width,
        height=height or kpi.default_height,
        padding=inset,
        board_style=board_style,
        board_width=None,
    )
    return svg


def render_spark_bar_svg(
    chart: ResolvedSparkBarChart,
    data: list[dict[str, Any]],
    width: float | None = None,
    height: float | None = None,
    *,
    board_style: ResolvedStyle,
    title_shift: TitleShift = NO_TITLE_SHIFT,
) -> str:
    spark_bar = chart.style.spark_bar
    assert spark_bar is not None
    svg, _ = render_native(
        chart,
        data,
        width=spark_bar.preferred_width if width is None else width,
        height=0.0 if height is None else height,
        padding=None,
        board_style=board_style,
        board_width=None,
        title_shift=title_shift,
    )
    return svg


def render_table_svg(
    chart: ResolvedTableChart,
    data: Rows,
    width: float | None = None,
    height: float = 0.0,
    *,
    board_style: ResolvedStyle,
    variables: VariableValues | None = None,
    inset: dict[str, float] | None = None,
    board_width: float | None = None,
) -> str:
    slot_width = chart.style.table.preferred_width if width is None else width
    return render_native(
        chart,
        data,
        width=slot_width,
        height=height,
        padding=inset,
        board_style=board_style,
        variables=variables,
        board_width=board_width if board_width is not None else slot_width,
    )[0]


def render_pie_svg(
    chart: PieChart | ResolvedPieChart,
    data: Rows,
    width: float = 400.0,
    height: float = 300.0,
    *,
    padding: dict[str, float] | None = None,
    theme: Style | None = None,
    patches: tuple[StylePatch, ...] = (),
) -> str:
    """A pie through the production render entry. A normalized chart resolves
    under ``theme`` (the default theme when omitted) and ``patches`` at
    ``width`` first."""
    board_style, context = resolve_style_and_context(
        get_theme_style() if theme is None else theme, *patches
    )
    if isinstance(chart, PieChart):
        resolved = resolve(chart, data, chart_style_context=context, width=width)
        assert isinstance(resolved, ResolvedPieChart)
        chart = resolved
    artifact = render_resolved_chart(
        chart, data, board_style, width=width, height=height, padding=padding
    )
    assert artifact.kind == "svg"
    assert isinstance(artifact.payload, str)
    return artifact.payload


def kpi_gradient_fill(cell: object, palette: list[str], low: float, high: float) -> str:
    """The card fill of a KPI whose background is a gradient over its own value."""
    rows = [{"v": cell}]
    chart_def = {
        "type": "kpi",
        "query": "q",
        "value": "v",
        "background": {
            "column": "v",
            "scale": {"palette": palette, "min": low, "max": high},
        },
    }
    flat = normalize_chart("c1", chart_def, {"q": ValuesQuery(rows=rows)}, sources={})
    board_style, context = resolve_style_and_context(get_theme_style())
    chart = resolve(flat, rows, chart_style_context=context)
    assert isinstance(chart, ResolvedKpiChart)
    svg = render_kpi_svg(chart, rows, width=300, height=160, board_style=board_style)
    rect = ET.fromstring(svg).find("{http://www.w3.org/2000/svg}rect")
    assert rect is not None
    return rect.attrib["fill"]
