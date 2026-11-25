"""Regression tests for TitleStyle migration to bare *StylePatch.

Verifies:
- ChartStylePatch accepts ChartTitleStylePatch
- TitleStyle.max_lines rejects anything but a line count or `all`
- Chart-local title override propagates through cascade to resolved_style.title
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.style.authored import (
    BarChartStylePatch,
    ChartTitleStylePatch,
)


def test_chart_style_patch_accepts_compiled_title_style_patch():
    # font is FontStyle in the generated patch model; dict is coerced by Pydantic
    patch = BarChartStylePatch(title=ChartTitleStylePatch(font={"color": "#c0ffee"}))
    assert patch.title is not None
    assert patch.title.font.color == "#c0ffee"


@pytest.mark.parametrize("junk", ["wrap-two", "truncate", "wrap_two", 0, True])
def test_compiled_title_style_wrap_rejects_junk(junk):
    """TitleStyle.max_lines is a count of at least 1 or `all`, nothing else."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.theme import TitleStyle

    base = get_theme_style().title.model_dump()
    base["max_lines"] = junk
    with pytest.raises(ValidationError, match="max_lines"):
        TitleStyle.model_validate(base)


def test_chart_local_title_font_color_propagates_through_cascade():
    """chart.style.bar.title.font.color overrides board title via merge_onto_base cascade for cartesian charts."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.compile.resolve.style.chart_context import (
        build_chart_style_context as _build_resolved_style,
    )

    board = resolve_chart_style_context(get_theme_style())
    patch = BarChartStylePatch(title=ChartTitleStylePatch(font={"color": "#deadbe"}))
    effective = _build_resolved_style(board, BarChart(id="t", type="bar", style=patch))
    # Title from the primary family patch flows to effective.title (not effective.bar.title).
    assert effective.title.font.color == "#deadbe"
