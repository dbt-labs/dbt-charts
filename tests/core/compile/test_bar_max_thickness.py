"""`style.charts.bar.max_thickness`: a theme's ceiling on category bar thickness."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedBarChart
from dbt_charts.core.compile.models.style.theme.bar import BarMaxThicknessStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

ROWS = [{"cat": "a", "v": 1}, {"cat": "b", "v": 2}]


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


def _resolved(max_thickness: Any = None, theme_cap: Any = None) -> ResolvedBarChart:
    style: dict[str, Any] = {}
    if max_thickness is not None:
        style["max_thickness"] = max_thickness
    chart = BarChart.model_validate(
        {
            "id": "t",
            "type": "bar",
            "query_name": "q",
            "x": "cat",
            "y": "v",
            "style": style,
        }
    )
    theme = get_theme_style("clarity")
    if theme_cap is not None:
        bar = theme.charts.bar.model_copy(update={"max_thickness": theme_cap})
        charts = theme.charts.model_copy(update={"bar": bar})
        theme = theme.model_copy(update={"charts": charts})
    _, ctx = resolve_style_and_context(theme)
    out = resolve(
        chart,
        ROWS,
        chart_style_context=ctx,
        rendered_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert isinstance(out, ResolvedBarChart)
    return out


def test_no_shipped_theme_caps_category_bars() -> None:
    assert _resolved().style.max_thickness is None


def test_an_authored_cap_reaches_the_resolved_style() -> None:
    cap = _resolved({"horizontal": 40, "vertical": "30%"}).style.max_thickness
    assert cap is not None
    assert (cap.horizontal, cap.vertical) == (40, "30%")


@pytest.mark.parametrize(
    ("authored", "read"),
    [(64, 64.0), ("64", 64.0), ("64px", 64.0), ("12.5%", "12.5%"), (" 50 % ", "50%")],
)
def test_a_cap_is_pixels_or_a_percent_of_the_plot(authored: Any, read: Any) -> None:
    cap = _resolved({"horizontal": authored, "vertical": authored}).style.max_thickness
    assert cap is not None
    assert (cap.horizontal, cap.vertical) == (read, read)


def test_none_leaves_columns_uncapped() -> None:
    cap = _resolved({"horizontal": 40, "vertical": "none"}).style.max_thickness
    assert cap is not None
    assert cap.vertical == "none"


def test_none_over_a_theme_cap_keeps_the_theme_horizontal_value() -> None:
    theme_cap = BarMaxThicknessStyle(horizontal=40, vertical="30%")
    cap = _resolved({"vertical": "none"}, theme_cap=theme_cap).style.max_thickness
    assert cap is not None
    assert (cap.horizontal, cap.vertical) == (40, "none")


@pytest.mark.parametrize(
    ("cap", "needle"),
    [
        ({"horizontal": 40, "vertical": "wide"}, "'wide' is not a valid cap"),
        ({"horizontal": 40, "vertical": "third_of_plot_height"}, "is not a valid cap"),
        ({"horizontal": 0, "vertical": "none"}, "must be positive"),
        ({"horizontal": -4, "vertical": "none"}, "must be positive"),
        ({"horizontal": "0%", "vertical": "none"}, "must be positive"),
        ({"horizontal": "-5%", "vertical": "none"}, "must be positive"),
        ({"horizontal": "none", "vertical": "none"}, "'none' is not a valid cap"),
        ({"horizontal": "x%", "vertical": "none"}, "'x%' is not a valid cap"),
        ({"horizontal": ["40"], "vertical": "none"}, "is not a valid cap"),
        ({"vertical": "none"}, "max_thickness.horizontal\n  Field required"),
        ({"horizontal": 40}, "max_thickness.vertical\n  Field required"),
        (
            {"horizontal": 40, "vertical": "none", "rows": 10},
            "Extra inputs are not permitted",
        ),
    ],
)
def test_a_malformed_cap_is_rejected(cap: dict[str, Any], needle: str) -> None:
    with pytest.raises(ValidationError, match=needle):
        _resolved(cap)
