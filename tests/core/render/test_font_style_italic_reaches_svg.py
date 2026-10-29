"""An authored ``font.style: italic`` reaches the rendered SVG text."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

_CHART_LOCAL = """
queries:
  q1:
    type: values
    rows:
      - {month: Janvier, revenue: 100}
      - {month: Fevrier, revenue: 150}
charts:
  c1:
    query: q1
    type: line
    title: Slanted Headline
    x: month
    y: revenue
    style:
      title:
        font:
          family: "'Source Serif 4', Georgia, serif"
          style: italic
          size: 22
      axis:
        labels:
          font:
            style: italic
rows:
  - c1
"""

_BOARD_NAMED_FONTS = """
style:
  fonts:
    heading:
      style: italic
    label:
      style: italic
queries:
  q1:
    type: values
    rows:
      - {month: Janvier, revenue: 100}
      - {month: Fevrier, revenue: 150}
charts:
  c1:
    query: q1
    type: line
    title: Slanted Headline
    x: month
    y: revenue
rows:
  - c1
"""


def setup_function() -> None:
    reset_config()


def teardown_function() -> None:
    reset_config()


def _render_svg(yaml: str, local_project: Callable[..., FilesystemProject]) -> str:
    result = compile(yaml)
    assert result.success, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    return render(result.board, executor, format="svg").output


def _text_tag(svg: str, text: str) -> str:
    match = re.search(rf"<text\b[^>]*>(?:<[^>]+>)*{re.escape(text)}<", svg)
    assert match is not None, f"no <text> for {text!r}"
    return match.group(0)


def test_chart_local_italic_reaches_title_and_axis_labels(
    local_project: Callable[..., FilesystemProject],
) -> None:
    svg = _render_svg(_CHART_LOCAL, local_project)
    assert 'font-style="italic"' in _text_tag(svg, "Slanted Headline")
    assert 'font-style="italic"' in _text_tag(svg, "Janvier")


def test_board_named_fonts_italic_reaches_title_and_axis_labels(
    local_project: Callable[..., FilesystemProject],
) -> None:
    svg = _render_svg(_BOARD_NAMED_FONTS, local_project)
    assert 'font-style="italic"' in _text_tag(svg, "Slanted Headline")
    assert 'font-style="italic"' in _text_tag(svg, "Janvier")


_FAMILY_TEMPLATE = """
style:
  fonts:
    label:
      style: italic
queries:
  q1:
    type: values
    rows:
      - {{month: Janvier, region: Nord, revenue: 100}}
      - {{month: Fevrier, region: Sud, revenue: 150}}
charts:
  c1:
    query: q1
    type: {chart_type}
    x: {x}
    y: {y}
    color: {color}
    title: Slanted Headline
rows:
  - c1
"""


@pytest.mark.parametrize(
    ("chart_type", "x", "y", "color", "axis_label", "legend_label"),
    [
        ("line", "month", "revenue", "region", "Janvier", None),
        ("bar", "month", "revenue", "region", "Janvier", "Nord"),
        ("scatter", "revenue", "revenue", "region", "100", "Nord"),
        ("heatmap", "month", "region", "revenue", "Janvier", None),
    ],
)
def test_label_slot_italic_reaches_axis_and_legend_labels(
    chart_type: str,
    x: str,
    y: str,
    color: str,
    axis_label: str,
    legend_label: str | None,
    local_project: Callable[..., FilesystemProject],
) -> None:
    yaml = _FAMILY_TEMPLATE.format(chart_type=chart_type, x=x, y=y, color=color)
    svg = _render_svg(yaml, local_project)
    assert 'font-style="italic"' in _text_tag(svg, axis_label)
    if legend_label is not None:
        assert 'font-style="italic"' in _text_tag(svg, legend_label)


_SURFACE_TEMPLATE = """
queries:
  q1:
    type: values
    rows:
      - {{month: Janvier, region: Nord, revenue: 100}}
      - {{month: Fevrier, region: Sud, revenue: 150}}
charts:
  c1:
    query: q1
{chart}
    title: Slanted Headline
    subtitle: Leaning Subtitle
    style:
{style}
rows:
  - c1
"""


def _indent(block: str, spaces: int) -> str:
    return "\n".join(" " * spaces + line for line in block.strip("\n").splitlines())


_ITALIC_FONT = """
font:
  style: italic
"""

# A top legend hides its title unless asked.
_LEGEND_TITLE = """
visible: true
font:
  style: italic
"""


@pytest.mark.parametrize(
    ("chart", "style_path", "text"),
    [
        ("type: line\nx: month\ny: revenue", "title.subtitle", "Leaning Subtitle"),
        (
            "type: bar\nx: month\ny: revenue\nx_label: Month Axis",
            "axis.title",
            "Month Axis",
        ),
        ("type: bar\nx: month\ny: revenue\ncolor: region", "legend.title", "Region"),
        ("type: heatmap\nx: month\ny: region\ncolor: revenue", "legend.label", "100"),
        ("type: histogram\nx: revenue", "axis.labels", "100"),
        ("type: table", "title", "Slanted Headline"),
        ("type: table", "title.subtitle", "Leaning Subtitle"),
        ("type: spark_bar\nx: revenue\ny: month", "subtitle", "Leaning Subtitle"),
    ],
)
def test_authored_italic_reaches_the_surface(
    chart: str,
    style_path: str,
    text: str,
    local_project: Callable[..., FilesystemProject],
) -> None:
    style = _LEGEND_TITLE if style_path == "legend.title" else _ITALIC_FONT
    for key in reversed(style_path.split(".")):
        style = f"{key}:\n{_indent(style, 2)}\n"
    yaml = _SURFACE_TEMPLATE.format(chart=_indent(chart, 4), style=_indent(style, 6))
    svg = _render_svg(yaml, local_project)
    assert 'font-style="italic"' in _text_tag(svg, text)


_SPARK_BAR_HEADING = """
style:
  fonts:
    heading:
      style: italic
queries:
  q1:
    type: values
    rows:
      - {month: Janvier, revenue: 100}
      - {month: Fevrier, revenue: 150}
charts:
  c1:
    query: q1
    type: spark_bar
    x: revenue
    y: month
    title: Slanted Headline
rows:
  - c1
"""


def test_heading_italic_reaches_the_spark_bar_title(
    local_project: Callable[..., FilesystemProject],
) -> None:
    svg = _render_svg(_SPARK_BAR_HEADING, local_project)
    assert 'font-style="italic"' in _text_tag(svg, "Slanted Headline")
