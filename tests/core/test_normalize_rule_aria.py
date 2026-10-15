"""A 100%-stacked chart's reference rules never announce ``NaN``."""

from __future__ import annotations

import re

import pytest

from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_DATA = [
    {"month": "Jan", "series": "A", "value": 3},
    {"month": "Jan", "series": "B", "value": 7},
    {"month": "Feb", "series": "A", "value": 5},
    {"month": "Feb", "series": "B", "value": 5},
]


def _svg(make_chart, **kwargs) -> str:
    pytest.importorskip("vl_convert")
    import vl_convert as vlc

    chart = make_chart(
        "bar", x="month", y="value", color="series", stack="normalize", **kwargs
    )
    return vlc.vegalite_to_svg(generate_vega_lite_spec(chart, _DATA, width=400))


def test_normalize_stack_rules_are_hidden(make_chart) -> None:
    svg = _svg(make_chart)

    assert "NaN" not in svg
    rule_groups = re.findall(r'<g [^>]*class="mark-rule role-mark[^>]*>', svg)
    assert rule_groups
    assert all('aria-hidden="true"' in group for group in rule_groups), rule_groups


def test_percent_normalize_stack_rules_are_hidden(make_chart) -> None:
    svg = _svg(make_chart, style={"axis_y": {"labels": {"format": "percent_whole"}}})

    assert "NaN" not in svg
    assert "rule mark" not in svg
    rule_groups = re.findall(r'<g [^>]*class="mark-rule role-mark[^>]*>', svg)
    assert rule_groups
    assert all('aria-hidden="true"' in group for group in rule_groups), rule_groups


def test_zero_and_threshold_rules_are_hidden(make_chart) -> None:
    pytest.importorskip("vl_convert")
    import vl_convert as vlc

    rows = [{"x": "a", "y": -2}, {"x": "b", "y": 1}, {"x": "c", "y": 3}]
    for chart_type in ("line", "bar", "area"):
        chart = make_chart(chart_type, x="x", y="y")
        svg = vlc.vegalite_to_svg(generate_vega_lite_spec(chart, rows, width=400))
        assert "rule mark" not in svg, chart_type

    points = [{"x": -3, "y": -2}, {"x": 1, "y": 1}, {"x": 4, "y": 3}]
    scatter = make_chart("scatter", x="x", y="y")
    svg = vlc.vegalite_to_svg(generate_vega_lite_spec(scatter, points, width=400))
    assert "rule mark" not in svg
