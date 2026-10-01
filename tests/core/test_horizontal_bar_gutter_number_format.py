"""Horizontal bar's categorical-axis gutter must measure the number format
Vega actually paints on a numeric category, not the raw row value.

``emitters/bar.py`` builds ``cat_labels`` from ``str(row[cat_field])`` and
feeds them to ``measured_label_padding`` — an authored
``axis_x.labels.format`` (a d3 NUMBER format) is applied by Vega
(``vl_field_maps.axis_to_vl`` sets VL ``format`` unconditionally) but never
reaches this measurement, so a numeric category column authored with a
format under-reserves its own label gutter.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())


def _spec(make_chart, *, number_format: str | None):
    data = [{"bucket": str((i + 1) * 1_000_000), "count": 10 - i} for i in range(6)]
    style: dict = {"orientation": "horizontal"}
    if number_format is not None:
        style["axis_x"] = {"labels": {"format": number_format}}
    chart = make_chart(
        "bar",
        x="bucket",
        y="count",
        style=BarChartStylePatch.model_validate(style),
    )
    resolve(chart, data, chart_style_context=_BOARD_CTX)
    return generate_vega_lite_spec(chart, data, height=300)


def test_authored_number_format_widens_the_categorical_gutter(make_chart) -> None:
    bare = _spec(make_chart, number_format=None)
    formatted = _spec(make_chart, number_format="$,.0f")

    bare_padding = bare["encoding"]["y"]["axis"]["labelPadding"]
    formatted_padding = formatted["encoding"]["y"]["axis"]["labelPadding"]

    assert formatted_padding > bare_padding
