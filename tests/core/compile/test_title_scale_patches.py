"""Regression tests for TitleStyle migration to bare *StylePatch.

Verifies:
- ChartStylePatch accepts TitleStylePatch
- TitleStyle.overflow normalizes snake_case and rejects invalid values
- Chart-local title override propagates through cascade to resolved_style.title
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.style.authored import (
    BarChartStylePatch,
    TitleStylePatch,
)


def test_chart_style_patch_accepts_compiled_title_style_patch():
    # font is FontStyle in the generated patch model; dict is coerced by Pydantic
    patch = BarChartStylePatch(title=TitleStylePatch(font={"color": "#c0ffee"}))
    assert patch.title is not None
    assert patch.title.font.color == "#c0ffee"


def test_compiled_title_style_overflow_rejects_snake_case():
    """TitleStyle.overflow does not rewrite wrap_two to wrap-two."""
    import pytest
    from pydantic import ValidationError

    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.theme import TitleStyle

    base = get_theme_style().title.model_dump()
    base["overflow"] = "wrap_two"
    with pytest.raises(ValidationError, match="overflow"):
        TitleStyle.model_validate(base)


def test_compiled_title_style_overflow_rejects_invalid():
    """TitleStyle.overflow raises ValidationError on unknown values (enforced by the Literal type)."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.theme import TitleStyle

    base = get_theme_style().title.model_dump()
    base["overflow"] = "trunc"
    with pytest.raises(ValidationError, match="Input should be"):
        TitleStyle.model_validate(base)


def test_chart_local_title_font_color_propagates_through_cascade():
    """chart.style.bar.title.font.color overrides board title via merge_onto_base cascade for cartesian charts."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.compile.resolve.style.chart_context import (
        build_chart_style_context as _build_resolved_style,
    )

    board = resolve_chart_style_context(get_theme_style())
    patch = BarChartStylePatch(title=TitleStylePatch(font=FontStyle(color="#deadbe")))
    effective = _build_resolved_style(board, BarChart(id="t", type="bar", style=patch))
    # Title from the primary family patch flows to effective.title (not effective.bar.title).
    assert effective.title.font.color == "#deadbe"
