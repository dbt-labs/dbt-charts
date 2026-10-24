"""The engine draws an annotated cartesian chart."""

from __future__ import annotations

import datetime

import pytest

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.normalize.charts import normalize_chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.session import BoardRenderSession
from dbt_charts.core.render.chart.vega_lite import render_chart

_ROWS = [{"month": f"2024-{m:02d}-01", "revenue": float(m)} for m in range(1, 7)]
_POINTS = [{"month": float(m), "revenue": float(m)} for m in range(1, 7)]
_NOTE = "Quarterly goal"


_RENDERED_AT = datetime.datetime(2024, 6, 14, tzinfo=datetime.UTC)


def _chart(family: str) -> Chart:
    spec = {
        "type": family,
        "query": "q",
        "x": "month",
        "y": "revenue",
        "annotations": [{"type": "rule", "y": 3, "text": _NOTE}],
    }
    rows = _POINTS if family == "scatter" else _ROWS
    return normalize_chart("c1", spec, {"q": ValuesQuery(rows=rows)}, sources={})


def _render(family: str) -> str | None:
    rows = _POINTS if family == "scatter" else _ROWS
    style, ctx = resolve_style_and_context(get_theme_style(get_default_theme_name()))
    resolved = resolve(
        _chart(family),
        rows,
        chart_style_context=ctx,
        rendered_at=_RENDERED_AT,
    )
    return BoardRenderSession.create(style).render_svg_family(
        resolved, rows, width=600.0, height=300.0
    )


@pytest.mark.parametrize("family", ["line", "area", "bar", "scatter"])
def test_the_engine_draws_annotations(family: str) -> None:
    svg = _render(family)
    assert svg is not None
    assert _NOTE in svg


def test_render_chart_draws_annotations() -> None:
    style, ctx = resolve_style_and_context(get_theme_style(get_default_theme_name()))
    svg = render_chart(
        _chart("line"), style, ctx, _ROWS, format="svg", clock=lambda: _RENDERED_AT
    )
    assert _NOTE in svg


_SEGMENT_ROWS = [
    {"month": f"2024-{m:02d}-01", "revenue": float(m + i), "segment": seg}
    for m in range(1, 7)
    for i, seg in enumerate(["SMB", "Enterprise"])
]


@pytest.mark.parametrize("overlay", [None, True])
def test_trend_text_on_a_colored_line_renders_by_default_and_overlaid(
    overlay: bool | None,
) -> None:
    trend: dict[str, object] = {
        "type": "trend",
        "fit": "linear()",
        "series": "each",
        "text": "{{ series }} trend",
    }
    if overlay:
        trend["style"] = {"marks": {"text": {"position": {"overlay": True}}}}
    spec = {
        "type": "line",
        "query": "q",
        "x": "month",
        "y": "revenue",
        "color": "segment",
        "annotations": [trend],
    }
    style, ctx = resolve_style_and_context(get_theme_style(get_default_theme_name()))
    resolved = resolve(
        normalize_chart("c1", spec, {"q": ValuesQuery(rows=_SEGMENT_ROWS)}, sources={}),
        _SEGMENT_ROWS,
        chart_style_context=ctx,
        rendered_at=datetime.datetime(2024, 6, 14, tzinfo=datetime.UTC),
    )
    svg = BoardRenderSession.create(style).render_svg_family(
        resolved, _SEGMENT_ROWS, width=600.0, height=300.0
    )
    assert svg is not None
    assert "SMB trend" in svg and "Enterprise trend" in svg


def test_a_trend_x_range_draws_the_trend_over_that_span() -> None:
    spec = {
        "type": "line",
        "query": "q",
        "x": "month",
        "y": "revenue",
        "annotations": [
            {
                "type": "trend",
                "fit": "linear()",
                "x": {"from": "2024-03-01", "to": "2024-06-01"},
                "text": "spanned",
            }
        ],
    }
    style, ctx = resolve_style_and_context(get_theme_style(get_default_theme_name()))
    resolved = resolve(
        normalize_chart("c1", spec, {"q": ValuesQuery(rows=_ROWS)}, sources={}),
        _ROWS,
        chart_style_context=ctx,
        rendered_at=datetime.datetime(2024, 6, 14, tzinfo=datetime.UTC),
    )
    svg = BoardRenderSession.create(style).render_svg_family(
        resolved, _ROWS, width=600.0, height=300.0
    )
    assert svg is not None and "spanned" in svg
