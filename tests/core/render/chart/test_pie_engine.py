"""Pie renders in the Rust engine: routing and the paint contracts it keeps."""

from __future__ import annotations

import re
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.resolved import ResolvedPieChart
from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.normalize.charts import normalize_chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.text_truncation import collect_text_truncations
from dbt_charts.core.text.case import apply_case

from ..._engine_render import render_pie_svg

_ROWS = [{"cat": "A", "v": 30}, {"cat": "B", "v": 70}]
_ARC_RADIUS = re.compile(r"A([\d.]+),\1,")


def _resolved(chart_def: dict[str, Any], width: float = 400.0) -> ResolvedPieChart:
    flat = normalize_chart("p", chart_def, {"q": ValuesQuery(rows=_ROWS)}, sources={})
    _, context = resolve_style_and_context(get_theme_style())
    chart = resolve(flat, _ROWS, chart_style_context=context, width=width)
    assert isinstance(chart, ResolvedPieChart)
    return chart


def _wedges(svg: str) -> list[str]:
    return re.findall(
        r'<path d="([^"]+)" fill="[^"]+"[^>]*aria-roledescription="arc mark"', svg
    )


def _wedge_fills(svg: str) -> list[str]:
    return re.findall(
        r'<path d="[^"]+" fill="([^"]+)"[^>]*aria-roledescription="arc mark"', svg
    )


def test_donut_hole_is_inner_radius_of_the_wheel() -> None:
    """The hole radius is `inner_radius` of the outer radius, the same law
    WARN-PIE-TOTAL-EXCEEDS-INNER-RADIUS measures the hole with."""
    chart = _resolved(
        {"type": "donut", "query": "q", "theta": "v", "style": {"inner_radius": 0.6}}
    )
    wedges = _wedges(render_pie_svg(chart, _ROWS))
    assert len(wedges) == len(_ROWS)
    for wedge in wedges:
        radii = sorted({float(r) for r in _ARC_RADIUS.findall(wedge)}, reverse=True)
        outer, inner = radii[0], radii[1]
        assert inner / outer == pytest.approx(0.6, abs=1e-3), wedge


def test_solid_pie_wedges_reach_the_center() -> None:
    svg = render_pie_svg(_resolved({"type": "pie", "query": "q", "theta": "v"}), _ROWS)
    wedges = _wedges(svg)
    assert len(wedges) == 2
    assert all(w.endswith("L0,0Z") for w in wedges), wedges


def test_no_color_pie_fills_palette_zero_and_inks_labels_dark_companion() -> None:
    chart = _resolved({"type": "pie", "query": "q", "theta": "v"})
    svg = render_pie_svg(chart, _ROWS)
    assert set(_wedge_fills(svg)) == {chart.palette[0]}
    label_fills = re.findall(
        r'<text [^>]*fill="([^"]+)"[^>]*aria-roledescription="text mark"', svg
    )
    assert label_fills
    assert set(label_fills) == {chart.dark_companion_stops[0]}


def test_color_comes_from_resolved_channels_only() -> None:
    """With `color` authored but no resolved color channel, every wedge takes
    palette[0]: the engine never falls back to `chart.color`."""
    chart = _resolved({"type": "pie", "query": "q", "theta": "v", "color": "cat"})
    assert "color" in chart.resolved_channels
    colorless = chart.model_copy(update={"resolved_channels": {}})
    assert set(_wedge_fills(render_pie_svg(colorless, _ROWS))) == {chart.palette[0]}


def test_total_value_font_style_reaches_the_total_text() -> None:
    chart = _resolved(
        {
            "type": "donut",
            "query": "q",
            "theta": "v",
            "total": {"visible": True},
            "style": {"total": {"value": {"font": {"style": "italic"}}}},
        }
    )
    svg = render_pie_svg(chart, _ROWS)
    assert re.search(r'<text [^>]*font-style="italic"[^>]*>100</text>', svg), svg


def test_slice_label_font_style_reaches_the_label_text() -> None:
    chart = _resolved(
        {
            "type": "pie",
            "query": "q",
            "theta": "v",
            "style": {"marks": {"slice": {"labels": {"font": {"style": "italic"}}}}},
        }
    )
    svg = render_pie_svg(chart, _ROWS)
    labels = re.findall(r'<text [^>]*aria-roledescription="text mark"', svg)
    assert labels
    assert all('font-style="italic"' in label for label in labels), labels


def test_a_cut_title_records_a_chart_title_truncation() -> None:
    # The default wrap-two overflow cuts a third line.
    title = "A pie chart title far too long to fit inside this narrow card " * 3
    chart = _resolved(
        {
            "type": "pie",
            "query": "q",
            "theta": "v",
            "title": title,
        },
        width=200,
    )
    with collect_text_truncations() as truncations:
        render_pie_svg(chart, _ROWS, width=200)
    records = truncations["p"]
    assert [(r.surface, r.authored_field) for r in records] == [
        ("chart_title", "title")
    ]
    # Recorded as painted: with the title's case applied, as on the Vega path.
    assert records[0].authored_text == apply_case(title, chart.title_style.font.case)
