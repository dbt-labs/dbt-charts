"""End-to-end: table pagination through the real board render seam.

Every existing pagination test calls render_table_svg(..., variables=...)
directly, which is exactly what hid the defect this file pins: production
never threads variables through render_svg_family, so a real render() always
painted page 1 regardless of the ``{chart_id}_page`` a host set. These tests
go through compile() -> render() -- the seam a live host (dct serve, Cloud)
actually uses -- so a regression in that plumbing fails here even though it
is invisible to a direct render_table_svg() call.
"""

from __future__ import annotations

import re
from unittest.mock import Mock

from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import render
from dbt_charts.core.render.controls import controls_stylesheet

_BOARD_YAML = """\
title: Probe
charts:
  t:
    query: q
    type: table
    style:
      preferred_width: 400
      pagination:
        enabled: true
        page_rows: 5
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - t
"""


def _make_executor(board: object, query_registry: object, rows: list[dict[str, str]]):
    ok = Mock()
    ok.is_success = True
    ok.data = rows
    ok.resolved_relations = None
    ok.truncated_reason = None
    mock_registry = Mock()
    mock_registry.execute.return_value = ok
    return Executor(
        board, adapter_registry=mock_registry, query_registry=query_registry
    )


def _find_active_page_text(svg: str) -> str:
    m = re.search(
        r'<text[^>]*data-pagination-current="t_page"[^>]*>([^<]*)</text>', svg
    )
    assert m, f"active-page text (data-pagination-current) not found:\n{svg}"
    return m.group(1)


def test_variables_page_reaches_the_table_renderer() -> None:
    """A board's ``{chart_id}_page`` variable must move which page a real
    render() paints, not just what a direct render_table_svg(variables=...)
    call does.
    """
    result = compile(_BOARD_YAML)
    assert result.success and result.board is not None, result.errors
    rows = [
        {"name": f"row_{i}"} for i in range(1, 13)
    ]  # 12 rows, page_rows=5 -> 3 pages
    executor = _make_executor(result.board, result.query_registry, rows)

    render_result = render(
        result.board, executor, format="svg", variables={"t_page": 2}, controls=True
    )
    assert render_result.board_error is None, render_result.board_error
    svg = render_result.output
    assert isinstance(svg, str)

    assert "row_6" in svg
    assert "row_10" in svg
    # Word boundary so row_1 doesn't false-match inside row_10/row_11/row_12.
    assert not re.search(r"\brow_1\b", svg)
    assert "row_5" not in svg
    assert _find_active_page_text(svg) == "2"


def test_paginator_glyph_uses_a_class_not_a_presentation_attribute() -> None:
    """A ``pointer-events="none"`` presentation attribute can never beat the
    host stylesheet's ``.dbt-chart text { pointer-events: auto }`` rule -- the
    fix must be a class plus a matching CSS rule, exactly like
    ``.dbt-table-cell-inert`` for cell text. Both rules ship with the host
    (interaction never lives inside a board), so the glyph in the SVG carries
    only the class.
    """
    result = compile(_BOARD_YAML)
    assert result.success and result.board is not None, result.errors
    rows = [{"name": f"row_{i}"} for i in range(1, 13)]
    executor = _make_executor(result.board, result.query_registry, rows)

    render_result = render(result.board, executor, format="svg", controls=True)
    assert render_result.board_error is None, render_result.board_error
    svg = render_result.output
    assert isinstance(svg, str)

    # Both rules must coexist in the same stylesheet -- that's what proves the
    # class rule's specificity, not just its presence, is what wins.
    css = controls_stylesheet()
    assert ".dbt-chart text {" in css
    assert "pointer-events: auto" in css

    glyph_tags = re.findall(r'<text [^>]*class="dbt-paginator-glyph"[^>]*>', svg)
    assert glyph_tags, "paginator glyph <text> must carry the dbt-paginator-glyph class"
    assert not any('pointer-events="none"' in tag for tag in glyph_tags), (
        "the inert presentation attribute must be dropped from the glyph "
        "<text> itself, not left alongside the class-based rule"
    )
    assert re.search(
        r"\.dbt-chart text\.dbt-paginator-glyph \{\s*"
        r"pointer-events: none;\s*cursor: pointer;\s*\}",
        css,
    ), "matching host rule must ship pointer-events: none + cursor: pointer"
    assert "dbt-paginator-glyph {" not in svg


def test_paginator_label_is_inert_to_selection() -> None:
    """The row-range label sits outside the strippable paginator group, so the
    host rule for the group's own glyphs does not reach it. It carries a class
    the host stylesheet targets instead -- interaction never ships inline in a
    board.
    """
    result = compile(_BOARD_YAML)
    assert result.success and result.board is not None, result.errors
    rows = [{"name": f"row_{i}"} for i in range(1, 106)]  # 21 pages: past the cap
    executor = _make_executor(result.board, result.query_registry, rows)

    live = render(result.board, executor, format="svg", controls=True).output
    static = render(result.board, executor, format="svg", controls=False).output
    assert isinstance(live, str) and isinstance(static, str)

    assert re.search(
        r'<text [^>]*class="dbt-paginator-label"[^>]*>1–5 / 105</text>', live
    )
    assert re.search(
        r'<text [^>]*class="dbt-paginator-label"[^>]*>1–5 / 105</text>', static
    )
    assert "user-select" not in live and "user-select" not in static
    assert re.search(
        r"\.dbt-chart \.dbt-paginator text,\s*"
        r"\.dbt-chart text\.dbt-paginator-label \{\s*user-select: none;\s*\}",
        controls_stylesheet(),
    )
