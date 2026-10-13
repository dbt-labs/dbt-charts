"""Tests for TitleSubtitleStyle font size propagation into the table renderer.

Behavior tests: subtitle.font.size reads directly from style.title.subtitle
(the same source every chart family reads for its Vega-Lite subtitle) with no
computed math. Uses a distinctive override value, not the theme default.

KPI no longer has a subtitle slot — its support row replaces that role —
so only the table and spark_bar paths are covered here.
"""

from __future__ import annotations

import dataclasses

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

from ._engine_render import render_table_svg

_RS, _CTX = resolve_style_and_context(get_theme_style())


def _make_table_chart(title: str = "Revenue", subtitle: str = "Q1"):
    from dbt_charts.core.compile.models.chart.normalized import TableChart

    return TableChart(id="test_table", type="table", title=title, subtitle=subtitle)


def _custom_board_with_subtitle_size(size: float | None):
    new_title = _CTX.title.model_copy(
        update={
            "subtitle": _CTX.title.subtitle.model_copy(
                update={
                    "font": _CTX.title.subtitle.font.model_copy(update={"size": size})
                }
            )
        }
    )
    custom_ctx = dataclasses.replace(_CTX, title=new_title)
    new_chart_defaults = dataclasses.replace(_RS.chart_defaults, title=new_title)
    custom_rs = dataclasses.replace(_RS, chart_defaults=new_chart_defaults)
    return custom_ctx, custom_rs


def test_table_subtitle_font_size_reads_directly_from_theme():
    """subtitle.font.size propagates into Table SVG verbatim — no delta/floor math."""

    distinctive_size = 17.0
    custom_ctx, custom_rs = _custom_board_with_subtitle_size(distinctive_size)

    chart = _make_table_chart()
    data = [{"col": "A", "val": 1}]

    chart = resolve(chart, data, chart_style_context=custom_ctx)
    svg = render_table_svg(
        chart,
        data,
        width=400,
        board_style=custom_rs,
    )

    assert f'font-size="{distinctive_size}' in svg, (
        f"Expected subtitle font-size={distinctive_size} verbatim in Table SVG"
    )


def test_table_subtitle_font_size_none_asserts():
    """Assert fires loudly when title.subtitle.font.size is None (cascade contract)."""
    import pytest

    from dbt_charts.core.diagnostics.chart_data import ChartDataError

    custom_ctx, custom_rs = _custom_board_with_subtitle_size(None)

    chart = _make_table_chart()
    data = [{"col": "A", "val": 1}]
    chart = resolve(chart, data, chart_style_context=custom_ctx)

    with pytest.raises(
        ChartDataError, match="theme must supply title.subtitle.font.size"
    ):
        render_table_svg(
            chart,
            data,
            width=400,
            board_style=custom_rs,
        )
