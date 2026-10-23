"""Regression tests: chart aria-labels must not list the same field twice.

Root cause (PR #2308 follow-up): the color/theta channels had no ``title`` set,
so vl-convert emitted raw ``field: value`` entries from the channel AND a titled
``Title: value`` entry from the explicit ``encoding.tooltip`` array.  Different
keys → vl-convert can't dedupe → both appeared in the aria-label → JS hover
layer rendered "Kind / Kind", "Status / Status", "User count / User count".

Fix: channels carry their own titles (and format strings for quantitative fields);
the redundant ``encoding.tooltip`` array is removed.

Tests assert on the **rendered SVG aria-label**, not the spec dict.
"""

from __future__ import annotations

import re

import pytest

from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.compile.models.chart.authored import ChartTotal
from dbt_charts.core.compile.models.chart.normalized import (
    PieChart,
)
from dbt_charts.core.compile.models.style.authored import PieChartStylePatch

from ..._engine_render import render_pie_svg


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


def _has_case_insensitive_duplicate(label: str) -> bool:
    """Return True if any field key appears twice (case-insensitively) in the label."""
    keys = [part.split(": ", 1)[0] for part in label.split("; ") if ": " in part]
    return len(keys) != len({k.lower() for k in keys})


_PIE_DATA = [
    {"plan": "starter_monthly", "user_count": 8.5},
    {"plan": "pro_monthly", "user_count": 3.3},
]


def test_pie_with_total_no_duplicate_theta_in_aria_label():
    """Pie with total: theta field (user_count) must not appear twice.

    Before fix: ``user_count: 8.5; User count: 8.5`` (raw theta channel +
    titled arc_tooltip entry).  After fix: ``User count: 8.5`` once.
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
        "Expected 'User count: 8.5' once, not 'user_count: 8.5; User count: 8.5'"
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
        lb for lb in labels if "User count: 8.5" in lb or "User count: 3.3" in lb
    ]
    assert formatted, (
        f"Expected at least one aria-label with formatted user_count (e.g. 'User count: 8.5').\n"
        f"Actual aria-labels: {labels!r}"
    )
