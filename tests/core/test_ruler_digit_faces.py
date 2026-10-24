"""Value rulers paint in the face their orientation calls for.

Tabular figures exist so a column of values scans; a vertical value ruler's ticks
stack into a column, a horizontal one's do not (numeral system design, "Axis
orientation"). Category labels never take the numeral face.
"""

from __future__ import annotations

import re

import pytest

from dbt_charts.core.compile.config import get_theme_style, user_facing_theme_names
from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
from dbt_charts.core.compile.resolve.style.board import (
    resolve_cascaded_font,
    resolve_chart_style_context,
)

from ._svg_render import render_board_to_svg


def _tabular(theme: str, axis_name: str, channel: str, chart_type: str) -> bool:
    ctx = resolve_chart_style_context(get_theme_style(theme))
    axis = resolved_axis_style(
        ctx, axis_name, channel, chart_type=chart_type, labeled=False
    )
    return resolve_cascaded_font(axis.labels.font).tabular_figures


@pytest.mark.parametrize("theme", user_facing_theme_names())
@pytest.mark.parametrize(
    ("axis_name", "channel", "chart_type", "expected"),
    [
        ("axis_x", "quantitative", "bar", True),  # bar x is the category axis
        ("axis_x", "quantitative", "scatter", False),  # a horizontal ruler
        ("axis_y", "quantitative", "bar", True),  # vertical bar value ruler
        ("axis_y", "quantitative", "scatter", True),
        ("axis_y", "ordinal", "bar", False),  # horizontal bar category labels
        ("axis_x", "ordinal", "bar", False),
    ],
)
def test_ruler_face_by_orientation(
    theme: str, axis_name: str, channel: str, chart_type: str, expected: bool
) -> None:
    assert _tabular(theme, axis_name, channel, chart_type) is expected


def _tick_label_families(
    orientation: str,
    chart_style: str = "",
    *,
    x: str = "name",
    board_style: str = "",
) -> dict[str, str]:
    yaml = f"""
title: T
{board_style}
queries:
  q1:
    type: values
    rows:
      - {{name: Alpha, num: 10, revenue: 1000}}
      - {{name: Beta, num: 20, revenue: 1500}}
charts:
  c1:
    query: q1
    type: bar
    x: {x}
    y: revenue
    style:
      orientation: {orientation}
{chart_style}
rows:
  - c1
"""
    svg = render_board_to_svg(yaml)
    families = re.findall(r'font-family="([^"]*)"[^>]*>([^<]*)</text>', svg)
    return {text: family for family, text in families}


def test_horizontal_bar_value_ruler_is_proportional() -> None:
    labels = _tick_label_families("horizontal")
    assert labels["500"] == labels["Alpha"]


def test_vertical_bar_value_ruler_stays_tabular() -> None:
    labels = _tick_label_families("vertical")
    assert "Tabular" in labels["500"]


def test_horizontal_bar_numeric_category_stays_tabular() -> None:
    """Numeric categories stack in a column down the side."""
    labels = _tick_label_families("horizontal", x="num")
    assert "Tabular" in labels["10"]
    assert "Tabular" not in labels["1,000"]


def test_horizontal_bar_authored_ruler_font_beats_the_orientation_rule() -> None:
    labels = _tick_label_families(
        "horizontal",
        "      axis_y:\n        labels:\n          font:\n            family: dbt Sans Tabular\n",
    )
    assert "Tabular" in labels["500"]


def test_board_level_quantitative_font_beats_the_orientation_rule() -> None:
    labels = _tick_label_families(
        "horizontal",
        board_style="style:\n  charts:\n    axis_quantitative:\n      labels:\n        font:\n          family: Georgia\n",
    )
    assert labels["500"].startswith("Georgia") or "Georgia" in labels["500"]
