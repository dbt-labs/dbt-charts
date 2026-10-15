"""Regression tests: chart aria-labels must not list the same field twice.

Root cause (PR #2308 follow-up): the color/theta channels had no ``title`` set,
so vl-convert emitted raw ``field: value`` entries from the channel AND a titled
``Title: value`` entry from the explicit ``encoding.tooltip`` array.  Different
keys → vl-convert can't dedupe → both appeared in the aria-label → JS hover
layer rendered "Kind / Kind", "Status / Status", "User Count / User Count".

Fix: channels carry their own titles (and format strings for quantitative fields);
the redundant ``encoding.tooltip`` array is removed.

Tests assert on the **rendered SVG aria-label**, not the spec dict.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.authored import ChartTotal
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    HeatmapChart,
    PieChart,
)
from dbt_charts.core.compile.models.style.authored import PieChartStylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

from ..._engine_render import render_pie_svg

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


def _svg_aria_labels(spec: dict[str, Any]) -> list[str]:
    import vl_convert as vlc

    svg = vlc.vegalite_to_svg(spec)
    return re.findall(r'aria-label="([^"]+)"', svg)


def _has_case_insensitive_duplicate(label: str) -> bool:
    """Return True if any field key appears twice (case-insensitively) in the label."""
    keys = [part.split(": ", 1)[0] for part in label.split("; ") if ": " in part]
    return len(keys) != len({k.lower() for k in keys})


_STACKED_DATA = [
    {"day_name": "Mon", "count": 5.5, "kind": "task"},
    {"day_name": "Tue", "count": 3.3, "kind": "pr"},
    {"day_name": "Wed", "count": 2.7, "kind": "task"},
]

_TICKET_DATA = [
    {"priority": "low", "ticket_count": 20.5, "status": "new"},
    {"priority": "high", "ticket_count": 5.5, "status": "closed"},
    {"priority": "low", "ticket_count": 8.3, "status": "closed"},
]

_PIE_DATA = [
    {"plan": "starter_monthly", "user_count": 8.5},
    {"plan": "pro_monthly", "user_count": 3.3},
]


def test_vertical_stacked_bar_no_duplicate_color_in_aria_label():
    """Vertical stacked bar: color field must not appear twice in any aria-label.

    Before fix: aria-label contained ``kind: task; Kind: task`` (raw channel
    entry + titled tooltip-array entry).  After fix: ``Kind: task`` once.
    """
    chart = BarChart(id="t", type="bar", x="day_name", y="count", color="kind")
    _rc = resolve(chart, _STACKED_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _STACKED_DATA, width=400)
    labels = _svg_aria_labels(spec)
    assert labels, "Expected at least one aria-label in rendered SVG"
    duped = [lb for lb in labels if _has_case_insensitive_duplicate(lb)]
    assert not duped, (
        f"Duplicate field keys found in aria-labels: {duped!r}\n"
        "Expected each field to appear exactly once (e.g. 'Kind: task', not 'kind: task; Kind: task')"
    )


def test_vertical_stacked_bar_measure_formatted_in_aria_label():
    """Vertical stacked bar: measure (count) must be formatted (5.5 not raw)."""
    chart = BarChart(id="t", type="bar", x="day_name", y="count", color="kind")
    _rc = resolve(chart, _STACKED_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _STACKED_DATA, width=400)
    labels = _svg_aria_labels(spec)
    formatted = [
        lb
        for lb in labels
        if "count: 5.5" in lb or "count: 3.3" in lb or "count: 2.7" in lb
    ]
    assert formatted, (
        f"Expected at least one aria-label with formatted count (e.g. 'count: 5.5').\n"
        f"Actual aria-labels: {labels!r}"
    )


def test_horizontal_stacked_bar_no_duplicate_color_in_aria_label():
    """Horizontal stacked bar: color field (status) must not appear twice.

    Before fix: ``status: new; Status: new``.  After fix: ``Status: new`` once.
    """
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    chart = BarChart(
        id="t",
        type="bar",
        x="priority",
        y="ticket_count",
        color="status",
        style=BarChartStylePatch.model_validate({"orientation": "horizontal"}),
    )
    _rc = resolve(chart, _TICKET_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _TICKET_DATA, width=400)
    labels = _svg_aria_labels(spec)
    assert labels, "Expected at least one aria-label in rendered SVG"
    duped = [lb for lb in labels if _has_case_insensitive_duplicate(lb)]
    assert not duped, (
        f"Duplicate field keys found in aria-labels: {duped!r}\n"
        "Expected 'Status: new' once, not 'status: new; Status: new'"
    )


def test_horizontal_stacked_bar_measure_formatted_in_aria_label():
    """Horizontal stacked bar: measure (ticket_count) must be formatted (20.5 not raw)."""
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    chart = BarChart(
        id="t",
        type="bar",
        x="priority",
        y="ticket_count",
        color="status",
        style=BarChartStylePatch.model_validate({"orientation": "horizontal"}),
    )
    _rc = resolve(chart, _TICKET_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _TICKET_DATA, width=400)
    labels = _svg_aria_labels(spec)
    formatted = [
        lb
        for lb in labels
        if "ticket count: 20.5" in lb
        or "ticket count: 5.5" in lb
        or "ticket count: 8.3" in lb
    ]
    assert formatted, (
        f"Expected at least one aria-label with formatted ticket_count (e.g. 'ticket count: 20.5').\n"
        f"Actual aria-labels: {labels!r}"
    )


def test_pie_with_total_no_duplicate_theta_in_aria_label():
    """Pie with total: theta field (user_count) must not appear twice.

    Before fix: ``user_count: 8.5; User Count: 8.5`` (raw theta channel +
    titled arc_tooltip entry).  After fix: ``User Count: 8.5`` once.
    """
    chart = PieChart(
        id="t",
        type="pie",
        theta="user_count",
        color="plan",
        total=ChartTotal(),
        style=PieChartStylePatch.model_validate({"inner_radius": 0.6}),
    )
    labels = re.findall(r'aria-label="([^"]+)"', render_pie_svg(chart, _PIE_DATA))
    assert labels, "Expected at least one aria-label in rendered SVG"
    duped = [lb for lb in labels if _has_case_insensitive_duplicate(lb)]
    assert not duped, (
        f"Duplicate field keys found in aria-labels: {duped!r}\n"
        "Expected 'User Count: 8.5' once, not 'user_count: 8.5; User Count: 8.5'"
    )


def test_pie_with_total_measure_formatted_in_aria_label():
    """Pie with total: theta (user_count) must appear formatted (8.5 not raw)."""
    chart = PieChart(
        id="t",
        type="pie",
        theta="user_count",
        color="plan",
        total=ChartTotal(),
        style=PieChartStylePatch.model_validate({"inner_radius": 0.6}),
    )
    labels = re.findall(r'aria-label="([^"]+)"', render_pie_svg(chart, _PIE_DATA))
    formatted = [
        lb for lb in labels if "User Count: 8.5" in lb or "User Count: 3.3" in lb
    ]
    assert formatted, (
        f"Expected at least one aria-label with formatted user_count (e.g. 'User Count: 8.5').\n"
        f"Actual aria-labels: {labels!r}"
    )


_HEATMAP_DATA = [
    {"day": "Mon", "hour": "9", "visits": 1234.56},
    {"day": "Mon", "hour": "10", "visits": 42.0},
    {"day": "Tue", "hour": "9", "visits": 87.3},
]


def test_heatmap_no_duplicate_color_in_aria_label():
    """Heatmap: color field (visits) must not appear twice in any aria-label.

    Before fix: rect/heatmap emitted the same redundant ``encoding.tooltip`` array
    PR #2308 left behind elsewhere, so vl-convert produced ``visits: 1234.56;
    Visits: 1,234.56`` in the aria-label.  After fix: the channel carries title
    and format; the array is gone.
    """
    chart = HeatmapChart(id="t", type="heatmap", x="day", y="hour", color="visits")
    _rc = resolve(chart, _HEATMAP_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _HEATMAP_DATA, width=400)
    labels = _svg_aria_labels(spec)
    assert labels, "Expected at least one aria-label in rendered SVG"
    duped = [lb for lb in labels if _has_case_insensitive_duplicate(lb)]
    assert not duped, (
        f"Duplicate field keys found in heatmap aria-labels: {duped!r}\n"
        "Expected 'Visits: 1,234.56' once, not 'visits: 1234.56; Visits: 1,234.56'"
    )


_HISTOGRAM_DATA = [
    {"price": 10, "kind": "A"},
    {"price": 22, "kind": "B"},
    {"price": 15, "kind": "A"},
    {"price": 28, "kind": "B"},
]


def test_histogram_with_color_no_duplicate_in_aria_label():
    """Histogram with color: no field appears twice in any aria-label.

    Histogram previously emitted the same redundant ``encoding.tooltip`` array
    PR #2308 left behind, masking the format-drop bug via channel-wins dedupe.
    Migration: drop the array; channels carry titles already.
    """
    chart = BarChart(id="t", type="histogram", x="price", color="kind")
    _rc = resolve(chart, _HISTOGRAM_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _HISTOGRAM_DATA, width=400)
    labels = _svg_aria_labels(spec)
    assert labels, "Expected at least one aria-label in rendered SVG"
    duped = [lb for lb in labels if _has_case_insensitive_duplicate(lb)]
    assert not duped, (
        f"Duplicate field keys found in histogram aria-labels: {duped!r}\n"
        "Expected each field once (e.g. 'Price: 10 – 12; Count: 1; Kind: A')"
    )


def test_heatmap_color_measure_formatted_in_aria_label():
    """Heatmap: color (visits) measure must be formatted via tooltip.format."""
    chart = HeatmapChart(id="t", type="heatmap", x="day", y="hour", color="visits")
    _rc = resolve(chart, _HEATMAP_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _HEATMAP_DATA, width=400)
    labels = _svg_aria_labels(spec)
    formatted = [lb for lb in labels if "Visits: 1,234.56" in lb]
    assert formatted, (
        f"Expected at least one aria-label with formatted visits (e.g. 'Visits: 1,234.56').\n"
        f"Actual aria-labels: {labels!r}"
    )


def test_bar_no_color_aria_label_clean():
    """Counter-example: bar with no color must have clean (no-duplicate) aria-labels."""
    data = [
        {"day_name": "Mon", "count": 5},
        {"day_name": "Tue", "count": 3},
    ]
    chart = BarChart(id="t", type="bar", x="day_name", y="count")
    _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, data, width=400)
    labels = _svg_aria_labels(spec)
    assert labels, "Expected at least one aria-label in rendered SVG"
    duped = [lb for lb in labels if _has_case_insensitive_duplicate(lb)]
    assert not duped, f"Unexpected duplicate fields in no-color bar: {duped!r}"
