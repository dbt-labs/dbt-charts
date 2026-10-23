"""Golden aria-label tests for the LUT-driven structured tooltip builder.

Walking-skeleton scope: line, multi-series line, bar (vertical + horizontal,
grouped + stacked), and pie/donut. Asserts on the **rendered SVG aria-label**,
following the same pattern as ``test_tooltip_dedup.py`` — the structured
builder's output is VL's native ``description`` channel, not an appended
``encoding.tooltip`` array (see ``emitters/_tooltip.py`` for why).

Header/series rows are role-tagged with zero-width Unicode markers (see
``emitters/_tooltip.py``'s ``ROLE_*`` constants) and carry a bare value with
no field-name prefix — tests assert on the marker + value, not on the old
``"Field: value"`` shape those rows used before this slice.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.authored import ChartTotal
from dbt_charts.core.compile.models.chart.normalized import (
    PieChart,
)
from dbt_charts.core.compile.models.style.authored import (
    PieChartStylePatch,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.emitters._tooltip import (
    MUTED,
    ROLE_HEADER_SWATCHED,
    ROLE_TOTAL,
)

from ..._engine_render import render_pie_svg

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


def _render_pie(chart: PieChart, data: list[dict[str, Any]]) -> list[str]:
    return re.findall(r'aria-label="([^"]+)"', render_pie_svg(chart, data))


_PIE_DATA = [
    {"plan": "starter_monthly", "user_count": 8.5},
    {"plan": "pro_monthly", "user_count": 3.3},
]


def test_pie_header_and_value_present():
    chart = PieChart(
        id="t",
        type="pie",
        theta="user_count",
        color="plan",
        total=ChartTotal(),
        style=PieChartStylePatch.model_validate({"inner_radius": 0.6}),
    )
    labels = _render_pie(chart, _PIE_DATA)
    assert any(f"{ROLE_HEADER_SWATCHED}starter_monthly" in lb for lb in labels), labels
    assert any("User count: 8.5" in lb for lb in labels), labels


def test_pie_header_before_value():
    chart = PieChart(
        id="t",
        type="pie",
        theta="user_count",
        color="plan",
        total=ChartTotal(),
        style=PieChartStylePatch.model_validate({"inner_radius": 0.6}),
    )
    labels = _render_pie(chart, _PIE_DATA)
    row = next(lb for lb in labels if f"{ROLE_HEADER_SWATCHED}starter_monthly" in lb)
    assert row.index(ROLE_HEADER_SWATCHED) < row.index("User count")


def test_pie_carries_percent_and_grand_total():
    """Pie/donut: % leads, raw beneath, grand total footer (always -- pie
    values are inherently one commensurable unit)."""
    chart = PieChart(id="t", type="pie", theta="user_count", color="plan")
    labels = _render_pie(chart, _PIE_DATA)
    row = next(lb for lb in labels if f"{ROLE_HEADER_SWATCHED}starter_monthly" in lb)
    assert "%: 72%" in row, row
    assert "User count: 8.5" in row, row
    assert f"{ROLE_TOTAL}Σ: 11.8" in row, row
    assert row.index("%: ") < row.index("User count") < row.index(f"{ROLE_TOTAL}Σ")


def test_pie_mutes_raw_count_and_total_but_not_the_share_lead():
    """On a pie the share % is the lead; the raw slice count and the grand
    total are context, so both carry the MUTED marker (rendered low-contrast)
    while the share % and swatched header do not."""
    chart = PieChart(id="t", type="pie", theta="user_count", color="plan")
    labels = _render_pie(chart, _PIE_DATA)
    row = next(lb for lb in labels if f"{ROLE_HEADER_SWATCHED}starter_monthly" in lb)
    # raw count and total are muted (MUTED prepended, composing with ROLE_TOTAL)
    assert f"{MUTED}User count: 8.5" in row, row
    assert f"{MUTED}{ROLE_TOTAL}Σ: 11.8" in row, row
    # the share % lead and the header are NOT muted
    assert f"{MUTED}%: " not in row, row
    assert f"{MUTED}{ROLE_HEADER_SWATCHED}" not in row, row


# --- Edge cases (final slice) ------------------------------------------------
