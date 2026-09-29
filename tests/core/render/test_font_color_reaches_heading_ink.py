"""style.font.color colors headings and titles as well as body text.

title.font.color inherits font.color, so it follows an authored font.color
unless a layer sets title.font.color itself. That holds only while no user-facing
theme sets title.font.color.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.config import reset_config, user_facing_theme_names
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

_HEADING_FILL_RE = re.compile(r"\.md-[0-9a-f]{8}-heading\s*\{[^}]*fill:\s*([^;\s}]+)")
_CHART_TITLE_FILL_RE = re.compile(
    r'role-title-text.*?<text[^>]*\bfill="([^"]+)"', re.DOTALL
)


def setup_function() -> None:
    reset_config()


def teardown_function() -> None:
    reset_config()


def _render_svg(yaml_text: str, local_project: Callable[..., FilesystemProject]) -> str:
    result = compile_board(yaml_text)
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    output = render(result.board, executor, format="svg").output
    assert isinstance(output, str)
    return output


def _heading_fill(svg: str) -> str:
    match = _HEADING_FILL_RE.search(svg)
    assert match is not None, f"no .md-*-heading fill rule found in: {svg[:400]}"
    return match.group(1)


def _chart_title_fill(svg: str) -> str:
    match = _CHART_TITLE_FILL_RE.search(svg)
    assert match is not None, f"no chart title fill found in: {svg[:400]}"
    return match.group(1)


def test_board_level_font_color_reaches_text_row_heading(
    local_project: Callable[..., FilesystemProject],
) -> None:
    board_yaml = """\
style:
  font:
    color: "#a1a1a1"
rows:
  - text: |
      # A heading

      Body text.
"""
    svg = _render_svg(board_yaml, local_project)
    assert _heading_fill(svg) == "#a1a1a1"


def test_item_level_font_color_reaches_its_own_row_heading(
    local_project: Callable[..., FilesystemProject],
) -> None:
    board_yaml = """\
rows:
  - text: |
      # A heading

      Body text.
    style:
      font:
        color: "#c3c3c3"
"""
    svg = _render_svg(board_yaml, local_project)
    assert _heading_fill(svg) == "#c3c3c3"


def test_title_font_color_still_wins_over_font_color(
    local_project: Callable[..., FilesystemProject],
) -> None:
    board_yaml = """\
style:
  font:
    color: "#a1a1a1"
  title:
    font:
      color: "#b2b2b2"
rows:
  - text: |
      # A heading

      Body text.
"""
    svg = _render_svg(board_yaml, local_project)
    assert _heading_fill(svg) == "#b2b2b2"


def test_outer_title_font_color_still_colors_an_inner_scopes_heading(
    local_project: Callable[..., FilesystemProject],
) -> None:
    board_yaml = """\
style:
  title:
    font:
      color: "#ff0000"
rows:
  - style:
      font:
        color: "#0000ff"
    text: |
      # A heading

      Body text.
"""
    svg = _render_svg(board_yaml, local_project)
    assert _heading_fill(svg) == "#ff0000"


@pytest.mark.parametrize("theme_name", user_facing_theme_names())
def test_authored_font_color_reaches_heading_under_every_built_in_theme(
    theme_name: str,
    local_project: Callable[..., FilesystemProject],
) -> None:
    board_yaml = f"""\
theme: {theme_name}
style:
  font:
    color: "#a1a1a1"
rows:
  - text: |
      # A heading

      Body text.
"""
    svg = _render_svg(board_yaml, local_project)
    assert _heading_fill(svg) == "#a1a1a1"


def test_authored_font_color_reaches_chart_title(
    local_project: Callable[..., FilesystemProject],
) -> None:
    board_yaml = """\
style:
  font:
    color: "#a1a1a1"
queries:
  q:
    columns: [category, revenue]
    values:
      - [A, 10]
      - [B, 20]
charts:
  c:
    query: q
    type: bar
    x: category
    y: revenue
    title: Revenue by category
rows:
  - c
"""
    svg = _render_svg(board_yaml, local_project)
    assert _chart_title_fill(svg) == "#a1a1a1"
