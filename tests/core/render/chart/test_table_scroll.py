"""A table too wide or too long for its slot scrolls inside it.

`column_layout.overflow: scroll` (the default) keeps compact columns at their
width and scrolls sideways; `fit` squeezes them as before. `freeze` pins
leading columns while the table scrolls, and `pagination.mode: scroll` swaps
the pager for a vertical scroll region under a pinned header.
"""

from __future__ import annotations

import re

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.render.render_result import RenderResult

from ..._svg_render import render_board_svg_result

_WIDE_COLUMNS = 16


def _board(style: str = "", rows: int = 3, columns: int = _WIDE_COLUMNS) -> str:
    keys = [f"account_metric_{i}" for i in range(columns)]
    body = "\n".join(
        "      - {"
        + ", ".join(f"{k}: {1000 * r + i}" for i, k in enumerate(keys))
        + "}"
        for r in range(rows)
    )
    chart_style = f"    style:\n{style}" if style else ""
    return (
        "queries:\n  q:\n    type: values\n    rows:\n" + body + "\n"
        "charts:\n  t:\n    query: q\n    type: table\n"
        + chart_style
        + "rows:\n  - t\n"
    )


def _render(yaml_text: str) -> tuple[str, RenderResult]:
    result = render_board_svg_result(yaml_text)
    assert isinstance(result.output, str)
    return result.output, result


def _codes(result: RenderResult) -> set[str]:
    return {w.code for w in result.warnings}


def _max(svg: str, axis: str) -> float:
    match = re.search(rf'data-max-{axis}="([^"]+)"', svg)
    assert match, "expected a scrolling table"
    return float(match.group(1))


def test_a_table_wider_than_its_slot_scrolls_instead_of_squeezing() -> None:
    svg, result = _render(_board())
    assert _max(svg, "x") > 0
    assert _max(svg, "y") == 0
    assert not {"WARN-TABLE-CRAMPED", "WARN-TABLE-COLUMNS-OVERFLOW"} & _codes(result)
    assert 'data-dbt-scroll-thumb="x"' in svg


def test_fit_squeezes_the_same_table() -> None:
    svg, result = _render(_board("      column_layout: {overflow: fit}\n"))
    assert "data-dbt-table-scroll" not in svg
    assert {"WARN-TABLE-CRAMPED", "WARN-TABLE-COLUMNS-OVERFLOW"} & _codes(result)


def test_a_table_that_fits_draws_the_same_under_either_mode() -> None:
    scroll, _ = _render(_board(columns=3))
    fit, _ = _render(_board("      column_layout: {overflow: fit}\n", columns=3))
    assert "data-dbt-table-scroll" not in scroll
    assert scroll == fit


def test_a_long_text_column_wraps_before_the_table_scrolls() -> None:
    text = "a long free text note " * 12
    yaml_text = (
        "queries:\n  q:\n    type: values\n    rows:\n"
        f"      - {{id: 1, amount: 12.5, note: '{text}'}}\n"
        "charts:\n  t: {query: q, type: table}\nrows:\n  - t\n"
    )
    svg, _ = _render(yaml_text)
    assert "data-dbt-table-scroll" not in svg
    assert re.search(r"<text[^>]*data-col=\"2\"[^>]*><tspan", svg), "note wraps"


def test_freeze_pins_the_leading_columns_in_their_own_panes() -> None:
    svg, _ = _render(_board("      column_layout: {freeze: 1}\n"))
    assert "-corner-" in svg
    frozen = re.search(
        r'clip-path="url\(#table-scroll-t-frozen-[^"]*\)">(.*?)</g></g>', svg
    )
    assert frozen, "expected a frozen rows pane"
    assert set(re.findall(r'data-col="(\d+)"', frozen.group(1))) == {"0"}
    assert 'data-dbt-scroll-shadow="x"' in svg


def test_scroll_mode_shows_page_rows_under_a_pinned_header() -> None:
    svg, _ = _render(
        _board("      pagination: {mode: scroll, page_rows: 10}\n", rows=40, columns=3)
    )
    assert _max(svg, "y") > 0
    assert "dbt-paginator" not in svg
    assert len(re.findall(r'<text[^>]*data-col="0"', svg)) == 40
    assert svg.index('font-weight="600"') < svg.index('data-col="0"'), "header first"


def test_freeze_without_scroll_is_an_error() -> None:
    _, result = _render(_board("      column_layout: {overflow: fit, freeze: 1}\n"))
    assert "ERR-TABLE-FREEZE-INVALID" in str(result.chart_errors)
    assert "overflow: scroll" in str(result.chart_errors)


def test_scroll_mode_with_pagination_off_is_rejected() -> None:
    result = compile_board(_board("      pagination: {enabled: false, mode: scroll}\n"))
    assert not result.success
    assert "pagination.enabled: true" in str(result.errors)


def test_freezing_more_columns_than_drawn_is_an_error() -> None:
    _, result = _render(_board("      column_layout: {freeze: 4}\n", columns=3))
    assert "ERR-TABLE-FREEZE-INVALID" in str(result.chart_errors)
