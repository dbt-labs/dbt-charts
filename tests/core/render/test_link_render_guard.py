"""Render-time link-scheme and CSS-color guards.

Compile-time validation (`compile/validate/links.py`) cannot see two cases:
a table cell/row link built from query row data (unknown until the query
runs), and a chart link's sentinel-stripped scheme (only visible after Vega
has already rendered it). Both are re-checked at render time by
`core.render.svg_utils.checked_href`, so an unsafe link becomes that chart's
inline error instead of a live `href` in the SVG.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render
from dbt_charts.core.render.converters.chart import _fix_chart_click_hrefs
from dbt_charts.core.render.errors import RenderError
from dbt_charts.core.render.render_result import RenderResult
from dbt_charts.core.render.svg_utils import css_color


def _render_yaml(board_yaml: str) -> RenderResult:
    result = compile_board(board_yaml)
    assert result.success and result.board is not None, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    return render(result.board, executor, format="svg")


def test_table_row_javascript_link_becomes_chart_error() -> None:
    """A table's chart-root link naming a column ("row_link" = bare column
    name) reads a query row's own value at render — data, not authoring,
    chooses the scheme. A malicious row value must not reach the SVG."""
    board = """
title: Test
queries:
  q1:
    type: values
    rows:
      - {name: "foo", row_link: "javascript:alert(1)"}
charts:
  c1:
    type: table
    query: q1
    link: row_link
rows:
  - c1
"""
    result = _render_yaml(board)

    assert result.chart_errors, "unsafe row link must surface as a chart error"
    assert result.chart_errors[0].code == "ERR-LINK-SCHEME-UNSAFE-AT-RENDER"
    output = result.output if isinstance(result.output, str) else ""
    # The chart's inline error block names the offending link as plain,
    # escaped text (informational) — the property under test is that it
    # never reaches a live href.
    assert 'href="javascript:alert(1)"' not in output


def test_hand_written_sentinel_link_becomes_chart_error() -> None:
    """An author who hand-writes the sentinel prefix
    (`link: "http://dct.invalidjavascript:alert(1)"`) bypasses both checks:
    it reads as scheme `http` at compile, and Vega's sanitizer never sees
    more than a domain-shaped string. The stripped value must be
    scheme-checked again, post-strip — as a per-chart error (chart_errors),
    not a whole-board failure: the layout-sizing pass's own render-first
    probe hits this same RenderError before the real render pass ever runs."""
    board = """
title: Test
queries:
  q1:
    type: values
    rows:
      - {month: "2024-01", revenue: 1000}
charts:
  c1:
    query: q1
    type: bar
    x: month
    y: revenue
    link: "http://dct.invalidjavascript:alert(1)"
rows:
  - c1
"""
    result = _render_yaml(board)

    assert result.board_error is None
    assert result.chart_errors, "unsafe sentinel link must surface as a chart error"
    assert result.chart_errors[0].code == "ERR-LINK-SCHEME-UNSAFE-AT-RENDER"
    output = result.output if isinstance(result.output, str) else ""
    assert 'href="javascript:alert(1)"' not in output


def test_non_sentinel_xlink_href_is_scheme_checked_too() -> None:
    """An absolute or template-first chart link (https://, ./, #...) never
    gets the http://dct.invalid sentinel, so it reaches vl_convert's output
    untouched by the sentinel-stripping branch — it must still be
    scheme-checked rather than passed through as-is."""
    svg_input = '<a xlink:href="javascript:alert(1)"><path d="M1,0"/></a>'
    with pytest.raises(RenderError) as exc_info:
        _fix_chart_click_hrefs(svg_input)
    assert exc_info.value.code.code == "ERR-LINK-SCHEME-UNSAFE-AT-RENDER"


def test_css_color_injection_raises() -> None:
    """A style="..." color slot must reject anything that isn't exactly one
    color — a value like "red; background-image:url(...)" needs no quote or
    angle bracket to open a new declaration, so escaping alone can't stop it.
    css_color never silently declines: it raises a RenderError, not the bare
    InvalidColorError."""
    with pytest.raises(RenderError) as exc_info:
        css_color("red; background-image:url(//evil)")
    assert exc_info.value.code.code == "ERR-CSS-COLOR-INVALID-AT-RENDER"


_BAD_COLOR = "red; background-image:url(//evil)"


def test_table_cell_link_color_invalid_becomes_chart_error() -> None:
    """An unsanitizable per-column `font.color` override is the one authored
    path that reaches `css_color` unsanitized — `colors["link"]` is always
    sanitize_color-gated first, so the row-link path can't trigger this."""
    board = f"""
title: T
queries:
  q:
    type: values
    rows:
      - {{name: "foo", link_col: "/x"}}
charts:
  t:
    query: q
    type: table
    style:
      font:
        color: "{_BAD_COLOR}"
      columns:
        link_col:
          link: "{{{{ link_col }}}}"
          font:
            color: "{_BAD_COLOR}"
rows:
  - t
"""
    result = _render_yaml(board)

    assert result.board_error is None
    assert result.chart_errors, "invalid cell-link color must surface as a chart error"
    assert result.chart_errors[0].code == "ERR-CSS-COLOR-INVALID-AT-RENDER"
    output = result.output if isinstance(result.output, str) else ""
    # The chart's inline error block names the offending color as plain,
    # escaped text (informational) — the property under test is that it
    # never reaches a live style="..." declaration.
    assert f'style="color: {_BAD_COLOR}"' not in output


def test_variables_border_color_invalid_becomes_board_diagnostic() -> None:
    """style.variables.border.color rides as a CSS custom property on the
    variables strip — board chrome, not chart-scoped. An invalid value must
    surface as a clean board_error diagnostic, never a rendered value or an
    uncaught traceback."""
    board = f"""
title: T
style:
  variables:
    border:
      color: "{_BAD_COLOR}"
variables:
  v1:
    input: text
    default: hi
queries:
  q:
    type: values
    rows:
      - {{n: 1}}
charts:
  t:
    query: q
    type: table
rows:
  - t
"""
    result = _render_yaml(board)

    assert result.output is None
    assert result.board_error is not None
    assert result.board_error.code == "ERR-CSS-COLOR-INVALID-AT-RENDER"
