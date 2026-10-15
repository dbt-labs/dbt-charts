"""KPI headline compaction is a theme value, thumbnail tuning is rendering config."""

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import (
    get_rendering_config,
    get_theme_style,
    load_config,
    reset_config,
)
from dbt_charts.core.compile.format import finalize_kpi_value_format
from dbt_charts.core.compile.models.style.theme.kpi import KpiCompactStyle
from dbt_charts.core.render.thumbnail import cartoonize

from .._svg_render import render_board_to_svg


def test_base_theme_carries_the_compact_policy() -> None:
    compact = get_theme_style().charts.kpi.value.compact
    assert (compact.spec, compact.threshold) == (".2~s", 1000.0)


def test_compact_threshold_comes_from_the_style_passed_in() -> None:
    compact = KpiCompactStyle(spec=".1~s", threshold=100.0)
    parts = finalize_kpi_value_format(None, 500.0, compact=compact)
    assert parts is not None and parts.spec == ".1~s"
    assert finalize_kpi_value_format(None, 50.0, compact=compact) is None


def test_thumbnail_tuning_lives_under_rendering() -> None:
    assert get_rendering_config().thumbnail.placeholder_ink == 0.20


_KPI_BOARD = """
title: T
queries:
  q1:
    type: values
    rows:
      - {{revenue: 3000000}}
charts:
  c1:
    query: q1
    type: kpi
    value: revenue
    label: Revenue
{chart_style}
rows:
  - c1
{board_style}
"""


@pytest.mark.parametrize(
    ("chart_style", "board_style"),
    [
        pytest.param(
            "",
            "style:\n  charts:\n    kpi:\n      value:\n        compact:\n"
            '          spec: ",.0f"\n          threshold: 10\n',
            id="board",
        ),
        pytest.param(
            '    style:\n      value:\n        compact:\n          spec: ",.0f"\n'
            "          threshold: 10\n",
            "",
            id="chart",
        ),
    ],
)
def test_authored_compact_spec_changes_the_printed_headline(
    chart_style: str, board_style: str
) -> None:
    svg = render_board_to_svg(
        _KPI_BOARD.format(chart_style=chart_style, board_style=board_style)
    )
    assert "3,000,000" in svg


def test_authored_compact_threshold_above_the_value_keeps_exact_digits() -> None:
    board_style = (
        "style:\n  charts:\n    kpi:\n      value:\n        compact:\n"
        '          spec: ".2~s"\n          threshold: 5000000\n'
    )
    svg = render_board_to_svg(
        _KPI_BOARD.format(chart_style="", board_style=board_style)
    )
    assert "3,000,000" in svg
    assert "3,000,000" not in render_board_to_svg(
        _KPI_BOARD.format(chart_style="", board_style="")
    )


def test_project_thumbnail_tuning_reaches_the_placeholder(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40" '
        'viewBox="0 0 100 40" data-dbt-page-background="#FFFFFF">'
        '<text x="5" y="20" font-size="11" font-family="Inter, sans-serif">Hi</text>'
        "</svg>"
    )
    reset_config()
    default_fill = re.search(r'fill="(#[0-9a-f]+)"', cartoonize(svg))
    (tmp_path / "dbt_charts.yml").write_text(
        "rendering:\n  thumbnail:\n    placeholder_ink: 0.8\n"
    )
    load_config(local_project(tmp_path))
    try:
        tuned_fill = re.search(r'fill="(#[0-9a-f]+)"', cartoonize(svg))
    finally:
        reset_config()
    assert default_fill and tuned_fill
    assert tuned_fill.group(1) != default_fill.group(1)
